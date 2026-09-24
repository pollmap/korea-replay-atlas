from copy import deepcopy
from pathlib import Path

from pipeline.real_estate_priority import region_priority, priority_map, POLICY_ID, GROUPS
from pipeline.real_estate_plan import load_plan_registry, build_plan
from pipeline.real_estate_fetch import Collector
from test_real_estate_fetch import registry, xml, STAMP, KEY

ROOT = Path(__file__).resolve().parents[1]


def test_current_registry_covers_every_region_once_with_requested_tiers():
    current, digest = load_plan_registry(ROOT)
    ranks = priority_map(current['regions'])
    assert len(ranks) == len(current['regions']) == 256
    for row in current['regions']:
        if row['lawd_code'].startswith(('11', '28')):
            assert ranks[row['lawd_code']] == 0
        if row['name'].startswith('경기도 수원시'):
            assert ranks[row['lawd_code']] == 1
        if row['name'].startswith('경기도 남양주시'):
            assert ranks[row['lawd_code']] == 5
        if '청주시' in row['name']:
            assert ranks[row['lawd_code']] == 4
    plan = build_plan(current, registry_sha256=digest, as_of=STAMP, months=121)
    assert plan['region_order'] == POLICY_ID
    assert len(plan['jobs']) == 61952
    tiers = [ranks[j['lawd_code']] for j in plan['jobs']]
    assert tiers == sorted(tiers)
    first_month = [j['lawd_code'][:2] for j in plan['jobs'] if ranks[j['lawd_code']] == 0 and j['deal_month'] == '202608']
    assert set(first_month) == {'11', '28'}
    assert set(tiers) == set(range(len(GROUPS)))


def test_region_names_do_not_promote_homonymous_cities_outside_the_province():
    assert region_priority({'lawd_code':'29110','name':'광주광역시 동구'}) == 7
    assert region_priority({'lawd_code':'41610','name':'경기도 광주시'}) == 1
    assert region_priority({'lawd_code':'50110','name':'제주특별자치도 제주시'}) == 7


def test_collector_resumes_with_new_order_without_resetting_completed_jobs(tmp_path):
    # Multi-region scheduling fixture; no network or official-key access.
    fixture = deepcopy(registry())
    fixture['regions'] = [
        {'lawd_code':code,'name':name} for code,name in [
            ('26110','부산광역시 중구'), ('43111','충청북도 청주시 상당구'),
            ('28110','인천광역시 중구'), ('41111','경기도 수원시 장안구'),
            ('11110','서울특별시 종로구'), ('30110','대전광역시 동구'),
            ('36110','세종특별자치시'), ('41360','경기도 남양주시'),
            ('50110','제주특별자치도 제주시')]]
    calls = []
    def transport(trade,key,code,month,*args,**kwargs):
        calls.append((code,month,trade))
        return xml()
    kwargs = dict(months=2,as_of=STAMP,clock=lambda:STAMP,reserve_bytes=0,transport=transport)
    c = Collector(tmp_path,fixture,**kwargs)
    report = c.collect(KEY,max_requests=9,min_interval=0)
    assert report['region_order'] == POLICY_ID
    assert [r[0] for r in calls[:4]] == ['11110','11110','28110','28110']
    assert all(code[:2] in ('11','28') for code,month,trade in calls[:8])
    assert calls[8][0] == '41111'
    completed = [tuple(r) for r in c.db.execute("SELECT id,status,pages,snapshot FROM jobs WHERE status='empty' ORDER BY id")]
    c.close()
    c = Collector(tmp_path,fixture,**kwargs)
    assert [tuple(r) for r in c.db.execute("SELECT id,status,pages,snapshot FROM jobs WHERE status='empty' ORDER BY id")] == completed
    c.collect(KEY,max_requests=1,min_interval=0)
    assert calls[-1][0] == '41111'
    assert c.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 10
    c.close()
