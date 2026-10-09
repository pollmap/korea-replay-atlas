"""One resumable correction candidate, with explicit publication acknowledgement.

The collector and public release remain separate. A waiting candidate is reused;
new collector revisions cannot generate a pile of unpublished workspaces.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .property_candidate import run as build_candidate
from .property_read_model import ReadModel
from .real_estate import RealEstateError, _reject_links
from .real_estate_local_archive import _lock
from .vps_runtime import read_state, write_json


def input_identity(database):
    """Ignore changing call/lease timestamps, include every publication input and rule."""
    digest = hashlib.sha256()
    for name in ('real_estate.py', 'real_estate_publish.py', 'real_estate_storage.py',
                 'real_estate_working_store.py', 'property_verification_cache.py',
                 'real_estate_complex_summary.py', 'real_estate_summary_month_pack.py'):
        digest.update(name.encode()); digest.update(Path(__file__).with_name(name).read_bytes())
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        for row in db.execute("SELECT key,value FROM meta WHERE key IN ('registry_sha256','scope_policy') ORDER BY key"):
            digest.update(json.dumps(row, separators=(',', ':')).encode())
        for row in db.execute('SELECT id,status,pages,snapshot FROM jobs ORDER BY id'):
            digest.update(json.dumps(row, separators=(',', ':')).encode())
    return digest.hexdigest()


def reconcile(data, output, *, reserve_bytes=0, publisher=None, builder=build_candidate):
    data, output = Path(data).absolute(), Path(output).absolute()
    _reject_links(data); _reject_links(output)
    output.mkdir(parents=True, exist_ok=True)
    with _lock(output / 'queue.lock'):
        state_path = data / 'public-release-status.json'
        state = read_state(state_path)
        database, model = ReadModel(data).resolve()
        identity = input_identity(database)
        status = read_state(output / 'candidate' / 'status.json')
        ready_path = output / 'candidate' / 'last-verified.json'
        pending = state.get('state') in ('candidate_ready', 'publishing', 'publish_failed')
        if state.get('state') == 'published' and state.get('input_identity') == identity:
            return {**state, 'action': 'unchanged'}
        # Resume a failed/running generation before considering newer collector data.
        if not pending or not ready_path.is_file():
            pinned = status.get('read_model', {}).get('generation') if status.get('state') in ('running', 'failed') else model['generation']
            if not isinstance(pinned, str) or not re.fullmatch(r'[a-f0-9]{32}-[a-f0-9]{64}', pinned):
                raise RealEstateError('publication_candidate_identity')
            state = {'schema_version': 1, 'state': 'preparing', 'generation': pinned,
                     'at': now(), 'automatic': publisher is not None, 'public_release': False}
            write_json(state_path, state)
            result = builder(data, output / 'candidate', reserve_bytes=reserve_bytes)
            release = result['property_release']['release_id']
            generation = result['read_model']['generation']
            identity = input_identity(data / 'read-model' / (generation + '.sqlite'))
            if not re.fullmatch(r'property-[a-f0-9]{16}', release):
                raise RealEstateError('publication_candidate_identity')
            state = {'schema_version': 1, 'state': 'candidate_ready', 'at': now(),
                     'release_id': release, 'generation': generation, 'public_release': False,
                     'automatic': publisher is not None, 'candidate': str(ready_path),
                     'input_identity': identity,
                     'resumed_generation': status.get('read_model', {}).get('generation')}
            write_json(state_path, state)
        else:
            result = json.loads(ready_path.read_bytes())
            if (result['property_release']['release_id'] != state['release_id']
                    or result['read_model']['generation'] != state['generation']):
                raise RealEstateError('publication_candidate_identity')
        if not publisher:
            return {**state, 'action': 'awaiting_publisher'}
        # Credentials stay in the publisher's private environment, never command output.
        state = {**state, 'state': 'publishing', 'at': now()}; write_json(state_path, state)
        try:
            acknowledgement = publisher(ready_path, state)
            if (acknowledgement.get('verified') is not True
                    or acknowledgement.get('property_release') != state['release_id']
                    or not re.fullmatch(r'[a-f0-9]{64}', acknowledgement.get('artifact_sha256', ''))
                    or acknowledgement.get('public_origin') != 'https://korea-replay.pages.dev'):
                raise RealEstateError('publication_acknowledgement')
            state = {**state, 'state': 'published', 'public_release': True,
                     'automatic': True, 'published_at': now(), 'publication': acknowledgement}
            write_json(state_path, state)
            return {**state, 'action': 'published'}
        except Exception:
            write_json(state_path, {**state, 'state': 'publish_failed', 'at': now(),
                                  'public_release': False, 'error_code': 'publication_failed'})
            raise RealEstateError('publication_failed') from None


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reserve-bytes', type=int, default=0)
    args = parser.parse_args()
    try:
        result = reconcile(args.data, args.output, reserve_bytes=args.reserve_bytes)
        print(json.dumps({key: result.get(key) for key in ('state', 'action', 'release_id', 'generation', 'public_release')}))
    except (RealEstateError, OSError, ValueError, KeyError, sqlite3.Error):
        parser.exit(1, 'property_publication_queue: preparation_failed\n')


if __name__ == '__main__':
    main()
