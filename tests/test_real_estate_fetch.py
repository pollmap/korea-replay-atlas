from copy import deepcopy
import io
import json
from pathlib import Path
import time
import zipfile
import xml.etree.ElementTree as ET

import pytest

from pipeline.real_estate import RealEstateError, normalize_xml_page, build_partitions
from pipeline.real_estate_fetch import Collector, assert_no_secret, month_sequence, immutable
from pipeline.real_estate_regions import registry_from_zip, resolve_legal_dong

STAMP='2026-09-20T00:00:00Z'
KEY='fixture-not-a-real-key'


def official_zip():
    source=('법정동코드\t법정동명\t폐지여부\n'
        '1100000000\t서울특별시\t존재\n1111000000\t서울특별시 종로구\t존재\n'
        '1111010100\t서울특별시 종로구 검증동\t존재\n'
        '1199900000\t폐지시\t폐지\n1199910100\t폐지시 검증동\t폐지\n')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr(zipfile.ZipInfo('법정동코드 전체자료.txt',(2020,1,1,0,0,0)),source.encode('cp949'))
    return out.getvalue()


def registry():return registry_from_zip(official_zip(),retrieved_at=STAMP)


def xml(rows=(),page=1,size=1000,total=None,code='000'):
    root=ET.Element('response');header=ET.SubElement(root,'header')
    ET.SubElement(header,'resultCode').text=code
    body=ET.SubElement(root,'body');items=ET.SubElement(body,'items')
    for row in rows:
        item=ET.SubElement(items,'item')
        for name,value in row.items():ET.SubElement(item,name).text=value
    for name,value in {'pageNo':page,'numOfRows':size,'totalCount':len(rows) if total is None else total}.items():
        ET.SubElement(body,name).text=str(value)
    return ET.tostring(root,encoding='utf-8')


def rent(**changes):
    return dict(sggCd='11110',umdNm='검증동',aptNm='가상단지',jibun='1-2',excluUseAr='84.9900',
        dealYear='2026',dealMonth='9',dealDay='1',deposit='10,000',monthlyRent='0',floor='2',buildYear='2000',**changes)


def collector(tmp_path,transport,clock=lambda:STAMP):
    return Collector(tmp_path,registry(),months=1,clock=clock,transport=transport,reserve_bytes=0)


def test_month_ledger_sixty_completed_plus_current_latest_completed_first():
    months=month_sequence(STAMP)
    assert len(months)==61 and len(set(months))==61
    assert months[:3]==['202608','202609','202607'] and months[-1]=='202109'
    assert month_sequence('2025-12-31T16:00:00Z',2)==['202512','202601']


def test_ten_year_window_covers_120_completed_months_plus_current():
    months=month_sequence(STAMP,121)
    assert len(months)==len(set(months))==121
    assert months[:3]==['202608','202609','202607'] and months[-1]=='201609'
    with pytest.raises(RealEstateError,match='invalid_month_count'):
        month_sequence(STAMP,122)


def test_registry_official_header_abolished_and_no_gps_name_guess():
    r=registry()
    assert r['audit']['active_regions']==1 and r['retired_region_prefixes']==['11999']
    assert resolve_legal_dong(r,'11110',None,'검증동')=='1111010100'
    assert resolve_legal_dong(r,'11110','1199910100','검증동') is None
    duplicate=deepcopy(r);duplicate['legal_dongs'].append({'code':'1111010200','name':'서울특별시 종로구 검증동','status':'active'})
    assert resolve_legal_dong(duplicate,'11110',None,'검증동') is None


def test_rent_units_missing_identity_and_unavailable_cancellation():
    record=normalize_xml_page(xml([rent()]),lawd_code='11110',deal_month='202609',retrieved_at=STAMP,trade_type='rent')['records'][0]
    assert record['deposit_krw']==100_000_000 and record['monthly_rent_krw']==0
    assert record['quality']=='valid' and record['complex_id'] is None and record['position'] is None
    assert record['cancellation']['status']=='not_provided' and record['price_krw'] is None
    assert record['provenance']['dataset_id']=='15126474'
    assert record['reported_at'] is None and record['registration_date'] is None


@pytest.mark.parametrize('field,value',[('deposit','-1'),('monthlyRent','1.5'),('preDeposit','1,22')])
def test_rent_invalid_money_is_not_zero(field,value):
    row=rent();row[field]=value
    record=normalize_xml_page(xml([row]),lawd_code='11110',deal_month='202609',retrieved_at=STAMP,trade_type='rent')['records'][0]
    assert record['quality']=='invalid'


