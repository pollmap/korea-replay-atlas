"""Budgeted official MOLIT collection with immutable raw data and durable checkpoints.

Without --execute this creates a bounded completed-months + current-month work ledger.
It never publishes into public/dist, retries automatically, or prints request URLs.
"""
from __future__ import annotations

import argparse
from functools import partial
from datetime import datetime, timezone, timedelta
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
from urllib.parse import urlencode, unquote, quote, quote_plus
import uuid

from .real_estate import (RealEstateError, canonical_bytes, sha256, normalize_xml_page,
                          build_partitions, utc_instant, _reject_links, MAX_PAGE_BYTES, validate_property_type)
from .real_estate_regions import load_registry
from .real_estate_priority import priority_map, POLICY_ID
from .real_estate_storage import encode_snapshot,decode_snapshot,MAX_SNAPSHOT_BYTES

KST = timezone(timedelta(hours=9))
ENDPOINTS = {
    'sale': '/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev',
    'rent': '/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent',
}
OFFICETEL_ENDPOINTS = {
    'sale': '/1613000/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade',
    'rent': '/1613000/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent',
}
RESERVE_BYTES = 30 * 1024**3


def instant():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def month_sequence(as_of, count=61):
    stamp = utc_instant(as_of).astimezone(KST)
    if type(count) is not int or not 1 <= count <= 121:
        raise RealEstateError('invalid_month_count')
    current = stamp.year * 12 + stamp.month - 1
    months = [f'{(current-i)//12:04d}{(current-i)%12+1:02d}' for i in range(count)]
    # National latest completed calendar month first; current month remains provisional.
    return [months[1], months[0], *months[2:]] if count > 1 else months


def assert_no_secret(raw, key):
    for secret in {key, quote(key, safe=''), quote_plus(key), json.dumps(key)[1:-1]}:
        if secret and secret.encode() in raw:
            raise RealEstateError('secret_reflection')
    if re.search(rb'(?:serviceKey|api[_-]?key|authorization)\s*(?:=|["\x27]\s*:)\s*["\x27]?[^<\s]{8}', raw, re.I):
        raise RealEstateError('credential_field_reflection')


def read_key(secret_file=None):
    value = os.environ.get('DATA_GO_KR_SERVICE_KEY')
    if not value and secret_file is not None:
        path = Path(secret_file).absolute(); _reject_links(path)
        if path.stat().st_size > 128 * 1024:
            raise RealEstateError('invalid_secret_file')
        value = json.loads(path.read_text(encoding='utf-8-sig')).get('DATA_GO_KR_SERVICE_KEY')
    if not isinstance(value, str) or not 10 <= len(value) <= 1024:
        raise RealEstateError('missing_service_key')
    value = unquote(value.strip())
    if not re.fullmatch(r'[A-Za-z0-9+/=_-]+', value):
        raise RealEstateError('invalid_service_key')
    return value


def fetch_page(key, trade_type, lawd_code, deal_month, page_no, page_size, *, timeout, max_bytes, property_type='apartment'):
    """One HTTPS request; no redirects/proxies, strict deadline, bounded identity body."""
    validate_property_type(property_type)
    if trade_type not in ENDPOINTS:
        raise RealEstateError('invalid_trade_type')
    connection = http.client.HTTPSConnection('apis.data.go.kr', timeout=timeout)
    deadline = time.monotonic() + timeout
    try:
        query = urlencode({'serviceKey': key, 'LAWD_CD': lawd_code, 'DEAL_YMD': deal_month,
                           'pageNo': str(page_no), 'numOfRows': str(page_size)})
        endpoints = OFFICETEL_ENDPOINTS if property_type == 'officetel' else ENDPOINTS
        connection.request('GET', endpoints[trade_type] + '?' + query,
            headers={'Accept': 'application/xml', 'Accept-Encoding': 'identity',
                     'User-Agent': 'KoreaReplay-official-reports/1.0'})
        response = connection.getresponse()
        if response.status != 200:
            raise RealEstateError('upstream_auth' if response.status in (401, 403)
                else 'upstream_quota' if response.status == 429 else 'upstream_http')
        if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise RealEstateError('unexpected_content_encoding')
        size = response.getheader('Content-Length')
        if size is not None and (not size.isdigit() or int(size) > max_bytes):
            raise RealEstateError('response_size_limit')
        chunks = []; consumed = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RealEstateError('upstream_timeout')
            socket = connection.sock or getattr(getattr(response.fp, 'raw', None), '_sock', None)
            if socket:
                socket.settimeout(remaining)
            chunk = response.read1(min(64 * 1024, max_bytes + 1 - consumed))
            if not chunk:
                break
            chunks.append(chunk); consumed += len(chunk)
            if consumed > max_bytes:
                raise RealEstateError('response_size_limit')
        raw = b''.join(chunks)
        assert_no_secret(raw, key)
        return raw
    except RealEstateError:
        raise
    except TimeoutError:
        raise RealEstateError('upstream_timeout') from None
    except (OSError, http.client.HTTPException, ValueError):
        raise RealEstateError('upstream_network') from None
    finally:
        connection.close()


