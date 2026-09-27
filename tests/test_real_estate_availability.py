import http.client
import json
import sqlite3

import pytest

from pipeline.real_estate import RealEstateError, canonical_bytes
from pipeline.real_estate_availability import before_source, source_policy
from pipeline.real_estate_fetch import Collector, fetch_page, month_sequence
from pipeline.real_estate_plan import build_plan, load_plan_registry, MAX_PLAN_BYTES
from pipeline.real_estate_publish import publish
from pipeline.property_automation import acquisition_progress, prepare_lane, finish_lane, run_automation
from pipeline.real_estate_archive import backup, restore
from pipeline.real_estate_run_guard import CollectionGuard
from test_real_estate_archive import LocalD1
from test_real_estate_fetch import registry, xml, STAMP, KEY
from test_real_estate_plan import ROOT
from test_real_estate_publish import read_asset


@pytest.mark.parametrize(('stamp','oldest'), [
    (STAMP, '200609'), ('2025-12-31T15:00:00Z', '200601'),
    ('2026-12-31T15:00:00Z', '200701'),
])
def test_twenty_completed_years_and_current_kst_month(stamp, oldest):
    months = month_sequence(stamp)
    assert len(months) == len(set(months)) == 241
    assert months[-1] == oldest
    for index in range(2, len(months)):
        def serial(month): return int(month[:4])*12 + int(month[4:])
        previous = months[0] if index == 2 else months[index-1]
        assert serial(previous) - serial(months[index]) == 1
    with pytest.raises(RealEstateError, match='invalid_plan_window'):
        month_sequence('0001-01-01T00:00:00Z')


@pytest.mark.parametrize('kind', ['apartment', 'officetel'])
@pytest.mark.parametrize(('trade','last_unavailable','first'), [
    ('sale', '200512', '200601'), ('rent', '201012', '201101'),
])
def test_official_floor_rejects_before_network(kind, trade, last_unavailable, first, monkeypatch):
    assert before_source(kind, trade, last_unavailable)
    assert not before_source(kind, trade, first)
    def forbidden(*args, **kwargs): raise AssertionError('Unexpected network access')
    monkeypatch.setattr(http.client, 'HTTPSConnection', forbidden)
    with pytest.raises(RealEstateError, match='before_source_start'):
        fetch_page(KEY, trade, '11110', last_unavailable, 1, 1000, property_type=kind,
                   timeout=10, max_bytes=8*1024**2)
    assert source_policy(kind)['scope'] == 'publication_lower_bound_not_verified_api_completeness'


def test_national_plan_fits_cap_and_excludes_unavailable_from_collectable_count():
    reg, digest = load_plan_registry(ROOT)
    plan = build_plan(reg, registry_sha256=digest, as_of=STAMP)
    assert plan['job_count'] == 123392
    assert plan['eligible_job_count'] == 110080
    assert plan['source_unavailable_job_count'] == 13312
    assert len(canonical_bytes(plan)) <= MAX_PLAN_BYTES
    assert plan['source_calls'] == 0 and not plan['data_acquired']


def test_legacy_ten_year_checkpoint_expansion_preserves_every_record_and_original(tmp_path):
    c = Collector(tmp_path, registry(), months=121, clock=lambda: STAMP,
                  reserve_bytes=0, transport=lambda *a, **kw: xml())
    c.collect(KEY, max_requests=2, min_interval=0)
    old_jobs = [tuple(row) for row in c.db.execute('SELECT * FROM jobs ORDER BY id')]
    old_calls = [tuple(row) for row in c.db.execute('SELECT * FROM calls')]
    originals = {p: p.read_bytes() for folder in ('raw', 'snapshots') for p in (tmp_path/folder).rglob('*') if p.is_file()}
    c.close()
    c = Collector(tmp_path, registry(), clock=lambda: STAMP, reserve_bytes=0, advance_window=True)
    assert c.summary()['expected'] == 482
    assert c.summary()['source_unavailable'] == 52
    for row in old_jobs:
        assert tuple(c.db.execute('SELECT * FROM jobs WHERE id=?', (row[0],)).fetchone()) == row
    assert [tuple(row) for row in c.db.execute('SELECT * FROM calls')] == old_calls
    assert all(path.read_bytes() == data for path, data in originals.items())
    progress = acquisition_progress(c.db)
    assert progress['eligible_jobs'] == 430 and progress['missing_collectable_jobs'] == 428
    assert progress['verified_snapshot_jobs'] == 2
    c.close()
    c = Collector(tmp_path, registry(), clock=lambda: '2026-10-01T00:00:00Z', reserve_bytes=0, advance_window=True)
    assert c.summary()['expected'] == 484  # Preserve the oldest month on rollover.
    assert c.summary()['source_unavailable'] == 52
    c.close()


