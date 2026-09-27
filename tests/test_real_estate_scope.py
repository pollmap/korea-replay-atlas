from copy import deepcopy
import json
import sqlite3

import pytest

from pipeline.real_estate import RealEstateError
from pipeline.real_estate_scope import CODE_GROUPS, FOCUS_RANKS, FOCUS_POLICY, selected_regions, scope_summary
from pipeline.real_estate_fetch import Collector
from pipeline.real_estate_plan import load_plan_registry, build_plan
from pipeline.real_estate_archive import backup, restore
from pipeline.real_estate_run_guard import CollectionGuard
from pipeline.property_automation import (STATE_KEY, run_automation, prepare_lane, finish_lane,
    requeue_safe_failures, pagination_budget_preflight, acquisition_progress)
from test_real_estate_fetch import registry, xml, STAMP, KEY
from test_real_estate_plan import ROOT
from test_real_estate_archive import LocalD1


SCOPE = 'priority-nine'
OUTSIDE = ('44150', '50110')
ORDER = ('41111', '11110', '28125', '44131', '44200', '36110', '43111', '30110', '26110')


def fixture_registry():
    result = deepcopy(registry())
    official, _ = load_plan_registry(ROOT)
    result['regions'] = [row for row in official['regions'] if row['lawd_code'] in (*ORDER, *OUTSIDE)]
    return result


def test_pinned_scope_matches_official_current_codes_and_exact_nine_region_order():
    reg, digest = load_plan_registry(ROOT)
    actual = {r['lawd_code']: r for r in reg['regions']}
    assert len(FOCUS_RANKS) == 112 and set(FOCUS_RANKS) <= set(actual)
    assert [(name, len(codes)) for name, codes in CODE_GROUPS] == [
        ('경기',47), ('서울',25), ('인천',11), ('천안',2), ('아산',1),
        ('세종',1), ('청주',4), ('대전',5), ('부산',16)]
    for prefix, name in [('41','경기'), ('11','서울'), ('28','인천'), ('30','대전'), ('26','부산')]:
        assert set(dict(CODE_GROUPS)[name]) == {code for code in actual if code[:2] == prefix}
    expected_city_codes = {'천안': ('44131','44133'), '아산': ('44200',),
                           '세종': ('36110',), '청주': ('43111','43112','43113','43114')}
    for name, codes in expected_city_codes.items():
        assert dict(CODE_GROUPS)[name] == codes
    # Names cannot promote an unrelated or invented code.
    with pytest.raises(RealEstateError, match='collection_scope_empty'):
        selected_regions([{'lawd_code':'44150','name':'경기도 서울 인천 천안 아산 청주 부산'}], SCOPE)
    plan = build_plan(reg, registry_sha256=digest, as_of=STAMP, scope=SCOPE)
    assert plan['region_order'] == FOCUS_POLICY
    assert plan['region_count'] == 112 and plan['scope']['registry_region_count'] == 256
    assert plan['job_count'] == 53984
    assert plan['source_unavailable_job_count'] == 5824
    assert plan['eligible_job_count'] == 48160
    assert plan['scope']['expected_region_count'] == 112 and plan['scope']['missing_codes'] == []
    ranks = [FOCUS_RANKS[row['lawd_code']] for row in plan['jobs']]
    assert ranks == sorted(ranks) and set(ranks) == set(range(9))


def test_collector_nine_order_does_not_touch_existing_outside_jobs(tmp_path):
    calls = []
    def source(key, trade, code, month, *args, **kwargs):
        calls.append((code, trade)); return xml()
    c = Collector(tmp_path, fixture_registry(), months=1, clock=lambda: STAMP,
                  reserve_bytes=0, transport=source, scope=SCOPE)
    before = [tuple(r) for r in c.db.execute("SELECT * FROM jobs WHERE lawd_code IN ('44150','50110') ORDER BY id")]
    result = c.collect(KEY, max_requests=100, min_interval=0)
    assert calls == [(code, trade) for code in ORDER for trade in ('rent','sale')]
    assert result['requests'] == 18 and result['region_order'] == FOCUS_POLICY
    assert result['coverage']['expected'] == 22 and result['scope_coverage']['expected'] == 18
    assert [tuple(r) for r in c.db.execute("SELECT * FROM jobs WHERE lawd_code IN ('44150','50110') ORDER BY id")] == before
    assert c.reprocess()['jobs'] == 18
    # The original nationwide option deliberately remains available.
    c.close()
    c = Collector(tmp_path, fixture_registry(), months=1, clock=lambda: STAMP,
                  reserve_bytes=0, transport=source, scope='nationwide')
    assert c.collect(KEY, max_requests=100, min_interval=0)['requests'] == 4
    c.close()


