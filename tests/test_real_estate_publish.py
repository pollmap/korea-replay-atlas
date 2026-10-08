from copy import deepcopy
import json
import sqlite3
from pathlib import Path

import pytest

from pipeline.real_estate import RealEstateError, sha256
from pipeline.real_estate_fetch import Collector
from pipeline.real_estate_publish import publish
from test_real_estate_fetch import registry, xml, rent, STAMP, KEY


@pytest.fixture(autouse=True)
def fixture_disk_space(monkeypatch):
    # Unit fixtures are tiny and must not depend on the host's production reserve.
    # The dedicated reserve test overrides this with its constrained device.
    from collections import namedtuple
    import pipeline.real_estate_publish as module
    Usage = namedtuple('Usage', 'total used free')
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: Usage(200*1024**3, 10*1024**3, 190*1024**3))


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


@pytest.mark.parametrize('refresh_status',['pending','partial','failed'])
def test_retained_snapshot_reparses_its_original_pages_and_keeps_collection_failure(tmp_path,refresh_status):
    root=setup(tmp_path)
    with sqlite3.connect(root/'checkpoint.sqlite') as db:
        old=db.execute("SELECT snapshot FROM jobs WHERE trade_type='sale'").fetchone()[0]
        db.execute("UPDATE jobs SET status=?,pages='[]',error_code=? WHERE trade_type='sale'",
                   (refresh_status,'upstream_timeout' if refresh_status=='failed' else None))
        db.execute("INSERT INTO calls(day,trade_type,job_id,page_no,started_at,status) VALUES('2026-09-21','sale','sale/11110/202609',1,'2026-09-21T00:00:00Z','failed')")
        original=list(db.execute('SELECT * FROM jobs'))
    result=publish(root,registry(),tmp_path/'candidates');base=tmp_path/'candidates'/result['release_id']
    region=json.loads((base/f"data/property/{result['release_id']}/regions/11110.json").read_text(encoding='utf-8'))
    p=next(x for x in region['partitions'] if x['trade_type']=='sale')
    m=next(x for x in region['metrics'] if x['trade_type']=='sale')
    assert p['status']=='complete' and p['source_rows']==2 and p['transactions']
    assert p['retrieved_at']==STAMP and m['retrieved_at']==STAMP
    assert p['refresh']==m['refresh']=={'status':refresh_status,'error_code':'upstream_timeout' if refresh_status=='failed' else None,'attempted_at':'2026-09-21T00:00:00Z'}
    assert result['audit']['stale_jobs']==1 and result['audit']['source_rows']==4
    assert region['coverage']['complete']==2 and region['collection_coverage']['complete']==1
    assert region['collection_coverage'][refresh_status]==1
    with sqlite3.connect(root/'checkpoint.sqlite') as db:
        assert list(db.execute('SELECT * FROM jobs'))==original
        assert db.execute("SELECT snapshot FROM jobs WHERE trade_type='sale'").fetchone()[0]==old