def test_unavailable_is_never_empty_or_a_refresh_target_and_publishes_null(tmp_path):
    calls = []
    def source(key, trade, region, month, *a, **kw):
        calls.append((trade, month)); return xml()
    root = tmp_path/'collection'
    c = Collector(root, registry(), months=1, clock=lambda: '2010-12-20T00:00:00Z',
                  reserve_bytes=0, transport=source)
    report = c.collect(KEY, max_requests=100, min_interval=0)
    assert report['requests'] == 1 and calls == [('sale', '201012')]
    assert c.summary()['empty'] == 1 and c.summary()['source_unavailable'] == 1
    assert c.db.execute("SELECT COUNT(*) FROM calls WHERE trade_type='rent'").fetchone()[0] == 0
    state, lane, selected = prepare_lane(c.db, '2010-12-31T00:00:00Z', mode='recent', months=1)
    finish_lane(c.db, state, lane, selected)
    assert c.db.execute("SELECT status,snapshot,pages FROM jobs WHERE trade_type='rent'").fetchone()[:] == ('source_unavailable', None, '[]')
    c.close()
    result = publish(root, registry(), tmp_path/'candidate')
    base = tmp_path/'candidate'/result['release_id']
    manifest = json.loads((base/result['property_release']['path']).read_bytes())
    assert manifest['coverage']['expected'] == 2 and manifest['coverage']['source_unavailable'] == 1
    region = read_asset(base, read_asset(base, manifest['regions'])['regions'][0]['index'])
    partition = next(row for row in region['partitions'] if row['trade_type'] == 'rent')
    assert partition['status'] == 'source_unavailable' and partition['error_code'] == 'before_source_start'
    assert partition['transactions'] == []
    assert all(partition[key] is None for key in ('source_rows', 'eligible_rows', 'retrieved_at'))
    metric = next(row for row in region['metrics'] if row['trade_type'] == 'rent')
    assert metric['median_price_per_m2_krw'] is None and metric['source_rows'] is None


def test_remote_twenty_year_migration_roundtrip_preserves_guard_and_budget(tmp_path):
    c = Collector(tmp_path/'old', registry(), months=121, clock=lambda: STAMP,
                  reserve_bytes=0, transport=lambda *a, **kw: xml())
    c.collect(KEY, max_requests=1, min_interval=0); c.close()
    store = LocalD1(); backup(tmp_path/'old', store)
    calls = []
    def source(*args, **kwargs): calls.append(1); return xml()
    report = run_automation(tmp_path/'run', store, KEY, as_of=STAMP, max_requests=1,
                            max_bytes=64*1024**2, reserve_bytes=0, transport=source)
    assert report['requests'] == 1 and len(calls) == 1
    assert report['coverage']['expected'] == 482 and report['coverage']['source_unavailable'] == 52
    assert report['acquisition']['verified_snapshot_jobs'] == 2
    assert report['acquisition']['missing_collectable_jobs'] == 428
    assert CollectionGuard(store).status()['owner'][0]['occupied'] == 0
    restore(tmp_path/'restored', store)
    with sqlite3.connect(tmp_path/'restored/checkpoint.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE status='source_unavailable' AND snapshot IS NULL").fetchone()[0] == 52


def test_all_pre_source_jobs_cannot_reserve_calls_even_if_legacy_status_is_pending(tmp_path):
    def forbidden(*args, **kwargs): raise AssertionError('No official source exists in this month')
    c = Collector(tmp_path, registry(), months=1, clock=lambda: '2005-12-20T00:00:00Z',
                  reserve_bytes=0, transport=forbidden)
    assert c.summary()['source_unavailable'] == 2
    with c.db:
        c.db.execute("UPDATE jobs SET status='pending',error_code=NULL")
    result = c.collect(KEY, max_requests=100, min_interval=0)
    assert result['requests'] == 0 and result['stop_reason'] == 'work_complete'
    assert c.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 0
    c.close()


def test_scheduled_defaults_do_not_raise_the_verified_free_budget():
    import inspect
    from pipeline.real_estate_publish import MAX_LEDGER_JOBS
    signature = inspect.signature(run_automation)
    assert signature.parameters['months'].default == 241
    assert signature.parameters['max_requests'].default == 100
    assert signature.parameters['max_bytes'].default == 64*1024**2
    assert 123392 < MAX_LEDGER_JOBS == 256000
    workflow = (ROOT/'.github/workflows/property-collect.yml').read_text(encoding='utf-8')
    assert '--months 241' in workflow
    assert "PROPERTY_COLLECTION_MAX_REQUESTS || '100'" in workflow
    assert "PROPERTY_COLLECTION_MAX_BYTES || '67108864'" in workflow
