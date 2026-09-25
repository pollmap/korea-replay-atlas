import json
import sqlite3

import pytest

from pipeline.real_estate import RealEstateError
from pipeline.real_estate_archive import backup, restore
from pipeline.real_estate_run_guard import CollectionGuard
from pipeline.property_automation import (STATE_KEY, finish_lane, main,
    pagination_budget_preflight, prepare_lane, record_pagination_budget,
    requeue_safe_failures, retry_at, run_automation)
from test_real_estate_archive import LocalD1
from test_real_estate_fetch import collector, xml, rent, STAMP


def parent(tmp_path):
    c = collector(tmp_path / 'original', lambda *a, **kw: xml())
    c.collect('fixture-key', max_requests=1, min_interval=0)
    c.close()
    store = LocalD1()
    backup(tmp_path / 'original', store)
    return store


def test_real_remote_roundtrip_and_new_month_preserve_old_jobs(tmp_path):
    store = parent(tmp_path)
    report = run_automation(tmp_path / 'run-1', store, 'fixture-key', as_of='2026-10-01T00:00:00Z',
                            months=2, max_requests=1, reserve_bytes=0, transport=lambda *a, **kw: xml())
    assert report['status'] == 'collected'
    assert report['requests'] == 1 and report['public_release'] is False
    assert report['coverage']['expected'] == 4
    assert CollectionGuard(store).status()['owner'][0]['occupied'] == 0
    restore(tmp_path / 'restored', store)
    with sqlite3.connect(tmp_path / 'restored/checkpoint.sqlite') as db:
        assert db.execute('SELECT DISTINCT deal_month FROM jobs ORDER BY 1').fetchall() == [('202609',), ('202610',)]
        state = json.loads(db.execute('SELECT value FROM meta WHERE key=?', (STATE_KEY,)).fetchone()[0])
        assert state['runs'] == 1
        assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 2


def test_persistence_failure_preserves_head_and_requires_recovery(tmp_path):
    store = parent(tmp_path)
    before = store.head()
    put = store.put
    def fail(raw):
        if raw == xml():
            raise RealEstateError('archive_daily_write_limit')
        return put(raw)
    store.put = fail
    calls = []
    def fetch(*a, **kw):
        calls.append(1)
        return xml()
    with pytest.raises(RealEstateError, match='automation_recovery_required'):
        run_automation(tmp_path / 'failed', store, 'fixture-key', as_of=STAMP, months=2,
                       max_requests=1, reserve_bytes=0, transport=fetch)
    assert len(calls) == 1 and store.head() == before
    assert CollectionGuard(store).status()['owner'][0]['occupied'] == 1
    with pytest.raises(RealEstateError, match='busy_or_backup_changed'):
        run_automation(tmp_path / 'second', store, 'fixture-key', as_of=STAMP, months=2,
                       max_requests=1, reserve_bytes=0, transport=fetch)
    assert len(calls) == 1


def test_quota_failure_is_archived_but_never_success_or_empty(tmp_path):
    store = parent(tmp_path)
    def quota(*a, **kw):
        raise RealEstateError('upstream_quota')
    report = run_automation(tmp_path / 'quota', store, 'fixture-key', as_of=STAMP, months=2,
                            max_requests=1, reserve_bytes=0, transport=quota)
    assert report['status'] == 'source_stopped'
    assert report['stop_reason'] == 'upstream_quota'
    assert report['coverage']['failed'] == 1 and report['coverage']['empty'] == 1
    assert CollectionGuard(store).status()['owner'][0]['occupied'] == 0
    restore(tmp_path / 'check', store)
    with sqlite3.connect(tmp_path / 'check/checkpoint.sqlite') as db:
        assert not db.execute('SELECT 1 FROM meta WHERE key=?', (STATE_KEY,)).fetchone()
    with pytest.raises(RealEstateError, match='upstream_quota'):
        run_automation(tmp_path / 'quota-next', store, 'fixture-key', as_of=STAMP, months=2,
            max_requests=1, reserve_bytes=0, transport=lambda *a, **kw: pytest.fail('same-day quota must block source'))