def test_enqueued_without_new_call_does_not_invent_attempt_time(tmp_path):
    root=setup(tmp_path)
    with sqlite3.connect(root/'checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET status='pending',pages='[]' WHERE trade_type='sale'")
    result=publish(root,registry(),tmp_path/'candidates');base=tmp_path/'candidates'/result['release_id']
    region=json.loads((base/f"data/property/{result['release_id']}/regions/11110.json").read_text(encoding='utf-8'))
    assert next(p for p in region['partitions'] if p['trade_type']=='sale')['refresh']['attempted_at'] is None


def test_retained_snapshot_missing_or_changed_original_never_publishes(tmp_path):
    root=setup(tmp_path)
    with sqlite3.connect(root/'checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET status='pending',pages='[]'")
    next((root/'raw').rglob('*.xml')).write_bytes(b'changed')
    with pytest.raises(RealEstateError,match='checkpoint_size_mismatch|checkpoint_hash_mismatch'):
        publish(root,registry(),tmp_path/'candidates')
    assert not list((tmp_path/'candidates').glob('property-*'))


@pytest.mark.parametrize('state',['pending','partial','failed'])
def test_without_verified_snapshot_stays_unavailable_not_stale_or_zero(tmp_path,state):
    root=setup(tmp_path,1)
    with sqlite3.connect(root/'checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET status=?,error_code=? WHERE snapshot IS NULL",
                   (state,'upstream_timeout' if state=='failed' else None))
    result=publish(root,registry(),tmp_path/'candidates');base=tmp_path/'candidates'/result['release_id']
    region=json.loads((base/f"data/property/{result['release_id']}/regions/11110.json").read_text(encoding='utf-8'))
    p=next(x for x in region['partitions'] if x['trade_type']=='sale')
    assert p['status']==state and p['source_rows'] is None and p['transactions']==[] and 'refresh' not in p
    assert region['coverage'][state]==region['collection_coverage'][state]==1


def test_retained_observed_empty_is_distinct_from_failed_without_snapshot(tmp_path):
    root=tmp_path/'checkpoint'
    c=Collector(root,registry(),months=1,clock=lambda:STAMP,transport=lambda *a,**k:xml(),reserve_bytes=0)
    c.collect(KEY,max_requests=1,min_interval=0)
    with c.db:c.db.execute("UPDATE jobs SET status='failed',pages='[]',error_code='upstream_timeout'")
    c.close()
    result=publish(root,registry(),tmp_path/'candidates');base=tmp_path/'candidates'/result['release_id']
    region=json.loads((base/f"data/property/{result['release_id']}/regions/11110.json").read_text(encoding='utf-8'))
    assert region['coverage']['empty']==1 and region['coverage']['failed']==1
    assert region['collection_coverage']['failed']==2
    old=next(p for p in region['partitions'] if p['status']=='empty')
    assert old['source_rows']==0 and old['retrieved_at']==STAMP and old['refresh']['status']=='failed'


def test_466_retained_and_307_new_snapshots_publish_without_editing_ledger(tmp_path):
    r=registry();second={**r['regions'][0],'lawd_code':'11140','legal_code':'1114000000','name':'서울특별시 중구'}
    r['regions'].append(second)
    def transport(key,trade,code,month,*args,**kwargs):
        row=sale() if trade=='sale' else rent()
        row.update(sggCd=code,aptSeq=f'{code}-99',dealYear=month[:4],dealMonth=month[4:],dealDay='1')
        return xml([row])
    root=tmp_path/'checkpoint'
    c=Collector(root,r,months=241,as_of=STAMP,clock=lambda:STAMP,transport=transport,reserve_bytes=0)
    result=c.collect(KEY,max_requests=773,min_interval=0)
    assert result['requests']==773
    ids=[j[0] for j in c.db.execute("SELECT id FROM jobs WHERE snapshot IS NOT NULL ORDER BY id")]
    assert len(ids)==773
    with c.db:
        c.db.executemany("UPDATE jobs SET status='pending',pages='[]' WHERE id=?",[(v,) for v in ids[:466]])
    before=list(c.db.execute('SELECT id,status,pages,snapshot FROM jobs ORDER BY id'));c.close()
    result=publish(root,r,tmp_path/'candidates',reserve_bytes=0)
    assert result['audit']['stale_jobs']==466
    assert result['audit']['coverage']['complete']==773 and result['audit']['source_rows']==773
    assert result['audit']['collection_coverage']['complete']==307
    base=tmp_path/'candidates'/result['release_id'];published=[]
    for path in (base/f"data/property/{result['release_id']}/regions").glob('*.json'):
        published.extend(json.loads(path.read_text(encoding='utf-8'))['partitions'])
    visible=[p for p in published if p['status']=='complete']
    assert len(visible)==773 and all(p['retrieved_at']==STAMP and p['source_rows']==1 for p in visible)
    assert sum('refresh' in p for p in visible)==466
    with sqlite3.connect(root/'checkpoint.sqlite') as db:
        assert list(db.execute('SELECT id,status,pages,snapshot FROM jobs ORDER BY id'))==[tuple(x) for x in before]


@pytest.mark.parametrize('empty',[False,True])
def test_unchanged_recheck_calls_reuse_complete_publication_without_reparsing(tmp_path,monkeypatch,empty):
    import pipeline.real_estate_publish as module
    if empty:
        root=tmp_path/'checkpoint'
        c=Collector(root,registry(),months=1,clock=lambda:STAMP,transport=lambda *a,**k:xml(),reserve_bytes=0)
        c.collect(KEY,max_requests=2,min_interval=0);c.close()
    else:root=setup(tmp_path)
    output=tmp_path/'candidates';original=publish(root,registry(),output)
    before={str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*') if p.is_file()}
    with sqlite3.connect(root/'checkpoint.sqlite') as db:
        db.execute("INSERT INTO calls(day,trade_type,job_id,page_no,started_at,status) VALUES('2026-09-21','sale','sale/11110/202609',1,'2026-09-21T00:00:00Z','stored')")
        # Operational correction receipts do not change the published snapshot.
        from pipeline.property_refresh_scheduler import initialize
        initialize(db)
        row=db.execute("SELECT snapshot FROM jobs WHERE trade_type='sale'").fetchone()[0]
        db.execute("INSERT INTO property_correction_queue VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",('sale/11110/202609','recent','complete','[]',row,'2026-09-21T00:00:00Z','2026-09-21T00:00:00Z',None,None,0,'2026-09-21T00:00:00Z','test-normalizer'))
    version=module.verification_version()
    monkeypatch.setattr(module,'verification_version',lambda:version)
    monkeypatch.setattr(module,'verify_snapshot',lambda *a:pytest.fail('unchanged publication reparsed source'))
    assert publish(root,registry(),output)==original
    assert {str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*') if p.is_file()}==before
    with sqlite3.connect(root/'checkpoint.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]==3
        assert db.execute('SELECT COUNT(*) FROM property_correction_queue').fetchone()[0]==1


def test_new_failed_attempt_changes_retained_snapshot_release(tmp_path):
    root=setup(tmp_path);output=tmp_path/'candidates'
    with sqlite3.connect(root/'checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET status='failed',pages='[]',error_code='upstream_timeout' WHERE trade_type='sale'")
    before=publish(root,registry(),output)
    with sqlite3.connect(root/'checkpoint.sqlite') as db:
        db.execute("INSERT INTO calls(day,trade_type,job_id,page_no,started_at,status) VALUES('2026-09-21','sale','sale/11110/202609',1,'2026-09-21T00:00:00Z','failed')")
    after=publish(root,registry(),output)
    assert before['release_id']!=after['release_id']
    assert before['audit']['source_rows']==after['audit']['source_rows']==4
    region=json.loads((output/after['release_id']/f"data/property/{after['release_id']}/regions/11110.json").read_bytes())
    assert next(p for p in region['partitions'] if p['trade_type']=='sale')['refresh']['attempted_at']=='2026-09-21T00:00:00Z'


def test_normalizer_version_change_invalidates_existing_publication(tmp_path,monkeypatch):
    import pipeline.real_estate_publish as module
    root=setup(tmp_path);output=tmp_path/'candidates';before=publish(root,registry(),output)
    original=module.verify_snapshot;verified=[]
    def verify(*args):verified.append(args[1]['id']);return original(*args)
    monkeypatch.setattr(module,'verification_version',lambda:'f'*64)
    monkeypatch.setattr(module,'verify_snapshot',verify)
    after=publish(root,registry(),output)
    assert before['release_id']!=after['release_id'] and len(verified)==2


@pytest.mark.parametrize('change',['price','cancellation'])
def test_changed_correction_keeps_source_invalidation(tmp_path,change):
    from pipeline import property_refresh_scheduler as corrections
    root=setup(tmp_path);output=tmp_path/'candidates';before=publish(root,registry(),output)
    def transport(*args,**kwargs):
        if args[1]=='rent':
            row=rent();row['aptSeq']='11110-99999';return xml([row,row])
        row=sale()
        if change=='price':row['dealAmount']='30,000'
        else:row=sale(True)
        return xml([row,sale(True)])
    c=Collector(root,registry(),months=1,as_of=STAMP,clock=lambda:'2026-09-21T00:00:00Z',transport=transport,reserve_bytes=0)
    batch=corrections.plan(c.db,'2026-09-21T00:00:00Z',scope='nationwide',trades=('sale',),max_requests=1)
    result=c.collect(KEY,max_requests=1,min_interval=0,correction=batch);c.close()
    assert result['correction']['changed_jobs']==1
    after=publish(root,registry(),output)
    assert before['release_id']!=after['release_id']
    region=json.loads((output/after['release_id']/f"data/property/{after['release_id']}/regions/11110.json").read_bytes())
    metric=next(x for x in region['metrics'] if x['trade_type']=='sale')
    assert metric['cancelled_rows']==(2 if change=='cancellation' else 1)
    if change=='price':assert metric['median_price_per_m2_krw']>3000000


@pytest.mark.parametrize('case',[
    'schema','kind','release','head_release','head_path','head_sha','empty_files',
    'duplicate_file','missing_manifest','missing_transaction','missing_region',
    'audit_files','audit_bytes','audit_rows','audit_complexes','audit_stale','foreign_path',
])
def test_reused_publication_rejects_invalid_receipt_without_rebuilding(tmp_path,case):
    root=setup(tmp_path);out=tmp_path/'candidates';result=publish(root,registry(),out)
    base=out/result['release_id'];receipt=base/'publication.json';value=deepcopy(result)
    if case=='schema':value['schema_version']=True
    elif case=='kind':value['kind']='property-release'
    elif case=='release':value['release_id']='property-'+'0'*16
    elif case=='head_release':value['property_release']['release_id']='property-'+'0'*16
    elif case=='head_path':value['property_release']['path']=value['files'][0]['path']
    elif case=='head_sha':value['property_release']['sha256']='0'*64
    elif case=='empty_files':value['files']=[]
    elif case=='duplicate_file':value['files'].append(deepcopy(value['files'][0]))
    elif case.startswith('missing_'):
        if case=='missing_manifest':drop=value['property_release']['path']
        elif case=='missing_transaction':drop=next(f['path'] for f in value['files'] if '/transactions/' in f['path'])
        else:drop=next(f['path'] for f in value['files'] if '/regions/' in f['path'])
        value['files']=[f for f in value['files'] if f['path']!=drop]
    elif case=='audit_files':value['audit']['files']+=1
    elif case=='audit_bytes':value['audit']['bytes']+=1
    elif case=='audit_rows':value['audit']['source_rows']+=1
    elif case=='audit_complexes':value['audit']['complexes']+=1
    elif case=='audit_stale':value['audit']['stale_jobs']+=1
    elif case=='foreign_path':value['files'][0]['path']='data/property/property-'+'0'*16+'/regions.json'
    # Missing/duplicate entries cannot be hidden behind an adjusted receipt count.
    if case in ('empty_files','duplicate_file') or case.startswith('missing_'):
        value['audit']['files']=len(value['files'])
        value['audit']['bytes']=sum(f['byte_length'] for f in value['files'])
    receipt.write_text(json.dumps(value))
    before={str(f.relative_to(out)):f.read_bytes() for f in out.rglob('*') if f.is_file()}
    with pytest.raises(RealEstateError,match='invalid_reused_publication'):
        publish(root,registry(),out)
    assert {str(f.relative_to(out)):f.read_bytes() for f in out.rglob('*') if f.is_file()}==before


@pytest.mark.parametrize('packed,compressed',[(False,False),(True,False),(True,True)])
def test_reused_publication_checks_old_and_compressed_reference_graph_without_source_parse(tmp_path,monkeypatch,packed,compressed):
    import pipeline.real_estate_publish as module
    root=setup(tmp_path);out=tmp_path/'candidates'
    options={'packed_transactions':packed,'compressed_transactions':compressed}
    result=publish(root,registry(),out,**options);receipt=out/result['release_id']/'publication.json'
    # Older receipts need no cache/transport/identity metadata when using the
    # original plain layout; optional audit additions remain backward compatible.
    legacy=deepcopy(result);legacy['audit'].pop('stale_jobs',None)
    legacy['audit']['policy']='property-publication-v4-retained-verified-snapshots'
    receipt.write_text(json.dumps(legacy))
    before={str(f.relative_to(out)):f.read_bytes() for f in out.rglob('*') if f.is_file()}
    version=module.verification_version();monkeypatch.setattr(module,'verification_version',lambda:version)
    monkeypatch.setattr(module,'verify_snapshot',lambda *a:pytest.fail('reused output reparsed source'))
    assert publish(root,registry(),out,**options)==legacy
    assert {str(f.relative_to(out)):f.read_bytes() for f in out.rglob('*') if f.is_file()}==before


def test_reused_publication_hashes_payload_and_rejects_linked_receipt(tmp_path):
    root=setup(tmp_path);out=tmp_path/'candidates';result=publish(root,registry(),out)
    base=out/result['release_id'];asset=base/result['files'][0]['path'];original=asset.read_bytes()
    asset.write_bytes(original[:-1]+b'x')
    with pytest.raises(RealEstateError,match='checkpoint_hash_mismatch'):
        publish(root,registry(),out)
    asset.write_bytes(original)
    receipt=base/'publication.json';external=tmp_path/'external-receipt.json';external.write_bytes(receipt.read_bytes());receipt.unlink()
    try:receipt.symlink_to(external)
    except OSError:pytest.skip('symlinks unavailable')
    with pytest.raises(RealEstateError):publish(root,registry(),out)


@pytest.mark.parametrize('case',['missing_regions_ref','wrong_ref_hash','wrong_ref_size','wrong_ref_path','unreferenced_asset'])
def test_reused_publication_checks_references_even_with_matching_body_hashes(tmp_path,case):
    from pipeline.real_estate import canonical_bytes
    root=setup(tmp_path);out=tmp_path/'candidates';value=publish(root,registry(),out)
    base=out/value['release_id'];manifest_path=value['property_release']['path']
    manifest=json.loads((base/manifest_path).read_bytes())
    if case=='missing_regions_ref':manifest.pop('regions')
    elif case=='wrong_ref_hash':manifest['regions']['sha256']='0'*64
    elif case=='wrong_ref_size':manifest['regions']['bytes']+=1
    elif case=='wrong_ref_path':manifest['regions']['url']=manifest['regions']['url'].replace('regions.json','missing.json')
    else:
        extra=deepcopy(next(f for f in value['files'] if '/transactions/' in f['path']))
        body=(base/extra['path']).read_bytes();extra['path']=extra['path'].replace('-000.json','-999.json')
        (base/extra['path']).write_bytes(body);value['files'].append(extra)
    body=canonical_bytes(manifest);(base/manifest_path).write_bytes(body)
    entry=next(f for f in value['files'] if f['path']==manifest_path)
    entry.update(sha256=sha256(body),byte_length=len(body));value['property_release']['sha256']=sha256(body)
    value['audit']['files']=len(value['files']);value['audit']['bytes']=sum(f['byte_length'] for f in value['files'])
    (base/'publication.json').write_text(json.dumps(value))
    with pytest.raises(RealEstateError,match='invalid_reused_publication'):publish(root,registry(),out)
