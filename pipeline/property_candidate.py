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

from .real_estate import RealEstateError, _reject_links
from .property_read_model import ReadModel, sha256
from .real_estate_local_archive import _lock
from .real_estate_regions import load_registry
from .real_estate_publish import publish
from .real_estate_transaction_pack import build as pack_transactions
from .real_estate_complex_summary import build as summarize
from .real_estate_summary_month_pack import build as pack_summaries
from .vps_runtime import write_json

RESERVE = 30 * 1024**3


def run(data, output, *, reserve_bytes=RESERVE, progress=None):
    data = Path(data).absolute(); output = Path(output).absolute()
    _reject_links(data); _reject_links(output)
    if any(p.lower() in ('public', 'dist') for p in output.parts):
        raise RealEstateError('public_output_forbidden')
    output.mkdir(parents=True, exist_ok=True)
    lock = output / 'candidate.lock'; _reject_links(lock)
    with _lock(lock):
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
            report({'phase':'raw_audit'})
            raw = publish(root, registry, output / 'raw-candidates', reserve_bytes=reserve_bytes,
                          max_files=100_000, checkpoint=database, progress=report)
            report({'phase':'transaction_pack', 'raw_release':raw['release_id']})
            packed = pack_transactions(output / 'raw-candidates' / raw['release_id'] / 'publication.json',
                                       output / 'transactions', reserve_bytes=reserve_bytes)
            report({'phase':'monthly_summary', 'property_release':packed['release_id']})
            summary = summarize(output / 'transactions' / packed['release_id'] / 'publication.json',
                                output / 'summaries', reserve_bytes=reserve_bytes, max_files=100_000)
            report({'phase':'summary_pack'})
            compact = pack_summaries(output / 'summaries' / summary['summary_release_id'] / 'publication.json',
                                    output / 'summary-packs', reserve_bytes=reserve_bytes)
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
    args = parser.parse_args()
    def log(value):
        print(json.dumps(value, ensure_ascii=False), flush=True)
    try:
        log(run(args.data, args.output, progress=log))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(1, 'property_candidate: ' + (error.code if isinstance(error, RealEstateError) else 'invalid_input') + '\n')


if __name__ == '__main__': main()