def immutable(root, relative, data):
    path = root / relative; _reject_links(path.absolute())
    if not path.resolve().is_relative_to(root.resolve()):
        raise RealEstateError('invalid_checkpoint_path')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise RealEstateError('immutable_content_conflict')
    else:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix='.pending-',dir=path.parent,delete=False) as stream:
                temporary=Path(stream.name);stream.write(data);stream.flush();os.fsync(stream.fileno())
            # Atomic create, with no replace even on POSIX. An interrupted write
            # cannot leave a truncated file at the immutable content address.
            try:os.link(temporary,path)
            except FileExistsError:
                if path.read_bytes()!=data:raise RealEstateError('immutable_content_conflict')
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
    return {'path': relative, 'sha256': sha256(data), 'bytes': len(data)}


class Collector:
    def __init__(self, root, registry, *, as_of=None, months=61, transport=fetch_page,
                 clock=instant, reserve_bytes=RESERVE_BYTES, extend_window=False, advance_window=False, property_type='apartment'):
        self.property_type = validate_property_type(property_type)
        if extend_window and advance_window:
            raise RealEstateError('conflicting_window_migration')
        self.root = Path(root).absolute(); _reject_links(self.root)
        if any(p.lower() in ('public', 'dist') for p in self.root.parts):
            raise RealEstateError('public_output_forbidden')
        self.root.mkdir(parents=True, exist_ok=True)
        self.registry = registry; self.clock = clock
        self.transport = partial(fetch_page, property_type=property_type) if transport is fetch_page else transport
        self.reserve_bytes = reserve_bytes
        self._space()
        self.database = self.root / 'checkpoint.sqlite'
        _reject_links(self.database)
        self.db = sqlite3.connect(self.database, timeout=5)
        self.db.row_factory = sqlite3.Row
        region_order = priority_map(registry['regions'])
        self.db.create_function('collection_region_priority', 1,
                                lambda code: region_order.get(code, 7), deterministic=True)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, trade_type TEXT NOT NULL,
            lawd_code TEXT NOT NULL, deal_month TEXT NOT NULL, priority INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', error_code TEXT, pages TEXT NOT NULL DEFAULT '[]',
            snapshot TEXT, updated_at TEXT);
          CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, day TEXT NOT NULL,
            trade_type TEXT NOT NULL, job_id TEXT NOT NULL, page_no INTEGER NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
            bytes INTEGER NOT NULL DEFAULT 0, error_code TEXT);
          CREATE TABLE IF NOT EXISTS snapshots (job_id TEXT NOT NULL, sha256 TEXT NOT NULL,
            descriptor TEXT NOT NULL, retrieved_at TEXT NOT NULL, PRIMARY KEY(job_id, sha256));
          CREATE TABLE IF NOT EXISTS lease (id INTEGER PRIMARY KEY, owner TEXT, expires REAL);
        ''')
        saved_type = self.db.execute("SELECT value FROM meta WHERE key='property_type'").fetchone()
        existing_jobs = self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
        inferred_type = saved_type['value'] if saved_type else 'apartment' if existing_jobs else property_type
        if inferred_type != property_type:
            self.db.close()
            raise RealEstateError('property_type_changed_use_new_checkpoint')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('property_type',?)", (property_type,))
        registry_payload = canonical_bytes(registry)
        digest = sha256(registry_payload)
        previous = self.db.execute("SELECT value FROM meta WHERE key='registry_sha256'").fetchone()
        if previous and previous['value'] != digest:
            raise RealEstateError('registry_changed_use_new_checkpoint')
        immutable(self.root, f'registry/{digest}.json', registry_payload)
        sequence = month_sequence(as_of or self.clock(), months)
        existing_months={r[0] for r in self.db.execute('SELECT DISTINCT deal_month FROM jobs')}
        extending=False
        if advance_window and existing_months:
            # Keep every historical month. Advancing the clock adds new work; it
            # never deletes the oldest month or resets completed snapshots/calls.
            if months < 2 or max(sequence) < max(existing_months) or max(existing_months) not in sequence:
                self.db.close()
                raise RealEstateError('planning_advance_outside_window')
            sequence += sorted(existing_months-set(sequence),reverse=True)
        if existing_months and existing_months!=set(sequence):
            previous=self.db.execute("SELECT value FROM meta WHERE key='planning_months'").fetchone()
            try:old_sequence=json.loads(previous['value']) if previous else None
            except (ValueError,TypeError):old_sequence=None
            valid_old = (isinstance(old_sequence,list) and set(old_sequence)==existing_months
                         and len(old_sequence)==len(existing_months))
            valid_extension = valid_old and extend_window and len(old_sequence)<len(sequence) and sequence[:len(old_sequence)]==old_sequence
            valid_advance = valid_old and advance_window and existing_months < set(sequence)
            if not (valid_extension or valid_advance):
                self.db.close()
                raise RealEstateError('planning_window_changed_requires_migration')
            # Hold the write lock while checking the collector lease and adding jobs.
            self.db.execute('BEGIN IMMEDIATE')
            lease=self.db.execute('SELECT expires FROM lease WHERE id=1').fetchone()
            locked_months={r[0] for r in self.db.execute('SELECT DISTINCT deal_month FROM jobs')}
            if ((lease and lease['expires']>time.time()) or locked_months!=existing_months
                    or self.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]!=len(old_sequence)*len(registry['regions'])*len(ENDPOINTS)):
                self.db.rollback()
                self.db.close()
                raise RealEstateError('planning_window_changed_requires_migration')
            extending=True
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('registry_sha256',?)", (digest,))
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('historical_coverage',?)",
                            (registry['historical_coverage'],))
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('planning_months',?)",(json.dumps(sequence),))
            if extending:
                self.db.execute("INSERT OR IGNORE INTO meta VALUES ('planning_previous_months',?)",(json.dumps(old_sequence),))
                self.db.execute("UPDATE meta SET value=? WHERE key='planning_months'",(json.dumps(sequence),))
            for priority, month in enumerate(sequence):
                if extending and advance_window:
                    self.db.execute('UPDATE jobs SET priority=? WHERE deal_month=?',(priority,month))
                for region in registry['regions']:
                    for trade in ENDPOINTS:
                        code = region['lawd_code']; job_id = f'{trade}/{code}/{month}'
                        self.db.execute('INSERT OR IGNORE INTO jobs(id,trade_type,lawd_code,deal_month,priority) VALUES(?,?,?,?,?)',
                                        (job_id, trade, code, month, priority))

    def close(self):
        self.db.close()

    def _space(self,required_bytes=0):
        if shutil.disk_usage(self.root).free-required_bytes < self.reserve_bytes:
            raise RealEstateError('disk_reserve')

    def summary(self):
        counts = {s: 0 for s in ('pending', 'partial', 'complete', 'empty', 'failed')}
        counts.update({r[0]: r[1] for r in self.db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status')})
        return {'expected': sum(counts.values()), **counts,
                'historical_coverage': self.registry['historical_coverage']}

    def _acquire(self):
        owner = uuid.uuid4().hex
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM lease WHERE id=1').fetchone()
            if row and row['expires'] > time.time():
                raise RealEstateError('collector_already_running')
            self.db.execute('INSERT OR REPLACE INTO lease VALUES(1,?,?)', (owner, time.time()+180))
        return owner

    def _reserve(self, job, page_no, daily_budget, owner):
        stamp = self.clock(); day = utc_instant(stamp).astimezone(KST).date().isoformat()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM lease WHERE id=1').fetchone()
            if row is None or row['owner'] != owner:
                raise RealEstateError('collector_lease_lost')
            used = self.db.execute('SELECT COUNT(*) FROM calls WHERE day=? AND trade_type=?',
                                   (day, job['trade_type'])).fetchone()[0]
            if used >= daily_budget:
                raise RealEstateError('local_daily_budget')
            self.db.execute('UPDATE lease SET expires=? WHERE id=1', (time.time()+180,))
            result = self.db.execute('INSERT INTO calls(day,trade_type,job_id,page_no,started_at,status) VALUES(?,?,?,?,?,?)',
                (day, job['trade_type'], job['id'], page_no, stamp, 'reserved'))
            return result.lastrowid

    def _assert_owner(self,owner):
        row=self.db.execute('SELECT owner FROM lease WHERE id=1').fetchone()
        if row is None or row['owner']!=owner:raise RealEstateError('collector_lease_lost')

    def _snapshot(self, job, pages):
        if sum(p['bytes'] for p in pages)>64*1024**2:
            raise RealEstateError('batch_size_limit')
        parsed=[]
        for source in pages:
            source_path=self.root/source['path'];_reject_links(source_path)
            if not source_path.resolve().is_relative_to(self.root.resolve()):
                raise RealEstateError('invalid_checkpoint_path')
            body=source_path.read_bytes()
            if len(body)!=source['bytes'] or sha256(body)!=source['sha256']:
                raise RealEstateError('checkpoint_hash_mismatch')
            parsed.append(normalize_xml_page(body,property_type=self.property_type,lawd_code=job['lawd_code'],deal_month=job['deal_month'],
                retrieved_at=source['retrieved_at'],trade_type=job['trade_type']))
        partition=build_partitions(parsed)[0]
        payload=canonical_bytes(partition);stored,encoding=encode_snapshot(payload);part_hash=sha256(stored)
        snapshot_ref={**immutable(self.root,f"snapshots/{job['id']}/{part_hash}.json.gz",stored),**encoding}
        if job['snapshot']:
            before=json.loads(job['snapshot']);old_path=self.root/before['path'];_reject_links(old_path)
            old_body=old_path.read_bytes()
            if len(old_body)!=before['bytes'] or sha256(old_body)!=before['sha256']:
                raise RealEstateError('previous_snapshot_hash_mismatch')
            previous=json.loads(decode_snapshot(old_body,before));old_ids={r['id'] for r in previous['records']};new_ids={r['id'] for r in partition['records']}
            difference={'kind':'snapshot-row-difference','previous_sha256':before['sha256'],
                'current_sha256':part_hash,'unchanged_rows':len(old_ids&new_ids),
                'added_rows':len(new_ids-old_ids),'removed_rows':len(old_ids-new_ids),
                'not_transaction_event_identity':True}
            immutable(self.root,f"changes/{job['id']}/{sha256(canonical_bytes(difference))}.json",canonical_bytes(difference))
        return ('empty' if partition['audit']['record_count']==0 else 'complete'),snapshot_ref

    def reprocess(self):
        """Re-normalize verified local raw pages; preserve old snapshots, no network."""
        self._space(5*1024**3 if self.reserve_bytes else 0)
        owner=self._acquire();processed=0
        try:
            jobs=list(self.db.execute("SELECT * FROM jobs WHERE status IN ('complete','empty') ORDER BY id"))
            for job in jobs:
                self._space(MAX_SNAPSHOT_BYTES+1024**2)
                with self.db:self.db.execute('UPDATE lease SET expires=? WHERE owner=?',(time.time()+180,owner))
                status,ref=self._snapshot(job,json.loads(job['pages']))
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    self._assert_owner(owner)
                    self.db.execute('UPDATE jobs SET snapshot=?,status=?,updated_at=? WHERE id=?',
                        (canonical_bytes(ref).decode(),status,self.clock(),job['id']))
                    self.db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)',
                        (job['id'],ref['sha256'],canonical_bytes(ref).decode(),self.clock()))
                processed+=1
            return {'status':'reprocessed','jobs':processed,'requests':0,'coverage':self.summary()}
        finally:
            with self.db:self.db.execute('DELETE FROM lease WHERE id=1 AND owner=?',(owner,))

    def collect(self, key, *, max_requests=600, max_bytes=64*1024**2, daily_budget=8000,
                min_interval=0.3, timeout=60, page_size=1000, retry_failed=False, refresh=False,
                collect_months=None):
        if (not 1 <= max_requests <= 2000 or not 1 <= max_bytes <= 64*1024**2
                or not 1 <= daily_budget <= 8000 or not 0 <= min_interval <= 60
                or not 1 <= timeout <= 60 or not 1 <= page_size <= 1000):
            raise RealEstateError('invalid_collection_budget')
        if collect_months is not None and (not collect_months or len(collect_months)>61
                or any(not isinstance(m,str) or not re.fullmatch(r'[0-9]{4}(?:0[1-9]|1[0-2])',m) for m in collect_months)):
            raise RealEstateError('invalid_month_filter')
        condition=''
        if collect_months:
            available={r[0] for r in self.db.execute('SELECT DISTINCT deal_month FROM jobs')}
            if not set(collect_months)<=available:raise RealEstateError('month_outside_planned_window')
            condition=' AND deal_month IN ('+','.join('?' for _ in collect_months)+')'
        # Operational start margin: default 30 GiB reserve + 5 GiB headroom.
        self._space(5*1024**3 if self.reserve_bytes else 0)
        owner = self._acquire(); used = 0; transferred = 0; stopped = 'work_complete'; failures = 0
        start = time.monotonic(); last_request = 0.0
        try:
            with self.db:
                if retry_failed:
                    self.db.execute("UPDATE jobs SET status='pending',pages='[]',error_code=NULL WHERE status='failed'"+condition,collect_months or [])
                if refresh:
                    self.db.execute("UPDATE jobs SET status='pending',pages='[]',error_code=NULL WHERE status IN ('complete','empty')"+condition,collect_months or [])
            while used < max_requests and transferred < max_bytes:
                if max_bytes-transferred < MAX_PAGE_BYTES and used:
                    stopped='run_budget'; break
                job = self.db.execute("SELECT * FROM jobs WHERE status IN ('pending','partial')"+condition+
                    ' ORDER BY collection_region_priority(lawd_code),priority,lawd_code,trade_type LIMIT 1',collect_months or []).fetchone()
                if job is None:
                    break
                self._space(MAX_PAGE_BYTES+MAX_SNAPSHOT_BYTES+1024**2)
                pages = json.loads(job['pages'])
                if pages and (utc_instant(self.clock())-utc_instant(pages[0]['retrieved_at'])).total_seconds() > 900:
                    # Old raw pages remain immutable; a current full snapshot starts over.
                    pages = []
                    with self.db:
                        self.db.execute('BEGIN IMMEDIATE')
                        self._assert_owner(owner)
                        self.db.execute("UPDATE jobs SET pages='[]',status='pending' WHERE id=?", (job['id'],))
                page_no = len(pages)+1
                try:
                    call_id = self._reserve(job, page_no, daily_budget, owner)
                except RealEstateError as error:
                    if error.code != 'local_daily_budget':
                        raise
                    stopped = error.code; break
                delay = min_interval - (time.monotonic()-last_request)
                if delay > 0:
                    time.sleep(delay)
                last_request = time.monotonic(); used += 1; raw = b''
                try:
                    raw = self.transport(key, job['trade_type'], job['lawd_code'], job['deal_month'], page_no,
                        page_size, timeout=timeout, max_bytes=min(MAX_PAGE_BYTES, max_bytes-transferred))
                    if not isinstance(raw, bytes) or not 0 < len(raw) <= min(MAX_PAGE_BYTES, max_bytes-transferred):
                        raise RealEstateError('response_size_limit')
                    transferred += len(raw); assert_no_secret(raw, key)
                    stamp = self.clock(); digest = sha256(raw)
                    raw_ref = immutable(self.root, f"raw/{job['trade_type']}/{job['lawd_code']}/{job['deal_month']}/{digest}.xml", raw)
                    page = normalize_xml_page(raw, property_type=self.property_type, lawd_code=job['lawd_code'], deal_month=job['deal_month'],
                                              retrieved_at=stamp, trade_type=job['trade_type'])
                    if page['page_no'] != page_no or page['page_size'] != page_size:
                        raise RealEstateError('unexpected_page_metadata')
                    if pages and page['total_count'] != pages[0]['total_count']:
                        raise RealEstateError('inconsistent_pagination')
                    pages.append({**raw_ref, 'retrieved_at': stamp, 'page_no': page_no,
                                  'total_count': page['total_count'], 'page_size': page_size})
                    required = max(1, (page['total_count']+page_size-1)//page_size)
                    if required > 1000:
                        raise RealEstateError('page_count_limit')
                    status = 'partial'; snapshot_ref = None
                    if len(pages) == required:
                        status,snapshot_ref=self._snapshot(job,pages)
                    with self.db:
                        self.db.execute('BEGIN IMMEDIATE')
                        self._assert_owner(owner)
                        self.db.execute('UPDATE calls SET finished_at=?,status=?,bytes=? WHERE id=?', (stamp,'validated',len(raw),call_id))
                        self.db.execute('UPDATE jobs SET pages=?,status=?,error_code=NULL,snapshot=COALESCE(?,snapshot),updated_at=? WHERE id=?',
                            (canonical_bytes(pages).decode(),status,canonical_bytes(snapshot_ref).decode() if snapshot_ref else None,stamp,job['id']))
                        if snapshot_ref:
                            self.db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?)',
                                (job['id'],snapshot_ref['sha256'],canonical_bytes(snapshot_ref).decode(),stamp))
                    failures = 0
                except (RealEstateError, OSError, ValueError, KeyError) as error:
                    failures += 1
                    code = error.code if isinstance(error,RealEstateError) else 'checkpoint_error'
                    with self.db:
                        self.db.execute('UPDATE calls SET finished_at=?,status=?,bytes=?,error_code=? WHERE id=?', (self.clock(),'failed',len(raw),code,call_id))
                        if code!='collector_lease_lost':
                            self._assert_owner(owner)
                            self.db.execute("UPDATE jobs SET status='failed',error_code=?,updated_at=? WHERE id=?", (code,self.clock(),job['id']))
                    if code in ('upstream_auth','upstream_quota','secret_reflection','credential_field_reflection',
                                'disk_reserve','response_size_limit','checkpoint_error','collector_lease_lost','remote_checkpoint_error'):
                        stopped=code; break
                    if failures >= 3:
                        stopped='consecutive_failures'; break
            else:
                stopped='run_budget'
            report={'schema_version':1,'kind':'real-estate-collection-run','finished_at':self.clock(),
                'requests':used,'response_bytes':transferred,'stop_reason':stopped,
                'elapsed_seconds':round(time.monotonic()-start,3),'coverage':self.summary(),
                'retries':0,'daily_budget_per_source':daily_budget,
                'region_order':POLICY_ID,
                'budget_scope':'this_checkpoint_root_all_runs_provider_service; other_consumers_not_counted'}
            payload=canonical_bytes(report); immutable(self.root,f'runs/{sha256(payload)}.json',payload)
            return report
        finally:
            with self.db:
                self.db.execute('DELETE FROM lease WHERE id=1 AND owner=?',(owner,))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--regions',required=True,type=Path)
    parser.add_argument('--as-of')
    parser.add_argument('--property-type', choices=('apartment','officetel'), default='apartment')
    parser.add_argument('--months',type=int,default=61)
    parser.add_argument('--extend-window',action='store_true',help='Append older months to this exact checkpoint without resetting jobs or calls')
    parser.add_argument('--advance-window',action='store_true',help='Add newly reached calendar months and retain all historical jobs and source records')
    parser.add_argument('--max-requests',type=int,default=600)
    parser.add_argument('--max-bytes',type=int,default=64*1024**2)
    parser.add_argument('--daily-budget',type=int,default=8000)
    parser.add_argument('--min-interval',type=float,default=0.3)
    parser.add_argument('--timeout',type=int,default=60)
    parser.add_argument('--collect-month',action='append')
    parser.add_argument('--secret-file',type=Path)
    parser.add_argument('--retry-failed',action='store_true')
    parser.add_argument('--refresh',action='store_true')
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--reprocess',action='store_true',help='Re-normalize stored raw pages, with no key or network access.')
    args=parser.parse_args(); collector=None
    try:
        if args.reprocess and args.execute:raise RealEstateError('conflicting_collection_mode')
        registry=load_registry(args.regions)
        collector=Collector(args.root,registry,as_of=args.as_of,months=args.months,extend_window=args.extend_window,advance_window=args.advance_window,property_type=args.property_type)
        result=collector.reprocess() if args.reprocess else collector.collect(read_key(args.secret_file),max_requests=args.max_requests,max_bytes=args.max_bytes,
            daily_budget=args.daily_budget,min_interval=args.min_interval,timeout=args.timeout,
            retry_failed=args.retry_failed,refresh=args.refresh,collect_months=args.collect_month) if args.execute else {'status':'planned','coverage':collector.summary()}
        print(json.dumps(result,ensure_ascii=False))
    except (OSError,ValueError,KeyError,sqlite3.Error) as error:
        parser.exit(1,f"real_estate_fetch: {error.code if isinstance(error,RealEstateError) else 'invalid_input'}\n")
    finally:
        if collector:collector.close()


if __name__=='__main__':
    main()