@pytest.mark.parametrize(('mode','runs'), [('backfill',0),('recent',0),('history',0),('auto',0),('auto',3),('auto',27)])
def test_all_remote_lanes_limit_requests_refreshes_and_retries_and_preserve_outside(tmp_path, mode, runs):
    fixture = fixture_registry()
    fixture['regions'] = [row for row in fixture['regions'] if row['lawd_code'] in ('41111', '50110')]
    c = Collector(tmp_path/'seed', fixture, months=4, clock=lambda: STAMP,
                  reserve_bytes=0, transport=lambda *a, **kw: xml())
    c.collect(KEY, max_requests=100, min_interval=0)
    with c.db:
        c.db.execute("UPDATE jobs SET status='pending',snapshot=NULL WHERE lawd_code NOT IN ('44150','50110')")
        c.db.execute("UPDATE jobs SET status='failed',error_code='upstream_timeout',updated_at='2026-01-01T00:00:00Z' WHERE lawd_code IN ('44150','50110')")
        c.db.execute('INSERT INTO meta VALUES(?,?)', (STATE_KEY, json.dumps({'runs':runs,'recent_cursor':0,'history_cursor':0})))
    outside = [tuple(r) for r in c.db.execute("SELECT * FROM jobs WHERE lawd_code IN ('44150','50110') ORDER BY id")]
    prior_calls = [tuple(r) for r in c.db.execute('SELECT * FROM calls ORDER BY id')]
    c.close()
    store = LocalD1(); backup(tmp_path/'seed', store)
    calls = []
    def source(key, trade, code, month, *args, **kwargs):
        calls.append((code, month)); return xml()
    report = run_automation(tmp_path/'run', store, KEY, as_of=STAMP, mode=mode, months=4,
                            max_requests=2, reserve_bytes=0, transport=source, scope=SCOPE)
    assert len(calls) == 2 and all(code in FOCUS_RANKS for code, _ in calls)
    assert report['scope']['id'] == SCOPE
    assert report['scope_acquisition']['expected_jobs'] == 8
    assert report['acquisition']['expected_jobs'] == 16
    assert report['retry_policy']['requeued'] == 0
    assert CollectionGuard(store).status()['owner'][0]['occupied'] == 0
    restore(tmp_path/'restored', store)
    with sqlite3.connect(tmp_path/'restored/checkpoint.sqlite') as db:
        assert db.execute("SELECT * FROM jobs WHERE lawd_code IN ('44150','50110') ORDER BY id").fetchall() == outside
        assert db.execute('SELECT * FROM calls ORDER BY id LIMIT ?', (len(prior_calls),)).fetchall() == prior_calls


def test_outside_failures_and_pagination_never_block_scoped_cursor_or_retry(tmp_path):
    c = Collector(tmp_path, fixture_registry(), months=1, clock=lambda: STAMP,
                  reserve_bytes=0, transport=lambda *a, **kw: xml(), scope=SCOPE)
    c.collect(KEY, max_requests=100, min_interval=0)
    with c.db:
        c.db.execute("UPDATE jobs SET status='failed',error_code='upstream_timeout',updated_at='2026-01-01T00:00:00Z' WHERE lawd_code IN ('44150','50110')")
    assert requeue_safe_failures(c.db, STAMP, scope=SCOPE)['requeued'] == 0
    state, lane, selected = prepare_lane(c.db, STAMP, mode='recent', months=1, scope=SCOPE)
    finish_lane(c.db, state, lane, selected, scope=SCOPE)
    assert json.loads(c.db.execute('SELECT value FROM meta WHERE key=?', (STATE_KEY,)).fetchone()[0])['recent_cursor'] == 1
    pagination_budget_preflight(c.db, STAMP, max_requests=100, max_bytes=64*1024**2, scope=SCOPE)
    with c.db:
        c.db.execute("UPDATE jobs SET status='partial',pages=? WHERE id='rent/50110/202609'",
                     (json.dumps([{'sha256':'a'*64,'retrieved_at':'2026-01-01T00:00:00Z'}]),))
        c.db.execute('INSERT INTO property_automation_pagination VALUES(?,?,?,?)', ('rent/50110/202609','a'*64,501,65*1024**2))
    pagination_budget_preflight(c.db, STAMP, max_requests=100, max_bytes=64*1024**2, scope=SCOPE)
    with pytest.raises(RealEstateError, match='automation_pagination_budget_insufficient'):
        pagination_budget_preflight(c.db, STAMP, max_requests=100, max_bytes=64*1024**2)
    assert acquisition_progress(c.db, scope=SCOPE)['expected_jobs'] == 18
    c.close()