def test_refresh_does_not_reset_fresh_pages_and_retains_snapshot(tmp_path):
    c = collector(tmp_path / 'local', lambda *a, **kw: xml())
    c.collect('fixture-key', max_requests=1, min_interval=0)
    original = c.db.execute("SELECT snapshot FROM jobs WHERE status='empty'").fetchone()[0]
    with c.db:
        c.db.execute("UPDATE jobs SET updated_at='2026-09-01T00:00:00Z' WHERE status='empty'")
    state, lane, month = prepare_lane(c.db, STAMP, mode='recent', months=1)
    row = c.db.execute('SELECT status,snapshot FROM jobs WHERE snapshot IS NOT NULL').fetchone()
    assert tuple(row) == ('pending', original)
    finish_lane(c.db, state, lane, month)
    saved = json.loads(c.db.execute('SELECT value FROM meta WHERE key=?', (STATE_KEY,)).fetchone()[0])
    assert saved['recent_cursor'] == 0
    c.collect('fixture-key', max_requests=2, min_interval=0)
    state, lane, month = prepare_lane(c.db, STAMP, mode='recent', months=1)
    assert c.db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0] == 0
    finish_lane(c.db, state, lane, month)
    saved = json.loads(c.db.execute('SELECT value FROM meta WHERE key=?', (STATE_KEY,)).fetchone()[0])
    assert saved['recent_cursor'] == 1
    c.close()


@pytest.mark.parametrize(('runs', 'expected'), [(0, 'backfill'), (2, 'backfill'), (3, 'recent'), (27, 'history')])
def test_schedule_lanes_preserve_backfill_budget(tmp_path, runs, expected):
    c = collector(tmp_path / 'local', lambda *a, **kw: xml())
    with c.db:
        c.db.execute('INSERT INTO meta VALUES(?,?)', (STATE_KEY, json.dumps({'runs': runs, 'recent_cursor': 0, 'history_cursor': 0})))
    assert prepare_lane(c.db, STAMP, months=121)[1] == expected
    c.close()


