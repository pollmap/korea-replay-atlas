"""Bounded correction queue; published job pages stay intact until full success.

The worker owns the bulk lock. Collector owns its lease and reserves every page
in the existing calls ledger before transport. This module performs no I/O to a
provider and has no public-release authority.
"""
from datetime import timedelta
import json
import re
import hashlib
from functools import lru_cache
from pathlib import Path

from .real_estate import RealEstateError, canonical_bytes, utc_instant
from .real_estate_availability import source_start
from .real_estate_scope import scope_condition
from .property_automation import KST, SAFE_RETRY_ERRORS, retry_at

ENABLE = 'KOREA_REPLAY_CORRECTION_REFRESH'
QUEUE = 'property_correction_queue'
CALLS = 'property_correction_calls'
LANES = ('recent', 'rolling', 'history', 'retry')
BASE = {'recent': 5600, 'history': 1600, 'retry': 800}
MAX_JOBS = 50
CONTENT_REVISION = 'property_correction_content_revision'


def _exists(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def initialize(db):
    db.executescript("""CREATE TABLE IF NOT EXISTS property_correction_queue (
        job_id TEXT PRIMARY KEY, lane TEXT NOT NULL, status TEXT NOT NULL,
        pages TEXT NOT NULL, base_snapshot TEXT NOT NULL, scheduled_at TEXT NOT NULL,
        updated_at TEXT NOT NULL, error_code TEXT, retry_day TEXT, retries INTEGER NOT NULL DEFAULT 0,
        checked_at TEXT, normalizer_sha TEXT);
        CREATE TABLE IF NOT EXISTS property_correction_calls (
        call_id INTEGER PRIMARY KEY, bucket TEXT NOT NULL);""")


def _bucket(lane):
    return 'recent' if lane == 'rolling' else lane


def _lane(month, now):
    age = now.year * 12 + now.month - (int(month[:4]) * 12 + int(month[4:]))
    return None if age < 0 else 'recent' if age < 3 else 'rolling' if age < 24 else 'history'


def _due(job, lane, now):
    try:
        checked = utc_instant(job['updated_at']).astimezone(KST)
    except (ValueError, TypeError, RealEstateError):
        return False  # malformed legacy dates require review, never blanket reset
    return (checked.date() < now.date() if lane == 'recent' else
            checked <= now - timedelta(days=7 if lane == 'rolling' else 90))


def _retry_due(row, now):
    if row['error_code'] not in SAFE_RETRY_ERRORS:
        return False
    used = row['retries'] if row['retry_day'] == now.date().isoformat() else 0
    if type(used) is not int or not 0 <= used <= 3:
        raise RealEstateError('correction_retry_state')
    return used < 3 and utc_instant(retry_at(row['error_code'], utc_instant(row['updated_at']))) <= now


def plan(db, stamp, *, scope='priority-nine', trades=('sale', 'rent'), max_requests=500):
    """Read-only choice, at most 50 jobs. No jobs/queue mutation on an idle day."""
    if type(max_requests) is not int or not 1 <= max_requests <= 500:
        raise RealEstateError('correction_budget')
    now = utc_instant(stamp).astimezone(KST); day = now.date().isoformat()
    queued = ({row['job_id']: dict(row) for row in db.execute('SELECT * FROM ' + QUEUE)}
              if _exists(db, QUEUE) else {})
    candidates = {(trade, lane): [] for trade in trades for lane in LANES}
    rows = db.execute("SELECT id,trade_type,deal_month,snapshot,updated_at FROM jobs j "
                      "WHERE status IN ('complete','empty') AND snapshot IS NOT NULL" +
                      scope_condition(scope, 'j.lawd_code') +
                      ' ORDER BY updated_at,collection_region_priority(lawd_code),priority,id')
    for row in rows:
        trade = row['trade_type']; month = row['deal_month']
        if trade not in trades or month < source_start('apartment', trade):
            continue
        job = dict(row); lane = _lane(month, now)
        if lane is None:
            continue
        pending = queued.get(row['id'])
        if pending and pending['status'] in ('pending', 'partial', 'failed'):
            if pending['base_snapshot'] != row['snapshot']:
                raise RealEstateError('correction_base_changed')
            if pending['status'] == 'failed':
                if not _retry_due(pending, now):
                    continue
                lane = 'retry'
            else:
                lane = pending['lane']
        else:
            if (pending and pending['status'] == 'complete' and pending['base_snapshot'] == row['snapshot']
                    and pending['normalizer_sha'] == normalizer_version() and pending['checked_at']):
                job['updated_at'] = pending['checked_at']
            if not _due(job, lane, now):
                continue
        target = candidates[(trade, lane)]
        if len(target) < MAX_JOBS:
            target.append(row['id'])
    totals = dict(db.execute('SELECT trade_type,COUNT(*) FROM calls WHERE day=? GROUP BY trade_type', (day,)))
    used = {}
    if _exists(db, CALLS):
        used = {(r[0], r[1]): r[2] for r in db.execute(
            'SELECT c.trade_type,r.bucket,COUNT(*) FROM calls c JOIN ' + CALLS +
            ' r ON r.call_id=c.id WHERE c.day=? GROUP BY c.trade_type,r.bucket', (day,))}
    # Daily recent work first; the retry reserve cannot be spent while due retries
    # exist. Rolling months share recent's 70%; history consumes its 20% + slack.
    for lane in ('recent', 'retry', 'rolling', 'history'):
        for trade in trades:
            jobs = candidates[(trade, lane)]
            if not jobs or totals.get(trade, 0) >= 8000:
                continue
            bucket = _bucket(lane)
            active = {_bucket(other) for other in LANES if candidates[(trade, other)]}
            cap = BASE[bucket]
            cap += sum(max(0, cap - used.get((trade, other), 0)) for other, cap in BASE.items()
                        if other != bucket and other not in active)
            room = max(0, cap - used.get((trade, bucket), 0))
            allowed = min(room, 8000 - totals.get(trade, 0), max_requests)
            if allowed:
                return {'job_ids': jobs, 'lane': lane, 'trade': trade, 'day': day,
                        'bucket': bucket, 'daily_lane_cap': min(8000, cap),
                        'max_requests': allowed}
    return None


def validate(batch):
    if (not isinstance(batch, dict) or set(batch) != {'job_ids','lane','trade','day','bucket','daily_lane_cap','max_requests'}
            or batch['lane'] not in LANES or batch['trade'] not in ('sale','rent')
            or batch['bucket'] != _bucket(batch['lane'])
            or not isinstance(batch['job_ids'], list) or not 1 <= len(batch['job_ids']) <= MAX_JOBS
            or len(set(batch['job_ids'])) != len(batch['job_ids'])
            or any(not isinstance(v, str) or not re.fullmatch(r'(sale|rent)/[0-9]{5}/[0-9]{6}', v) for v in batch['job_ids'])
            or type(batch['daily_lane_cap']) is not int or not 1 <= batch['daily_lane_cap'] <= 8000
            or type(batch['max_requests']) is not int or not 1 <= batch['max_requests'] <= 500):
        raise RealEstateError('correction_batch_invalid')


def prepare(db, batch, stamp, *, scope):
    validate(batch)
    now = utc_instant(stamp).astimezone(KST)
    if batch['day'] != now.date().isoformat():
        raise RealEstateError('correction_day_changed')
    initialize(db)
    with db:
        for job_id in batch['job_ids']:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND status IN ('complete','empty') AND snapshot IS NOT NULL" +
                             scope_condition(scope), (job_id,)).fetchone()
            if (row is None or row['trade_type'] != batch['trade']
                    or row['deal_month'] < source_start('apartment', row['trade_type'])):
                raise RealEstateError('correction_job_changed')
            old = db.execute('SELECT * FROM ' + QUEUE + ' WHERE job_id=?', (job_id,)).fetchone()
            if old and old['status'] in ('pending','partial','failed'):
                if old['base_snapshot'] != row['snapshot']:
                    raise RealEstateError('correction_base_changed')
                if old['status'] == 'failed':
                    if batch['lane'] != 'retry' or not _retry_due(dict(old), now):
                        raise RealEstateError('correction_retry_not_due')
                    count = old['retries'] if old['retry_day'] == batch['day'] else 0
                    db.execute("UPDATE " + QUEUE + " SET status='pending',lane='retry',error_code=NULL,retry_day=?,retries=? WHERE job_id=?",
                               (batch['day'], count + 1, job_id))
                continue
            job = dict(row)
            receipt_ok = (old and old['base_snapshot'] == row['snapshot'] and old['normalizer_sha'] == normalizer_version())
            if receipt_ok and old['checked_at']:
                job['updated_at'] = old['checked_at']
            lane = _lane(row['deal_month'], now)
            if lane != batch['lane'] or not _due(job, lane, now):
                raise RealEstateError('correction_job_not_due')
            db.execute('INSERT OR REPLACE INTO ' + QUEUE + ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (job_id, batch['lane'], 'pending', '[]', row['snapshot'], stamp, stamp, None, None, 0, old['checked_at'] if receipt_ok else None, old['normalizer_sha'] if receipt_ok else None))


