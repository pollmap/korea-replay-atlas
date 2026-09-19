from copy import deepcopy
import json
from pathlib import Path

import pytest

from pipeline.real_estate import RealEstateError, sha256
from pipeline.real_estate_fetch import Collector
from pipeline.real_estate_publish import publish
from test_real_estate_fetch import registry, xml, rent, STAMP, KEY


def sale(cancelled=False):
    return {'sggCd':'11110','umdCd':'10100','umdNm':'검증동','aptNm':'가상단지','aptSeq':'11110-99999',
        'jibun':'1-2','excluUseAr':'84.99','dealYear':'2026','dealMonth':'9','dealDay':'1',
        'dealAmount':'20,000','floor':'4','buildYear':'2000','cdealType':'O' if cancelled else '',
        'cdealDay':'20260903' if cancelled else ''}


def setup(tmp_path,limit=2):
    def transport(*args,**kwargs):
        if args[1]=='sale':return xml([sale(),sale(True)])
        row=rent();row['aptSeq']='11110-99999'
        return xml([row,row])
    root=tmp_path/'checkpoint'
    c=Collector(root,registry(),months=1,clock=lambda:STAMP,transport=transport,reserve_bytes=0)
    c.collect(KEY,max_requests=limit,min_interval=0);c.close()
    return root


def read_asset(base,asset):return json.loads((base/asset['url'].lstrip('/')).read_text(encoding='utf-8'))


def test_publication_reparses_raw_preserves_occurrences_and_cancellation(tmp_path):
    root=setup(tmp_path);result=publish(root,registry(),tmp_path/'candidates');base=tmp_path/'candidates'/result['release_id']
    assert result['audit']['source_rows']==4 and result['audit']['complexes']==1
    assert result['audit']['source_hashes_verified'] and result['audit']['raw_pages_reparsed']
    manifest=json.loads((base/result['property_release']['path']).read_text(encoding='utf-8'))
    assert manifest['coordinates']=={'verified_complexes':0,'unresolved_complexes':1,'name_only_join':False}
    region=read_asset(base,read_asset(base,manifest['regions'])['regions'][0]['index'])
    metrics={m['trade_type']:m for m in region['metrics']}
    assert metrics['sale']['source_rows']==2 and metrics['sale']['eligible_rows']==1 and metrics['sale']['cancelled_rows']==1
    assert metrics['rent']['source_rows']==2 and metrics['rent']['cancelled_rows'] is None
    complexes=read_asset(base,region['complexes'])['complexes']
    assert len(complexes)==1 and complexes[0]['position'] is None and not complexes[0]['address_conflict']
    asset=region['partitions'][0]['transactions'][0]
    rows=read_asset(base,asset)['transactions']
    assert len(rows)==4 and len({r['id'] for r in rows})==4
    assert sum(r['cancellation']=='cancelled' for r in rows)==1
    for f in result['files']:
        payload=(base/f['path']).read_bytes()
        assert sha256(payload)==f['sha256'] and len(payload)==f['byte_length']
        assert b'serviceKey' not in payload and b'source_fields' not in payload
    assert publish(root,registry(),tmp_path/'candidates')==result


def test_pending_is_null_never_zero_and_has_no_transaction_asset(tmp_path):
    root=setup(tmp_path,1);result=publish(root,registry(),tmp_path/'candidates');base=tmp_path/'candidates'/result['release_id']
    region=json.loads((base/f"data/property/{result['release_id']}/regions/11110.json").read_text(encoding='utf-8'))
    pending=next(p for p in region['partitions'] if p['trade_type']=='sale')
    assert pending['status']=='pending' and pending['source_rows'] is None and pending['transactions']==[]
    m=next(m for m in region['metrics'] if m['trade_type']=='sale')
    assert m['eligible_rows'] is None and m['median_price_per_m2_krw'] is None


def test_changed_raw_prevents_any_completed_release(tmp_path):
    root=setup(tmp_path);next((root/'raw').rglob('*.xml')).write_bytes(b'altered')
    with pytest.raises(RealEstateError,match='checkpoint_size_mismatch|checkpoint_hash_mismatch'):
        publish(root,registry(),tmp_path/'candidates')
    assert not list((tmp_path/'candidates').glob('property-*'))


def test_changed_registry_and_public_output_are_rejected(tmp_path):
    root=setup(tmp_path);r=deepcopy(registry());r['regions'][0]['name']='changed'
    with pytest.raises(RealEstateError,match='registry_hash_mismatch'):publish(root,r,tmp_path/'candidates')
    with pytest.raises(RealEstateError,match='public_output_forbidden'):publish(root,registry(),tmp_path/'public')


def test_nonstandard_lot_is_location_issue_not_price_exclusion(tmp_path):
    def transport(*args,**kwargs):
        if args[1]=='rent':return xml()
        valid=sale();valid['jibun']='가-'
        cancelled=sale(True);cancelled['jibun']='BL-2-2'
        invalid=sale();invalid['dealAmount']='not-money';invalid['jibun']='BL-'
        return xml([valid,cancelled,invalid])
    root=tmp_path/'checkpoint'
    c=Collector(root,registry(),months=1,clock=lambda:STAMP,transport=transport,reserve_bytes=0)
    c.collect(KEY,max_requests=2,min_interval=0);c.close()
    result=publish(root,registry(),tmp_path/'candidates');base=tmp_path/'candidates'/result['release_id']
    region=json.loads((base/f"data/property/{result['release_id']}/regions/11110.json").read_text(encoding='utf-8'))
    m=next(m for m in region['metrics'] if m['trade_type']=='sale')
    assert m['invalid_rows']==3 and m['eligible_rows']==1 and m['statistics_excluded_rows']==2
    assert m['median_price_per_m2_krw'] is not None
    rows=read_asset(base,next(p for p in region['partitions'] if p['trade_type']=='sale')['transactions'][0])['transactions']
    assert sum(r['statistics_eligible'] for r in rows)==1
    assert all(r['lot_number'] is None for r in rows)
    assert read_asset(base,region['complexes'])['complexes'][0]['position'] is None


def test_publisher_respects_disk_reserve_without_final_release(tmp_path,monkeypatch):
    import pipeline.real_estate_publish as module
    root=setup(tmp_path)
    class Space:free=1000
    monkeypatch.setattr(module.shutil,'disk_usage',lambda path:Space())
    with pytest.raises(RealEstateError,match='disk_reserve'):
        publish(root,registry(),tmp_path/'candidates',reserve_bytes=1000)
    assert not list((tmp_path/'candidates').glob('property-*'))
