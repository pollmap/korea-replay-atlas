"""Isolated VPS property API and single-owner, bounded collection service.

The public Pages frontend and pinned data releases remain immutable. This API
serves verified snapshots and truthful acquisition status, not an automatic
public release. Raw XML, credentials and filesystem paths are never served.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .real_estate import RealEstateError, canonical_bytes, _reject_links
from .real_estate_archive import audit_checkpoint, checked_path
from .real_estate_fetch import Collector, KST, read_key
from .real_estate_history_audit import audit as history_audit
from .real_estate_local_archive_set import LocalArchiveSet, backup_set
from .real_estate_regions import load_registry
from .real_estate_storage import decode_snapshot
from .property_automation import requeue_safe_failures

RESERVE = 30 * 1024**3
MAX_RESPONSE = 1024**2
MAX_API_SNAPSHOT = 16 * 1024**2
ACCEPTED_STOPS = {'run_budget', 'work_complete', 'local_daily_budget'}


def instant():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def write_json(path, value):
    path = Path(path); _reject_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.next')
    _reject_links(temporary)
    temporary.write_bytes(canonical_bytes(value))
    os.replace(temporary, path)


def read_state(path):
    path = Path(path); _reject_links(path)
    if not path.exists():
        return {'state': 'not_started'}
    if path.stat().st_size > MAX_RESPONSE:
        raise RealEstateError('runtime_status_size')
    return json.loads(path.read_bytes())


def available_trades(db, *, day, budget=8000):
    """An exhausted rent source must not starve the separate sale allowance."""
    used = dict(db.execute('SELECT trade_type,COUNT(*) FROM calls WHERE day=? GROUP BY trade_type', (day,)))
    return [trade for trade in ('sale', 'rent') if used.get(trade, 0) < budget]


@contextmanager
def collector_lock(path):
    import fcntl  # Linux only; tests of acquisition logic remain cross-platform.
    _reject_links(path)
    with Path(path).open('a+b') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RealEstateError('vps_collector_already_running') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def backup(root, backups, *, progress=None):
    result = backup_set(root, LocalArchiveSet(backups, reserve_bytes=RESERVE), progress=progress)
    return {'status': 'verified', 'head': result['backup_set'], 'files': result['files'],
            'groups': result['groups'], 'audit': result['audit'], 'space': result['space']}


def collect_once(root, backups, secret_file, *, max_requests=500, progress=None):
    root = Path(root)
    use = shutil.disk_usage(root)
    if use.used / use.total >= .8 or use.free < RESERVE + 5 * 1024**3:
        raise RealEstateError('vps_storage_review_required')
    with closing(sqlite3.connect((root / 'checkpoint.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
        registry_hash = db.execute("SELECT value FROM meta WHERE key='registry_sha256'").fetchone()[0]
    registry = load_registry(root / 'registry' / (registry_hash + '.json'))
    collector = Collector(root, registry, months=241, advance_window=True,
                          scope='priority-nine', require_scope_complete=True, reserve_bytes=RESERVE)
    try:
        stamp = instant()
        trades = available_trades(collector.db, day=datetime.now(KST).date().isoformat())
        if not trades:
            return {'finished_at': stamp, 'collection': {'stop_reason': 'local_daily_budget', 'requests': 0},
                    'public_release': False}
        retries = requeue_safe_failures(collector.db, stamp, limit=5,
                                       first_acquisition_only=True, scope='priority-nine')
        report = collector.collect(read_key(secret_file), max_requests=max_requests,
                                   max_bytes=64 * 1024**2, daily_budget=8000, min_interval=.3,
                                   timeout=60, first_acquisition_only=True, collect_trades=trades, progress=progress)
    finally:
        collector.close()
    if progress:
        progress({'phase': 'backup', 'collection': report})
    recovery = backup(root, backups, progress=progress)
    return {'finished_at': instant(), 'collection': report, 'safe_retries': retries,
            'backup': recovery, 'public_release': False}


def worker(data, backups, secret_file, *, interval=300, max_requests=500):
    data = Path(data); root = data / 'collector'
    state = data / 'worker-status.json'
    if not (data / 'migration-verified.json').is_file():
        raise RealEstateError('verified_migration_required')
    migration = read_state(data / 'migration-verified.json')
    if (migration.get('status') != 'verified' or not isinstance(migration.get('audit'), dict)
            or type(migration.get('files')) is not int or migration['files'] <= 0):
        raise RealEstateError('verified_migration_required')
    with collector_lock(data / 'collector.lock'):
        while True:
            base = {'at': instant(), 'state': 'paused', 'public_release': False}
            if not (data / 'collection-enabled').is_file():
                write_json(state, base); time.sleep(30); continue
            # Source/auth/hash failures latch until an operator explicitly removes
            # this service's hold after diagnosis; no automatic credential retry.
            if (data / 'collection-hold.json').exists():
                write_json(state, {**base, 'state': 'held', 'hold': read_state(data / 'collection-hold.json')})
                time.sleep(30); continue
            try:
                write_json(state, {**base, 'state': 'collecting'})
                def progress(event):
                    write_json(state, {'at': instant(), 'state': 'running', 'phase': event.get('phase', 'backup'),
                                       'verified_files': event.get('verified_files'), 'requests': event.get('requests'),
                                       'response_bytes': event.get('response_bytes'), 'public_release': False})
                report = collect_once(root, backups, secret_file, max_requests=max_requests, progress=progress)
                write_json(data / 'last-run.json', report)
                stop = report['collection']['stop_reason']
                if stop not in ACCEPTED_STOPS:
                    raise RealEstateError(stop)
                write_json(state, {'at': instant(), 'state': 'waiting', 'stop_reason': stop,
                                   'last_success_at': report['finished_at'], 'requests': report['collection']['requests'],
                                   'public_release': False})
            except Exception as error:
                code = error.code if isinstance(error, RealEstateError) else 'vps_collection_failed'
                hold = {'at': instant(), 'error_code': code}
                write_json(data / 'collection-hold.json', hold)
                write_json(state, {**hold, 'state': 'held', 'public_release': False})
                print(json.dumps(hold), flush=True)  # No exception text, URLs or credentials.
            time.sleep(interval)


class PropertyAPI:
    def __init__(self, data):
        self.data = Path(data).absolute(); _reject_links(self.data)
        self.root = self.data / 'collector'
        self.lock = threading.Lock(); self.coverage = None; self.coverage_at = 0

    def connection(self):
        path = self.root / 'checkpoint.sqlite'; _reject_links(path)
        return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)

    def acquisition(self):
        with self.lock:
            if self.coverage is None or time.monotonic() - self.coverage_at > 60:
                self.coverage = history_audit(self.root / 'checkpoint.sqlite', as_of=datetime.now(KST).date().isoformat())
                self.coverage_at = time.monotonic()
        return {'service': 'korea-replay', 'at': instant(), 'acquisition': self.coverage,
                'worker': read_state(self.data / 'worker-status.json'),
                'publication': {'automatic': False, 'site': 'https://korea-replay.pages.dev/',
                                'acquired_is_not_published': True}}

    def transactions(self, query):
        allowed = {'regionCode', 'month', 'trade', 'limit', 'offset', 'snapshot'}
        if set(query) - allowed or any(len(v) != 1 for v in query.values()):
            raise RealEstateError('invalid_query')
        one = lambda key, fallback='': query.get(key, [fallback])[0]
        region, month, trade = one('regionCode'), one('month'), one('trade', 'sale')
        if (not re.fullmatch(r'[0-9]{5}', region) or not re.fullmatch(r'[0-9]{4}(?:0[1-9]|1[0-2])', month)
                or trade not in ('sale', 'rent') or not re.fullmatch(r'[0-9]{1,3}', one('limit', '50'))
                or not re.fullmatch(r'[0-9]{1,7}', one('offset', '0'))):
            raise RealEstateError('invalid_query')
        limit, offset = int(one('limit', '50')), int(one('offset', '0'))
        if not 1 <= limit <= 100 or offset > 1000000 or offset and not one('snapshot'):
            raise RealEstateError('invalid_query')
        with closing(self.connection()) as db:
            job = db.execute('SELECT status,snapshot,error_code FROM jobs WHERE id=?', (f'{trade}/{region}/{month}',)).fetchone()
        if job is None:
            return 404, {'status': 'not_planned', 'records': [], 'public_release': False}
        status, raw_ref, code = job
        if raw_ref is None:
            return 200, {'status': status, 'records': [], 'total': 0 if status == 'empty' else None,
                         'error_code': code, 'public_release': False}
        ref = json.loads(raw_ref)
        if one('snapshot') and one('snapshot') != ref['sha256']:
            return 409, {'status': 'snapshot_changed', 'records': [], 'public_release': False}
        path = self.root / checked_path(ref['path']); _reject_links(path)
        if ref.get('decoded_bytes', ref['bytes']) > MAX_API_SNAPSHOT or ref['bytes'] > MAX_API_SNAPSHOT:
            return 422, {'status': 'requires_partitioning', 'records': [], 'public_release': False}
        partition = json.loads(decode_snapshot(path.read_bytes(), ref))
        records = partition['records']; page = records[offset:offset + limit]
        return 200, {'status': status, 'retained_previous': status not in ('complete', 'empty'),
                     'snapshot': ref['sha256'], 'records': page, 'total': len(records),
                     'next_offset': offset + len(page) if offset + len(page) < len(records) else None,
                     'source': partition['source'], 'public_release': False}

    def dispatch(self, url):
        parts = urlsplit(url)
        if len(url) > 4096:
            return 400, {'error_code': 'invalid_query'}
        if parts.path == '/health' and not parts.query:
            with closing(self.connection()) as db:
                db.execute('SELECT 1 FROM meta LIMIT 1').fetchone()
            return 200, {'ok': True, 'service': 'korea-replay', 'build': os.environ.get('APP_BUILD', 'local'),
                         'data_ready': (self.data / 'migration-verified.json').is_file(),
                         'collector_state': read_state(self.data / 'worker-status.json').get('state', 'not_started')}
        if parts.path == '/api/v1/property/acquisition' and not parts.query:
            return 200, self.acquisition()
        if parts.path == '/api/v1/property/transactions':
            return self.transactions(parse_qs(parts.query, keep_blank_values=True, max_num_fields=12))
        return 404, {'error_code': 'not_found'}


def serve(data, port):
    api = PropertyAPI(data); slots = threading.BoundedSemaphore(2)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            if not slots.acquire(blocking=False):
                status, value = 503, {'error_code': 'busy'}
            else:
                try:
                    status, value = api.dispatch(self.path)
                except Exception as error:
                    status, value = (400 if isinstance(error, RealEstateError) and error.code == 'invalid_query' else 503), {'error_code': 'unavailable'}
                finally:
                    slots.release()
            payload = canonical_bytes(value)
            if len(payload) > MAX_RESPONSE:
                status, payload = 413, canonical_bytes({'error_code': 'response_size_limit'})
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            origin = self.headers.get('Origin', '')
            if re.fullmatch(r'https://(?:[a-f0-9]{8}\.)?korea-replay\.pages\.dev', origin):
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.end_headers(); self.wfile.write(payload)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('api', 'worker', 'backup'))
    parser.add_argument('--data', type=Path, default=Path('/data'))
    parser.add_argument('--backups', type=Path, default=Path('/backups'))
    parser.add_argument('--secret-file', type=Path, default=Path('/run/secrets/provider.json'))
    parser.add_argument('--port', type=int, default=8330)
    args = parser.parse_args()
    if args.mode == 'api':
        serve(args.data, args.port)
    elif args.mode == 'worker':
        worker(args.data, args.backups, args.secret_file)
    else:
        print(json.dumps(backup(args.data / 'collector', args.backups), ensure_ascii=False))


if __name__ == '__main__':
    main()