def test_empty_success_checkpoint_resumes_without_requests(tmp_path):
    calls=[]
    def transport(*args,**kwargs):calls.append(args);return xml()
    c=collector(tmp_path,transport)
    first=c.collect(KEY,max_requests=2,min_interval=0)
    assert first['coverage']['empty']==2 and len(calls)==2
    assert c.collect(KEY,max_requests=2,min_interval=0)['requests']==0
    assert c.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]==2
    c.close()


def test_provider_auth_stops_and_is_never_empty(tmp_path):
    c=collector(tmp_path,lambda *a,**k:xml(code='30'))
    result=c.collect(KEY,min_interval=0)
    assert result['requests']==1 and result['stop_reason']=='upstream_auth'
    assert result['coverage']['failed']==1 and result['coverage']['empty']==0
    assert c.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]==0
    c.close()


def test_reserved_requests_survive_quota_and_resume(tmp_path):
    c=collector(tmp_path,lambda *a,**k:xml())
    c.collect(KEY,max_requests=1,min_interval=0)
    # Refresh revisits the same source, so the previous reservation consumes its budget.
    result=c.collect(KEY,max_requests=2,daily_budget=1,min_interval=0,refresh=True)
    assert result['stop_reason']=='local_daily_budget' and result['requests']==0
    assert c.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
    c.close()


def test_reflected_credentials_never_written(tmp_path):
    c=collector(tmp_path,lambda *a,**k:b'<error>'+KEY.encode()+b'</error>')
    result=c.collect(KEY,min_interval=0)
    assert result['stop_reason']=='secret_reflection'
    for path in Path(tmp_path).rglob('*'):
        if path.is_file():assert KEY.encode() not in path.read_bytes()
    assert not (tmp_path/'raw').exists()
    c.close()


def test_changed_snapshot_replaces_current_and_preserves_history(tmp_path):
    values=[xml([rent()]),xml()]
    c=collector(tmp_path,lambda *a,**k:values.pop(0))
    first=c.collect(KEY,max_requests=1,min_interval=0)
    assert first['coverage']['complete']==1
    c.collect(KEY,max_requests=1,min_interval=0,refresh=True)
    assert c.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]==2
    changes=list((tmp_path/'changes').rglob('*.json'))
    assert len(changes)==1
    difference=json.loads(changes[0].read_text())
    assert difference['removed_rows']==1 and difference['not_transaction_event_identity'] is True
    c.close()


def test_concurrent_collector_has_one_owner(tmp_path):
    c=collector(tmp_path,lambda *a,**k:xml());owner=c._acquire()
    with pytest.raises(RealEstateError,match='collector_already_running'):c.collect(KEY,min_interval=0)
    with c.db:c.db.execute('DELETE FROM lease WHERE owner=?',(owner,))
    c.close()


def test_partial_page_hash_tamper_fails_snapshot(tmp_path):
    c=collector(tmp_path,lambda *a,**k:xml([rent()],size=1,total=2,page=a[4]))
    c.collect(KEY,max_requests=1,min_interval=0,page_size=1)
    raw=next((tmp_path/'raw').rglob('*.xml'));raw.write_bytes(b'tampered')
    result=c.collect(KEY,max_requests=1,min_interval=0,page_size=1)
    assert result['coverage']['failed']==1
    assert c.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]==0
    c.close()


def test_sale_and_rent_partition_cannot_merge():
    kwargs=dict(lawd_code='11110',deal_month='202609',retrieved_at=STAMP)
    parts=build_partitions([normalize_xml_page(xml(),**kwargs),normalize_xml_page(xml(),trade_type='rent',**kwargs)])
    assert len(parts)==2
    assert {p['kind'] for p in parts}=={'apartment-sale-report-partition','apartment-rent-report-partition'}


def test_opaque_apartment_id_prefix_never_supplies_current_geography():
    record=normalize_xml_page(xml([rent(aptSeq='99999-old-provider-id')]),lawd_code='11110',
        deal_month='202609',retrieved_at=STAMP,trade_type='rent')['records'][0]
    assert record['quality']=='valid' and record['complex_id']=='molit-apt:11110:99999-old-provider-id'
    assert record['source_complex_id']=='99999-old-provider-id' and record['position'] is None
    wrong=rent(aptSeq='11110-correct-looking');wrong['sggCd']='26110'
    record=normalize_xml_page(xml([wrong]),lawd_code='11110',deal_month='202609',retrieved_at=STAMP,trade_type='rent')['records'][0]
    assert record['complex_id'] is None and {'field':'sggCd','code':'scope_mismatch'} in record['issues']