def test_missing_credentials_fail_before_network_or_workspace(tmp_path, monkeypatch, capsys):
    for name in ('DATA_GO_KR_SERVICE_KEY', 'CLOUDFLARE_API_TOKEN', 'PROPERTY_ARCHIVE_CONFIG_JSON'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr('pipeline.property_automation.D1Archive', lambda *a, **kw: pytest.fail('network-capable object must not be constructed'))
    assert main(['--execute', '--work-parent', str(tmp_path / 'absent')]) == 1
    assert not (tmp_path / 'absent').exists()
    assert json.loads(capsys.readouterr().out)['error'] == 'automation_credentials_missing'


def test_unexpected_secret_config_is_never_logged(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('DATA_GO_KR_SERVICE_KEY', 'fixture-service-key')
    monkeypatch.setenv('CLOUDFLARE_API_TOKEN', 'fixture-private-token')
    monkeypatch.setenv('PROPERTY_ARCHIVE_CONFIG_JSON', '{secret-invalid-json')
    assert main(['--execute', '--work-parent', str(tmp_path / 'absent')]) == 1
    output = capsys.readouterr().out
    assert 'secret-invalid-json' not in output and 'fixture-private-token' not in output


def test_budget_rejected_before_remote_access(tmp_path):
    class Never:
        def head(self):
            pytest.fail('must validate before remote reads')
    with pytest.raises(RealEstateError, match='budget_invalid'):
        run_automation(tmp_path / 'absent', Never(), 'key', max_requests=501)


def test_existing_run_directory_is_not_reused(tmp_path):
    store = parent(tmp_path)
    path = tmp_path / 'exists'
    path.mkdir()
    with pytest.raises(RealEstateError, match='requires_new_directory'):
        run_automation(path, store, 'fixture-key', as_of=STAMP, months=2, reserve_bytes=0)


def test_retry_times_distinguish_archive_utc_and_provider_kst():
    from datetime import datetime, timezone
    now = datetime(2026, 9, 26, 20, 0, tzinfo=timezone.utc)
    assert retry_at('archive_daily_write_limit', now) == '2026-09-27T00:17:00Z'
    assert retry_at('upstream_quota', now) == '2026-09-27T15:17:00Z'
    assert retry_at('automation_recovery_required', now) is None


def test_workflow_executes_collector_and_cannot_expose_raw_artifacts():
    from pathlib import Path
    workflow = Path('.github/workflows/property-collect.yml').read_text(encoding='utf-8')
    assert 'pipeline.property_automation --execute' in workflow
    assert 'PROPERTY_COLLECTION_ENABLED' in workflow
    assert 'ubuntu-24.04' in workflow and 'cancel-in-progress: false' in workflow
    assert 'uses: actions/upload-artifact' not in workflow
    assert 'uses: actions/cache' not in workflow


def test_safe_retry_preserves_pages_and_snapshot_and_obeys_cooldown(tmp_path):
    c = collector(tmp_path / 'local', lambda *a, **kw: xml())
    c.collect('fixture-key', max_requests=1, min_interval=0)
    row = c.db.execute("SELECT id,pages,snapshot FROM jobs WHERE status='empty'").fetchone()
    with c.db:
        c.db.execute("UPDATE jobs SET status='failed',error_code='upstream_timeout',updated_at=? WHERE id=?",
                     ('2026-09-20T00:00:00Z', row[0]))
    first = requeue_safe_failures(c.db, '2026-09-20T00:20:00Z')
    assert first['requeued'] == 0 and first['next_retry_at'] == '2026-09-20T00:30:00Z'
    assert requeue_safe_failures(c.db, '2026-09-20T00:30:00Z')['requeued'] == 1
    current = c.db.execute('SELECT pages,snapshot FROM jobs WHERE id=?', (row[0],)).fetchone()
    assert tuple(current) == tuple(row[1:])
    c.close()


@pytest.mark.parametrize('error', ['upstream_auth', 'remote_checkpoint_error', 'checkpoint_error',
    'secret_reflection', 'credential_field_reflection', 'inconsistent_pagination', 'unknown_error'])
def test_unsafe_failure_is_never_requeued(tmp_path, error):
    c = collector(tmp_path / 'local', lambda *a, **kw: xml())
    with c.db:
        c.db.execute("UPDATE jobs SET status='failed',error_code=?,updated_at='2026-01-01T00:00:00Z'", (error,))
    assert requeue_safe_failures(c.db, STAMP)['requeued'] == 0
    assert c.db.execute("SELECT COUNT(*) FROM jobs WHERE status='failed'").fetchone()[0] == 2
    state, lane, selected = prepare_lane(c.db, STAMP, mode='recent', months=1)
    finish_lane(c.db, state, lane, selected)
    assert json.loads(c.db.execute('SELECT value FROM meta WHERE key=?', (STATE_KEY,)).fetchone()[0])['recent_cursor'] == 0
    c.close()


def test_safe_retry_has_run_and_per_day_limits(tmp_path):
    c = collector(tmp_path / 'local', lambda *a, **kw: xml())
    with c.db:
        c.db.execute("UPDATE jobs SET status='failed',error_code='upstream_network',updated_at='2026-09-19T00:00:00Z'")
    assert requeue_safe_failures(c.db, STAMP, limit=1)['requeued'] == 1
    assert c.db.execute("SELECT COUNT(*) FROM jobs WHERE status='failed'").fetchone()[0] == 1
    job = c.db.execute('SELECT job_id FROM property_automation_retries').fetchone()[0]
    for stamp in ('2026-09-20T01:00:00Z', '2026-09-20T02:00:00Z'):
        with c.db:
            c.db.execute("UPDATE jobs SET status='failed',error_code='upstream_network' WHERE id=?", (job,))
        assert requeue_safe_failures(c.db, stamp, limit=1)['requeued'] == 1
    with c.db:
        c.db.execute("UPDATE jobs SET status='failed',error_code='upstream_network' WHERE id=?", (job,))
        c.db.execute("UPDATE jobs SET status='complete' WHERE id!=?", (job,))
    blocked = requeue_safe_failures(c.db, '2026-09-20T03:00:00Z', limit=1)
    assert blocked['requeued'] == 0 and blocked['next_retry_at'] == '2026-09-20T15:17:00Z'
    assert requeue_safe_failures(c.db, '2026-09-20T15:17:00Z', limit=1)['requeued'] == 1
    c.close()


def test_provider_quota_retry_waits_for_next_kst_day_plus_margin(tmp_path):
    c = collector(tmp_path / 'local', lambda *a, **kw: xml())
    with c.db:
        c.db.execute("UPDATE jobs SET status='failed',error_code='upstream_quota',updated_at='2026-09-20T14:00:00Z'")
    assert requeue_safe_failures(c.db, '2026-09-20T15:10:00Z')['requeued'] == 0
    assert requeue_safe_failures(c.db, '2026-09-20T15:17:00Z')['requeued'] == 2
    c.close()


def test_legacy_timeout_retry_is_durable_in_verified_remote_head(tmp_path):
    c = collector(tmp_path / 'legacy', lambda *a, **kw: xml())
    c.collect('fixture-key', max_requests=1, min_interval=0)
    with c.db:
        c.db.execute("UPDATE jobs SET status='failed',error_code='upstream_timeout',updated_at='2026-09-19T00:00:00Z' WHERE status='pending'")
    c.close()
    store = LocalD1()
    backup(tmp_path / 'legacy', store)
    result = run_automation(tmp_path / 'run', store, 'fixture-key', as_of=STAMP, months=2,
                            max_requests=4, reserve_bytes=0, transport=lambda *a, **kw: xml())
    assert result['retry_policy']['requeued'] == 1
    assert result['coverage']['failed'] == 0
    restore(tmp_path / 'restored', store)
    with sqlite3.connect(tmp_path / 'restored/checkpoint.sqlite') as db:
        assert db.execute('SELECT requeues,error_code FROM property_automation_retries').fetchall() == [(1, 'upstream_timeout')]
        assert db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 4


def test_http_success_xml_quota_blocks_next_run_before_source_call(tmp_path):
    store = parent(tmp_path)
    calls = []
    def quota(*args, **kwargs):
        calls.append(1)
        return xml(code='22')
    report = run_automation(tmp_path / 'xml-quota', store, 'fixture-key', as_of=STAMP, months=2,
                            max_requests=1, reserve_bytes=0, transport=quota)
    assert report['stop_reason'] == 'upstream_quota'
    assert CollectionGuard(store).query("SELECT phase,error_code FROM collection_reservations")['results'] == [
        {'phase': 'stored', 'error_code': None}]
    with pytest.raises(RealEstateError, match='upstream_quota'):
        run_automation(tmp_path / 'blocked', store, 'fixture-key', as_of=STAMP, months=2,
                        max_requests=1, reserve_bytes=0, transport=quota)
    assert calls == [1]


def page_transport(calls):
    def fetch(key, trade, code, month, page, size, **kwargs):
        calls.append((trade, page))
        return xml([rent() for _ in range(1000 if page == 1 else 1)], page=page, total=1001)
    return fetch


def bounded_collect(c, stamp, *, requests, byte_limit):
    pagination_budget_preflight(c.db, stamp, max_requests=requests, max_bytes=byte_limit)
    start = c.db.execute('SELECT COALESCE(MAX(id),0) FROM calls').fetchone()[0]
    result = c.collect('fixture-key', max_requests=requests, max_bytes=byte_limit, min_interval=0)
    record_pagination_budget(c.db, start, result)
    return result


@pytest.mark.parametrize(('requests','byte_limit'), [(1, 16*1024**2), (25, 8*1024**2)])
def test_stale_exclusive_partial_stops_instead_of_repeating_first_page(tmp_path, requests, byte_limit):
    stamp = [STAMP]
    calls = []
    c = collector(tmp_path / 'local', page_transport(calls), clock=lambda: stamp[0])
    assert bounded_collect(c, stamp[0], requests=requests, byte_limit=byte_limit)['stop_reason'] == 'run_budget'
    stamp[0] = '2026-09-20T04:00:00Z'
    with pytest.raises(RealEstateError, match='automation_pagination_budget_insufficient'):
        bounded_collect(c, stamp[0], requests=requests, byte_limit=byte_limit)
    assert calls == [('rent', 1)]
    # Increasing the bottleneck budget deliberately permits a new complete pass.
    bounded_collect(c, stamp[0], requests=2, byte_limit=16*1024**2)
    assert calls == [('rent', 1), ('rent', 1), ('rent', 2)]
    assert c.summary()['complete'] == 1
    c.close()


def test_valid_partial_pages_resume_without_requiring_full_job_request_budget(tmp_path):
    stamp = [STAMP]
    calls = []
    c = collector(tmp_path / 'local', page_transport(calls), clock=lambda: stamp[0])
    bounded_collect(c, stamp[0], requests=1, byte_limit=8*1024**2)
    stamp[0] = '2026-09-20T00:05:00Z'
    bounded_collect(c, stamp[0], requests=1, byte_limit=8*1024**2)
    assert calls == [('rent', 1), ('rent', 2)]
    assert c.summary()['complete'] == 1
    assert c.db.execute('SELECT COUNT(*) FROM property_automation_pagination').fetchone()[0] == 0
    c.close()


def test_partial_after_other_job_may_retry_with_full_next_run_budget(tmp_path):
    stamp = [STAMP]
    calls = []
    transport = page_transport(calls)
    def mixed(key, trade, *args, **kwargs):
        if trade == 'rent':
            calls.append((trade, 1))
            return xml()
        return transport(key, trade, *args, **kwargs)
    c = collector(tmp_path / 'local', mixed, clock=lambda: stamp[0])
    bounded_collect(c, stamp[0], requests=2, byte_limit=16*1024**2)
    assert c.db.execute('SELECT COUNT(*) FROM property_automation_pagination').fetchone()[0] == 0
    stamp[0] = '2026-09-20T04:00:00Z'
    bounded_collect(c, stamp[0], requests=2, byte_limit=16*1024**2)
    assert calls == [('rent', 1), ('sale', 1), ('sale', 1), ('sale', 2)]
    c.close()


def test_pagination_evidence_survives_remote_head_and_blocks_with_owner_released(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    store = parent(tmp_path)
    calls = []
    result = run_automation(tmp_path / 'first', store, 'fixture-key', as_of=STAMP, months=2,
        max_requests=1, reserve_bytes=0, transport=page_transport(calls))
    assert result['stop_reason'] == 'run_budget'
    before = store.head()
    future = (datetime.now(timezone.utc) + timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
    monkeypatch.setattr('pipeline.property_automation.instant', lambda: future)
    with pytest.raises(RealEstateError, match='automation_pagination_budget_insufficient'):
        run_automation(tmp_path / 'second', store, 'fixture-key', as_of=STAMP, months=2,
            max_requests=1, reserve_bytes=0, transport=page_transport(calls))
    assert len(calls) == 1 and store.head() == before
    assert CollectionGuard(store).status()['owner'][0]['occupied'] == 0