def test_unknown_scope_fails_before_remote_work_and_workflows_are_explicit(tmp_path):
    with pytest.raises(RealEstateError, match='invalid_collection_scope'):
        run_automation(tmp_path, object(), KEY, scope='guess-nearby')
    workflow = (ROOT/'.github/workflows/property-collect.yml').read_text(encoding='utf-8')
    assert '--scope priority-nine' in workflow and '--months 241' in workflow
    assert '--require-scope-complete' in workflow
    plan = (ROOT/'.github/workflows/property-plan.yml').read_text(encoding='utf-8')
    assert 'default: priority-nine' in plan and "'--scope', os.environ['SCOPE_INPUT']" in plan
    assert "'--require-scope-complete'" in plan


def test_incomplete_operating_registry_blocks_with_no_source_calls_and_normal_release(tmp_path):
    c = Collector(tmp_path/'seed', registry(), months=1, clock=lambda: STAMP,
                  reserve_bytes=0, transport=lambda *a, **kw: xml())
    c.close()
    store = LocalD1(); backup(tmp_path/'seed', store)
    before = store.head()
    calls = []
    with pytest.raises(RealEstateError, match='collection_scope_registry_incomplete'):
        run_automation(tmp_path/'run', store, KEY, as_of=STAMP, months=1,
            reserve_bytes=0, scope=SCOPE, require_scope_complete=True,
            transport=lambda *a, **kw: calls.append(1))
    assert calls == [] and store.head() == before
    assert CollectionGuard(store).status()['owner'][0]['occupied'] == 0
    summary = scope_summary(registry()['regions'], SCOPE)
    assert summary['region_count'] == 1 and summary['expected_region_count'] == 112
    assert len(summary['missing_codes']) == 111


def test_stale_completed_outside_snapshots_are_not_reset_by_any_refresh_path(tmp_path):
    fixture = fixture_registry()
    fixture['regions'] = [row for row in fixture['regions'] if row['lawd_code'] in ('41111', '50110')]
    c = Collector(tmp_path, fixture, months=1, clock=lambda: STAMP,
                  reserve_bytes=0, transport=lambda *a, **kw: xml())
    c.collect(KEY, max_requests=4, min_interval=0)
    with c.db:
        c.db.execute("UPDATE jobs SET updated_at='2026-01-01T00:00:00Z'")
    before = [tuple(r) for r in c.db.execute("SELECT * FROM jobs WHERE lawd_code='50110' ORDER BY id")]
    c.close()
    calls = []
    def source(key, trade, code, month, *a, **kw):
        calls.append(code); return xml()
    c = Collector(tmp_path, fixture, months=1, clock=lambda: STAMP,
                  reserve_bytes=0, transport=source, scope=SCOPE)
    prepare_lane(c.db, STAMP, mode='recent', months=1, scope=SCOPE)
    assert c.db.execute("SELECT COUNT(*) FROM jobs WHERE lawd_code='41111' AND status='pending' AND snapshot IS NOT NULL").fetchone()[0] == 2
    assert [tuple(r) for r in c.db.execute("SELECT * FROM jobs WHERE lawd_code='50110' ORDER BY id")] == before
    result = c.collect(KEY, max_requests=100, min_interval=0, refresh=True, retry_failed=True)
    assert result['requests'] == 2 and calls == ['41111','41111']
    assert [tuple(r) for r in c.db.execute("SELECT * FROM jobs WHERE lawd_code='50110' ORDER BY id")] == before
    assert c.reprocess()['jobs'] == 2
    assert [tuple(r) for r in c.db.execute("SELECT * FROM jobs WHERE lawd_code='50110' ORDER BY id")] == before
    c.close()
