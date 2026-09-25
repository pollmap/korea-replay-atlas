"""Bounded apartment collection for an ephemeral standard GitHub runner.

Only the private verified archive head advances. This module never publishes a
website release, uploads raw artifacts to GitHub, or clears an uncertain owner.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .real_estate import RealEstateError, utc_instant, _reject_links
from .real_estate_archive import D1Archive
from .real_estate_fetch import fetch_page, instant, month_sequence, read_key, MAX_PAGE_BYTES
from .real_estate_remote import RemoteWorkspace, RemoteCollector
from .real_estate_run_guard import CollectionGuard, DAY, guarded_transport

STATE_KEY = 'property_automation_v1'
SUCCESS_STOPS = {'work_complete', 'run_budget'}
UNCERTAIN_STOPS = {'remote_checkpoint_error', 'checkpoint_error', 'collector_lease_lost'}
RETRY_DELAYS = {'upstream_timeout': timedelta(minutes=30),
                'upstream_network': timedelta(minutes=30), 'upstream_http': timedelta(hours=6)}
SAFE_RETRY_ERRORS = (*RETRY_DELAYS, 'upstream_quota')
KST = timezone(timedelta(hours=9))


def _state(db):
    row = db.execute('SELECT value FROM meta WHERE key=?', (STATE_KEY,)).fetchone()
    value = json.loads(row[0]) if row else {'runs': 0, 'recent_cursor': 0, 'history_cursor': 0}
    if (not isinstance(value, dict) or set(value) != {'runs', 'recent_cursor', 'history_cursor'}
            or any(type(v) is not int or not 0 <= v < 10**9 for v in value.values())):
        raise RealEstateError('automation_state_invalid')
    return value


def prepare_lane(db, stamp, *, mode='auto', months=121):
    """Continue unfinished months; never restart a refresh on every bounded run.

    Three runs in four backfill. Every fourth run checks recent corrections;
    every 28th checks one older month. Cursors live inside the archived checkpoint.
    Existing region priority remains owned by Collector, not this scheduler.
    """
    if mode not in ('auto', 'backfill', 'recent', 'history'):
        raise RealEstateError('automation_mode_invalid')
    state = _state(db)
    number = state['runs'] + 1
    lane = mode if mode != 'auto' else ('history' if number % 28 == 0 else
                                      'recent' if number % 4 == 0 else 'backfill')
    sequence = month_sequence(stamp, months)
    selected = None
    if lane != 'backfill':
        candidates = sequence[:3] if lane == 'recent' else sequence[3:]
        if not candidates:
            lane = 'backfill'
        else:
            cursor = lane + '_cursor'
            selected = candidates[state[cursor] % len(candidates)]
            cutoff = (utc_instant(stamp) - timedelta(days=7 if lane == 'recent' else 90)).isoformat().replace('+00:00', 'Z')
            with db:
                # The previous snapshot and every original remain available while
                # a correction is fetched. Never turn an error into an empty result.
                db.execute("""UPDATE jobs SET status='pending',pages='[]',error_code=NULL
                    WHERE deal_month=? AND status IN ('complete','empty')
                    AND (updated_at IS NULL OR updated_at<?)""", (selected, cutoff))
    return state, lane, selected


def finish_lane(db, state, lane, selected):
    state = dict(state)
    state['runs'] += 1
    if selected is not None:
        unfinished = db.execute("SELECT COUNT(*) FROM jobs WHERE deal_month=? AND status IN ('pending','partial','failed')", (selected,)).fetchone()[0]
        if not unfinished:
            state[lane + '_cursor'] += 1
    with db:
        db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)',
                   (STATE_KEY, json.dumps(state, sort_keys=True)))


def requeue_safe_failures(db, stamp, *, limit=5, selected=None):
    """Bounded durable retries of known provider failures only.

    A requeue consumes one of three attempts per job/KST day even if the run's
    request budget expires first. Remote uncertainty and auth require diagnosis.
    Existing raw-page and successful snapshot references are never reset here.
    """
    if type(limit) is not int or not 1 <= limit <= 5:
        raise RealEstateError('automation_retry_limit')
    now = utc_instant(stamp)
    day = now.astimezone(KST).date().isoformat()
    db.execute('''CREATE TABLE IF NOT EXISTS property_automation_retries (
        job_id TEXT PRIMARY KEY, day TEXT NOT NULL, requeues INTEGER NOT NULL,
        last_retry_at TEXT NOT NULL, error_code TEXT NOT NULL)''')
    condition = ' AND j.deal_month=?' if selected else ''
    rows = db.execute("""SELECT j.id,j.error_code,j.updated_at,r.day,r.requeues,r.last_retry_at
        FROM jobs j LEFT JOIN property_automation_retries r ON r.job_id=j.id
        WHERE j.status='failed' AND j.error_code IN (?,?,?,?)""" + condition +
        ' ORDER BY collection_region_priority(j.lawd_code),j.priority,j.lawd_code,j.trade_type LIMIT 1000',
        (*SAFE_RETRY_ERRORS, *([selected] if selected else []))).fetchall()
    requeued = 0
    next_retry = None
    for job, code, updated, previous_day, count, last_retry in rows:
        if requeued >= limit:
            break
        if not updated:
            continue
        try:
            due = utc_instant(retry_at(code, utc_instant(updated)))
            if last_retry:
                due = max(due, utc_instant(retry_at(code, utc_instant(last_retry))))
        except (RealEstateError, ValueError, TypeError):
            # Malformed legacy timestamps must be investigated, never guessed.
            continue
        used = count if previous_day == day else 0
        if type(used) is not int or not 0 <= used <= 3:
            raise RealEstateError('automation_retry_state_invalid')
        if used >= 3:
            due = max(due, utc_instant(retry_at('local_daily_budget', now)))
        if due > now:
            next_retry = due if next_retry is None else min(next_retry, due)
            continue
        with db:
            changed = db.execute("UPDATE jobs SET status='pending',error_code=NULL WHERE id=? AND status='failed' AND error_code=?",
                                 (job, code)).rowcount
            if changed != 1:
                raise RealEstateError('automation_retry_state_changed')
            db.execute('INSERT OR REPLACE INTO property_automation_retries VALUES(?,?,?,?,?)',
                       (job, day, used + 1, stamp, code))
        requeued += 1
    return {'requeued': requeued, 'limit_per_run': limit, 'limit_per_job_kst_day': 3,
            'next_retry_at': next_retry.strftime('%Y-%m-%dT%H:%M:%SZ') if next_retry else None}


def pagination_budget_preflight(db, stamp, *, max_requests, max_bytes, selected=None):
    """Stop proven unfinishable stale jobs before Collector restarts page one."""
    db.execute('''CREATE TABLE IF NOT EXISTS property_automation_pagination (
        job_id TEXT PRIMARY KEY, first_page_sha TEXT NOT NULL,
        required_requests INTEGER NOT NULL, required_bytes INTEGER NOT NULL)''')
    condition = ' AND j.deal_month=?' if selected else ''
    rows = db.execute("""SELECT j.pages,p.first_page_sha,p.required_requests,p.required_bytes
        FROM jobs j JOIN property_automation_pagination p ON p.job_id=j.id
        WHERE j.status IN ('partial','pending')""" + condition, (selected,) if selected else ()).fetchall()
    now = utc_instant(stamp)
    for raw, first_sha, required_requests, required_bytes in rows:
        pages = json.loads(raw)
        if not pages or pages[0]['sha256'] != first_sha:
            continue
        if (now - utc_instant(pages[0]['retrieved_at'])).total_seconds() <= 900:
            continue  # Still-valid pages can finish across bounded runs.
        if max_requests < required_requests or max_bytes < required_bytes:
            raise RealEstateError('automation_pagination_budget_insufficient')


def record_pagination_budget(db, since_call, report):
    """Keep evidence only when this whole run started and served one job.

    A job partially served after other jobs may fit the next full run budget;
    that case must not produce a blocking assertion.
    """
    with db:
        db.execute("DELETE FROM property_automation_pagination WHERE job_id IN (SELECT id FROM jobs WHERE status IN ('complete','empty'))")
    if report['stop_reason'] != 'run_budget':
        return
    calls = db.execute('SELECT job_id,page_no,status FROM calls WHERE id>? ORDER BY id', (since_call,)).fetchall()
    if (not calls or len({row[0] for row in calls}) != 1 or calls[0][1] != 1
            or any(row[2] != 'validated' for row in calls)):
        return
    job = calls[0][0]
    row = db.execute("SELECT pages FROM jobs WHERE id=? AND status='partial'", (job,)).fetchone()
    if not row:
        return
    pages = json.loads(row[0])
    if not pages or len(pages) != len(calls):
        raise RealEstateError('automation_pagination_evidence_invalid')
    first = pages[0]
    required = (first['total_count'] + first['page_size'] - 1) // first['page_size']
    if required <= len(pages):
        raise RealEstateError('automation_pagination_evidence_invalid')
    # Collector reserves MAX_PAGE_BYTES of remaining response budget before the
    # next call. Raising only the request cap cannot fix a proven byte shortfall.
    with db:
        db.execute('INSERT OR REPLACE INTO property_automation_pagination VALUES(?,?,?,?)',
                   (job, first['sha256'], required, sum(p['bytes'] for p in pages) + MAX_PAGE_BYTES))


def run_automation(root, store, key, *, max_requests=25, max_bytes=16*1024**2,
                   as_of=None, mode='auto', months=121, transport=fetch_page,
                   reserve_bytes=2*1024**3):
    if (type(max_requests) is not int or not 1 <= max_requests <= 500
            or type(max_bytes) is not int or not 8*1024**2 <= max_bytes <= 64*1024**2):
        raise RealEstateError('automation_budget_invalid')
    stamp = as_of or instant()
    month_sequence(stamp, months)
    if mode not in ('auto', 'backfill', 'recent', 'history'):
        raise RealEstateError('automation_mode_invalid')
    workspace = RemoteWorkspace(root, store)
    guard = CollectionGuard(store)
    guard.initialize()
    if guard.query(f"SELECT 1 FROM collection_reservations WHERE day={DAY} AND error_code='upstream_quota' LIMIT 1")['results']:
        raise RealEstateError('upstream_quota')
    # HTTP 200 XML quota responses are durably stored before normalization.
    # Their provider error therefore lives in the checkpoint calls, not the raw
    # reservation's error_code. Use the same authoritative KST day as the guard.
    today = guard.query(f'SELECT {DAY} AS day')['results'][0]['day']
    with sqlite3.connect((workspace.root / 'checkpoint.sqlite').as_uri() + '?mode=ro', uri=True) as db:
        if db.execute("SELECT 1 FROM calls WHERE day=? AND error_code='upstream_quota' LIMIT 1", (today,)).fetchone():
            raise RealEstateError('upstream_quota')
    lease = guard.acquire(workspace.head)
    collector = None
    try:
        guard.seed_budget(lease, workspace.head, workspace.baseline_counts)
        collector = RemoteCollector(workspace, as_of=stamp, months=months, advance_window=True,
            reserve_bytes=reserve_bytes, transport=guarded_transport(guard, lease, transport))
        state, lane, selected = prepare_lane(collector.db, stamp, mode=mode, months=months)
        retries = requeue_safe_failures(collector.db, stamp, limit=min(5, max_requests), selected=selected)
        pagination_budget_preflight(collector.db, instant(), max_requests=max_requests, max_bytes=max_bytes, selected=selected)
        since_call = collector.db.execute('SELECT COALESCE(MAX(id),0) FROM calls').fetchone()[0]
        report = collector.collect(key, max_requests=max_requests, max_bytes=max_bytes,
                                   timeout=30, collect_months=[selected] if selected else None)
        if report['stop_reason'] in UNCERTAIN_STOPS:
            raise RealEstateError('automation_recovery_required')
        # A source quota/auth failure is durably recorded, but is not a successful
        # scheduled run and does not advance the correction cursor.
        if report['stop_reason'] in SUCCESS_STOPS:
            finish_lane(collector.db, state, lane, selected)
        record_pagination_budget(collector.db, since_call, report)
        collector.close()
        collector = None
        pending = guard.query("SELECT 1 FROM collection_reservations WHERE owner=? AND generation=? AND phase='reserved' LIMIT 1",
                              [lease['owner'], lease['generation']])['results']
        if pending:
            raise RealEstateError('automation_recovery_required')
        guard.renew(lease)
        published = workspace.publish(lease, heartbeat=lambda: guard.renew(lease))
        guard.release(lease, published['backup'])
        return {'schema_version': 1, 'kind': 'private-property-collection',
                'status': 'collected' if report['stop_reason'] in SUCCESS_STOPS else 'source_stopped',
                'lane': lane, 'month': selected, 'requests': report['requests'],
                'response_bytes': report['response_bytes'], 'stop_reason': report['stop_reason'],
                'coverage': report['coverage'], 'archive_verified': True,
                'retry_policy': retries,
                'public_release': False, 'finished_at': report['finished_at'],
                'next_retry_at': retry_at(report['stop_reason'])}
    except Exception:
        # Same conservative release rule as run_remote. No expiration takeover,
        # reset, deletion, budget refund or retry of a possibly executed request.
        try:
            attempted = guard.query('SELECT 1 FROM collection_reservations WHERE owner=? AND generation=? LIMIT 1',
                                    [lease['owner'], lease['generation']])['results']
            if not attempted:
                guard.release(lease, workspace.head)
        except (RealEstateError, sqlite3.Error):
            pass
        raise
    finally:
        if collector:
            collector.close()


def retry_at(code, now=None):
    now = now or datetime.now(timezone.utc)
    if code in RETRY_DELAYS:
        return (now + RETRY_DELAYS[code]).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    zone = timezone.utc if code in ('archive_daily_write_limit', 'archive_daily_read_limit') else timezone(timedelta(hours=9))
    if code not in ('archive_daily_write_limit', 'archive_daily_read_limit', 'upstream_quota', 'local_daily_budget', 'collection_daily_budget'):
        return None
    local = now.astimezone(zone)
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=17, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def credentials():
    """Validate every injected credential before any network connection."""
    token = os.environ.get('CLOUDFLARE_API_TOKEN', '')
    broker_token = os.environ.get('PROPERTY_ARCHIVE_BROKER_TOKEN', '')
    broker_url = os.environ.get('PROPERTY_ARCHIVE_BROKER_URL', '')
    config_text = os.environ.get('PROPERTY_ARCHIVE_CONFIG_JSON', '')
    broker_requested = bool(broker_token or broker_url)
    if (not config_text or len(config_text) > 16384
            or (broker_requested and (not broker_token or not broker_url))
            or (not broker_requested and (not token or len(token) > 4096))):
        raise RealEstateError('automation_credentials_missing')
    key = read_key()
    config = json.loads(config_text)
    if not isinstance(config, dict) or set(config) != {'account_id', 'control_database', 'object_databases'}:
        raise RealEstateError('automation_archive_configuration')
    # Explicit broker configuration never falls back to a management credential.
    # Constructors validate their complete configuration without network access.
    if broker_requested:
        from .real_estate_archive import BrokerArchive
        return BrokerArchive(config, token=broker_token, endpoint=broker_url), key
    return D1Archive(config, token=token), key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-parent', type=Path, required=True)
    parser.add_argument('--mode', choices=('auto', 'backfill', 'recent', 'history'), default='auto')
    parser.add_argument('--max-requests', type=int, default=25)
    parser.add_argument('--max-bytes', type=int, default=16*1024**2)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args(argv)
    try:
        if not args.execute:
            raise RealEstateError('explicit_execution_required')
        store, key = credentials()
        parent = args.work_parent.absolute()
        _reject_links(parent)
        parent.mkdir(parents=True, exist_ok=True)
        # A retry always restores the latest verified head in a fresh directory.
        # Failed directories are retained for local diagnosis; no raw GH artifacts.
        root = parent / ('collection-' + uuid.uuid4().hex)
        result = run_automation(root, store, key, mode=args.mode,
                                max_requests=args.max_requests, max_bytes=args.max_bytes)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result['status'] == 'collected' else 1
    except Exception as error:
        code = error.code if isinstance(error, RealEstateError) else 'automation_invalid_or_unavailable'
        # Do not print exception strings, tracebacks, config, response bodies or URLs.
        if not re.fullmatch('[a-z0-9_]{1,80}', str(code)):
            code = 'automation_invalid_or_unavailable'
        print(json.dumps({'status': 'blocked', 'error': code, 'public_release': False,
                          'next_retry_at': retry_at(code)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