def select_job(db, batch):
    args = batch['job_ids']
    row = db.execute('SELECT j.*,q.pages AS correction_pages,q.base_snapshot FROM jobs j JOIN ' + QUEUE +
                     " q ON q.job_id=j.id WHERE q.status IN ('pending','partial') AND j.id IN (" +
                     ','.join('?' for _ in args) + ') ORDER BY j.updated_at,j.priority,j.id LIMIT 1', args).fetchone()
    if row is None:
        return None
    if row['status'] not in ('complete','empty') or row['snapshot'] != row['base_snapshot']:
        raise RealEstateError('correction_base_changed')
    job = dict(row); job['previous_pages'] = job['pages']; job['pages'] = job.pop('correction_pages')
    return job


def reserve(db, batch, stamp, trade):
    day = utc_instant(stamp).astimezone(KST).date().isoformat()
    if day != batch['day']:
        raise RealEstateError('correction_day_changed')
    used = db.execute('SELECT COUNT(*) FROM calls c JOIN ' + CALLS +
                      ' r ON r.call_id=c.id WHERE c.day=? AND c.trade_type=? AND r.bucket=?',
                      (day, trade, batch['bucket'])).fetchone()[0]
    if used >= batch['daily_lane_cap']:
        raise RealEstateError('local_daily_budget')


