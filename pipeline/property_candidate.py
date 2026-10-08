"""Build private audited candidates from a pinned closed read-model generation.

No source calls, collector writes, deployment or public-pointer changes.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import shutil

from .real_estate import RealEstateError, _reject_links
from .property_read_model import ReadModel, sha256
from .real_estate_local_archive import _lock
from .real_estate_regions import load_registry
from .real_estate_publish import publish, TARGET_ASSET
from .real_estate_complex_summary import build as summarize
from .real_estate_summary_month_pack import build as pack_summaries
from .vps_runtime import write_json
from .bulk_work import bulk_work
from .property_verification_cache import VerificationCache
from .real_estate_storage import MAX_SNAPSHOT_BYTES

RESERVE = 2 * 1024**3


def input_sizes(database):
    """Count only pinned publisher inputs, without opening XML/snapshot payloads.

    These are descriptor totals, not a bound on the compressed publication or
    a claim that its inputs have been audited. Every writer keeps its own stage
    preflight and per-file free-space guard.
    """
    references = 0; inputs = {}
    for status, value in database.execute("SELECT status,snapshot FROM jobs WHERE snapshot IS NOT NULL"):
        if status not in ('complete', 'empty', 'pending', 'partial', 'failed'):
            continue
        descriptor = json.loads(value)
        if not isinstance(descriptor, dict) or descriptor.get('encoding') not in (None, 'gzip', 'xz'):
            raise RealEstateError('candidate_input_descriptor')
        size = descriptor.get('bytes')
        decoded = descriptor.get('decoded_bytes') if descriptor.get('encoding') else size
        name = descriptor.get('path'); digest = descriptor.get('sha256')
        if (not isinstance(name, str) or not name or '..' in Path(name).parts
                or Path(name).is_absolute() or not isinstance(digest, str)
                or not re.fullmatch(r'[a-f0-9]{64}', digest)
                or type(size) is not int or not 0 < size <= MAX_SNAPSHOT_BYTES
                or type(decoded) is not int or not 0 < decoded <= MAX_SNAPSHOT_BYTES):
            raise RealEstateError('candidate_input_descriptor')
        identity = (digest, size, decoded)
        if name in inputs and inputs[name] != identity:
            raise RealEstateError('candidate_input_descriptor_conflict')
        inputs[name] = identity; references += 1
    return {'snapshot_references': references, 'snapshot_files': len(inputs),
            'snapshot_stored_bytes': sum(value[1] for value in inputs.values()),
            'snapshot_decoded_bytes': sum(value[2] for value in inputs.values()),
            'largest_snapshot_decoded_bytes': max((value[2] for value in inputs.values()), default=0),
            'payloads_read': 0, 'publication_upper_bytes': None,
            'enforcement': 'stage_preflight_and_per_file_reserve'}


def run(data, output, *, reserve_bytes=RESERVE, progress=None):
    if type(reserve_bytes) is not int or reserve_bytes < 0:
        raise RealEstateError('invalid_disk_reserve')
    data = Path(data).absolute(); output = Path(output).absolute()
    _reject_links(data); _reject_links(output)
    if any(p.lower() in ('public', 'dist') for p in output.parts):
        raise RealEstateError('public_output_forbidden')
    output.mkdir(parents=True, exist_ok=True)
    lock = output / 'candidate.lock'; _reject_links(lock)
    with _lock(lock), bulk_work(data):
        status_path = output / 'status.json'; _reject_links(status_path)
        state = json.loads(status_path.read_bytes()) if status_path.exists() else {}
        # Resume the exact input after interruption, never mix ledger generations.
        if state.get('state') in ('running', 'failed'):
            model = state['read_model']; generation = model['generation']
            if not re.fullmatch(r'[a-f0-9]{32}-[a-f0-9]{64}', generation):
                raise RealEstateError('candidate_generation')
            database = data / 'read-model' / (generation + '.sqlite')
        else:
            database, model = ReadModel(data).resolve()
        _reject_links(database)
        if database.stat().st_size != model['bytes'] or sha256(database) != model['sha256']:
            raise RealEstateError('candidate_checkpoint_hash')
        root = data / 'collector'
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            if db.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
                raise RealEstateError('candidate_checkpoint_not_closed')
            registry_hash = db.execute("SELECT value FROM meta WHERE key='registry_sha256'").fetchone()[0]
            inputs = input_sizes(db)
        if not re.fullmatch(r'[a-f0-9]{64}', registry_hash):
            raise RealEstateError('candidate_registry')
        registry = load_registry(root / 'registry' / (registry_hash + '.json'))
        state = {'schema_version':1, 'state':'running', 'read_model':model,
                 'public_release':False, 'source_calls':0, 'ledger_writes':0}

        def report(event):
            state.update(event)
            state['at'] = datetime.now(timezone.utc).isoformat()
            write_json(status_path, state)
            if progress:progress(dict(state))

        try:
            report({'phase':'space_preflight', 'input_sizes':inputs, 'reserve_bytes':reserve_bytes})
            if shutil.disk_usage(output).free < reserve_bytes + 2 * TARGET_ASSET:
                raise RealEstateError('disk_reserve')
            report({'phase':'raw_audit'})
            cache_path = output / 'verification.sqlite'
            # Keep the writable cache in this candidate workspace; never in a
            # source or immutable generation directory.
            if (cache_path.resolve().is_relative_to(root.resolve())
                    or cache_path.resolve().is_relative_to(database.parent.resolve())
                    or cache_path.exists() and (not cache_path.is_file() or cache_path.stat().st_nlink != 1)):
                raise RealEstateError('candidate_cache_path')
            with closing(VerificationCache(cache_path, root)) as cache:
                packed = publish(root, registry, output / 'transactions', reserve_bytes=reserve_bytes,
                                 checkpoint=database, progress=report, packed_transactions=True,
                                 compressed_transactions=True, audit_cache=cache)
                report({'verification_execution':{'cache_hits':cache.hits, 'cache_misses':cache.misses,
                                                   'version':cache.version}})
            report({'phase':'monthly_summary', 'property_release':packed['release_id']})
            summary = summarize(output / 'transactions' / packed['release_id'] / 'publication.json',
                                output / 'summaries', reserve_bytes=reserve_bytes, max_files=100_000, compressed_assets=True)
            report({'phase':'summary_pack'})
            compact = pack_summaries(output / 'summaries' / summary['summary_release_id'] / 'publication.json',
                                    output / 'summary-packs', reserve_bytes=reserve_bytes, compressed_assets=True)
            result = {'schema_version':1, 'state':'ready', 'public_release':False,
                      'read_model':model, 'property_release':packed['property_release'],
                      'property_publication':str(output / 'transactions' / packed['release_id'] / 'publication.json'),
                      'summary_publication':str(output / 'summary-packs' / compact['summary_release_id'] / 'publication.json'),
                      'audit':packed['audit'], 'summary_audit':compact['audit'],
                      'source_calls':0, 'ledger_writes':0}
            write_json(output / 'last-verified.json', result)
            report({'phase':'verified', 'state':'ready'})
            return result
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            report({'state':'failed', 'error_code':error.code if isinstance(error, RealEstateError) else 'candidate_failed'})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reserve-bytes', type=int, default=RESERVE,
                        help='Free recovery bytes retained by every stage/file guard (default: 2 GiB)')
    args = parser.parse_args()
    def log(value):
        print(json.dumps(value, ensure_ascii=False), flush=True)
    try:
        log(run(args.data, args.output, reserve_bytes=args.reserve_bytes, progress=log))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(1, 'property_candidate: ' + (error.code if isinstance(error, RealEstateError) else 'invalid_input') + '\n')


if __name__ == '__main__': main()