def test_offline_reprocessing_retains_raw_and_does_not_request(tmp_path):
    calls=[]
    def fetch(*args,**kwargs):calls.append(1);return xml([rent(aptSeq='99999-id')])
    c=collector(tmp_path,fetch);c.collect(KEY,max_requests=1,min_interval=0)
    before={p.relative_to(tmp_path):p.read_bytes() for p in (tmp_path/'raw').rglob('*.xml')}
    result=c.reprocess()
    assert result['jobs']==1 and result['requests']==0 and len(calls)==1
    assert before=={p.relative_to(tmp_path):p.read_bytes() for p in (tmp_path/'raw').rglob('*.xml')}
    assert c.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
    c.close()


def test_refresh_filter_does_not_reset_unselected_completed_month(tmp_path):
    c=Collector(tmp_path,registry(),months=2,clock=lambda:STAMP,transport=lambda *a,**k:xml(),reserve_bytes=0)
    c.collect(KEY,max_requests=4,min_interval=0)
    c.collect(KEY,max_requests=1,min_interval=0,refresh=True,collect_months=['202609'])
    assert c.db.execute("SELECT COUNT(*) FROM jobs WHERE deal_month='202608' AND status='empty'").fetchone()[0]==2
    with pytest.raises(RealEstateError,match='month_outside_planned_window'):
        c.collect(KEY,max_requests=1,collect_months=['202601'])
    c.close()


def test_month_window_cannot_silently_accumulate_or_reset_quota(tmp_path):
    c=collector(tmp_path,lambda *a,**k:xml());c.collect(KEY,max_requests=1,min_interval=0);c.close()
    with pytest.raises(RealEstateError,match='planning_window_changed_requires_migration'):
        Collector(tmp_path,registry(),months=2,clock=lambda:STAMP,reserve_bytes=0)
    c=collector(tmp_path,lambda *a,**k:xml())
    assert c.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
    assert c.summary()['expected']==2;c.close()


def test_explicit_older_month_extension_preserves_jobs_calls_and_priorities(tmp_path):
    c=Collector(tmp_path,registry(),months=2,clock=lambda:STAMP,transport=lambda *a,**k:xml(),reserve_bytes=0)
    c.collect(KEY,max_requests=1,min_interval=0)
    before=[tuple(row) for row in c.db.execute('SELECT id,priority,status,pages FROM jobs ORDER BY id')]
    assert c.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
    c.close()
    with pytest.raises(RealEstateError,match='planning_window_changed_requires_migration'):
        Collector(tmp_path,registry(),months=3,clock=lambda:STAMP,reserve_bytes=0)
    c=Collector(tmp_path,registry(),months=3,clock=lambda:STAMP,reserve_bytes=0,extend_window=True)
    after=[tuple(row) for row in c.db.execute('SELECT id,priority,status,pages FROM jobs WHERE deal_month IN ("202608","202609") ORDER BY id')]
    assert after==before
    assert c.summary()['expected']==6 and c.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
    assert c.db.execute('SELECT COUNT(*) FROM jobs WHERE deal_month="202607" AND status="pending"').fetchone()[0]==2
    c.close()


def test_older_month_extension_refuses_an_active_collector_lease(tmp_path):
    c=Collector(tmp_path,registry(),months=2,clock=lambda:STAMP,reserve_bytes=0)
    with c.db:c.db.execute('INSERT INTO lease VALUES(1,?,?)',('other-run',time.time()+120))
    c.close()
    with pytest.raises(RealEstateError,match='planning_window_changed_requires_migration'):
        Collector(tmp_path,registry(),months=3,clock=lambda:STAMP,reserve_bytes=0,extend_window=True)
    c=Collector(tmp_path,registry(),months=2,clock=lambda:STAMP,reserve_bytes=0)
    assert c.summary()['expected']==4
    assert c.db.execute("SELECT value FROM meta WHERE key='planning_previous_months'").fetchone() is None
    c.close()


def test_atomic_content_file_is_absent_after_promotion_failure(tmp_path,monkeypatch):
    import pipeline.real_estate_fetch as module
    def reject(*args):raise OSError('fixture failure')
    monkeypatch.setattr(module.os,'link',reject)
    with pytest.raises(OSError):immutable(tmp_path,'raw/input.xml',b'complete fixture')
    assert not (tmp_path/'raw'/'input.xml').exists()
    assert not list((tmp_path/'raw').glob('.pending-*'))


def test_expired_owner_cannot_overwrite_a_new_collectors_checkpoint(tmp_path):
    c=None
    def transport(*args,**kwargs):
        with c.db:c.db.execute("UPDATE lease SET owner='new-owner' WHERE id=1")
        return xml()
    c=collector(tmp_path,transport)
    result=c.collect(KEY,max_requests=1,min_interval=0)
    assert result['stop_reason']=='collector_lease_lost'
    assert result['coverage']['pending']==2 and result['coverage']['failed']==0
    assert c.db.execute('SELECT owner FROM lease').fetchone()[0]=='new-owner'
    assert c.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]==0
    assert c.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==1
    c.close()