def save_pages(db, job, pages, status, snapshot, stamp, *, unchanged=False):
    if snapshot and not unchanged:
        changed = db.execute("UPDATE jobs SET pages=?,status=?,snapshot=?,error_code=NULL,updated_at=? "
                             "WHERE id=? AND snapshot=? AND status IN ('complete','empty')",
                             (canonical_bytes(pages).decode(), status, canonical_bytes(snapshot).decode(), stamp,
                              job['id'], job['base_snapshot'])).rowcount
        if changed != 1:
            raise RealEstateError('correction_base_changed')
        db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',
                   (CONTENT_REVISION, str(content_revision(db) + 1)))
    db.execute('UPDATE ' + QUEUE + ' SET pages=?,status=?,updated_at=?,error_code=NULL WHERE job_id=?',
               ('[]' if snapshot else canonical_bytes(pages).decode(), 'complete' if snapshot else 'partial', stamp, job['id']))
    if snapshot:
        db.execute('UPDATE ' + QUEUE + ' SET checked_at=?,normalizer_sha=?,base_snapshot=? WHERE job_id=?',
                   (stamp, normalizer_version(), canonical_bytes(snapshot).decode(), job['id']))


def idle_report(collector):
    return {'stop_reason': 'work_complete', 'requests': 0, 'response_bytes': 0,
            'coverage': collector.summary(), 'scope_coverage': collector.summary(scoped=True),
            'correction': {'state': 'not_due', 'source_calls': 0, 'changed_jobs': 0,
                           'content_revision': content_revision(collector.db),
                           'review_required': review_required(collector.db)}}


@lru_cache(maxsize=1)
def normalizer_version():
    # Hash real code rather than a manually forgotten version constant.
    digest = hashlib.sha256()
    for name in ('real_estate.py', 'real_estate_fetch.py', 'real_estate_storage.py'):
        digest.update(name.encode()); digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def unchanged_snapshot(collector, job, pages):
    old = json.loads(job['previous_pages'])
    keys = ('sha256','bytes','page_no','total_count','page_size')
    if len(old) != len(pages) or any(any(a[k] != b[k] for k in keys) for a,b in zip(old,pages)):
        return None
    saved = json.loads(job['snapshot'])
    receipt = collector.db.execute('SELECT normalizer_sha,base_snapshot FROM ' + QUEUE + ' WHERE job_id=?', (job['id'],)).fetchone()
    if not receipt or receipt['normalizer_sha'] != normalizer_version() or receipt['base_snapshot'] != job['snapshot']:
        # Existing snapshots predate the code receipt. Prove the same original
        # timestamps reproduce exactly once; do not guess their normalizer.
        from .real_estate_working_store import read_reference
        from .real_estate_storage import decode_snapshot
        previous = decode_snapshot(read_reference(collector.root, saved), saved)
        if canonical_bytes(collector._partition(job, old)) != previous:
            return None
    return saved


def content_revision(db):
    row = db.execute('SELECT value FROM meta WHERE key=?', (CONTENT_REVISION,)).fetchone()
    if row is None:
        return 0
    if not isinstance(row[0], str) or not re.fullmatch('[0-9]{1,16}', row[0]):
        raise RealEstateError('correction_content_revision')
    return int(row[0])


def review_required(db):
    if not _exists(db, QUEUE):
        return {}
    result = {}
    for code,count in db.execute("SELECT error_code,COUNT(*) FROM " + QUEUE + " WHERE status='failed' GROUP BY error_code"):
        if code not in SAFE_RETRY_ERRORS:
            safe = code if isinstance(code,str) and re.fullmatch('[a-z_]{1,64}',code) else 'correction_review_required'
            result[safe] = result.get(safe,0) + count
    return result
