import hashlib
import io
import json

import pytest
from botocore.exceptions import ClientError

from pipeline import audit as audit_module
from pipeline import publication


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    public=tmp_path/'public'/'data';local=tmp_path/'local'
    (public/'releases').mkdir(parents=True)
    monkeypatch.setattr(publication,'PUBLIC',public)
    monkeypatch.setattr(publication,'LOCAL',local)
    monkeypatch.setattr(audit_module,'PUBLIC',public)
    monkeypatch.setattr(audit_module,'LOCAL',local)
    def release(token, feature_name):
        path=public/f'{feature_name}.geojson'
        data=json.dumps({'type':'FeatureCollection','features':[],'fixture':feature_name}).encode()
        path.write_bytes(data)
        catalog={'schema_version':1,'release_id':'pub-'+token*16,
                 'assets':[{'id':feature_name,'url':'/data/'+path.name,'format':'geojson',
                            'count':0,'sha256':hashlib.sha256(data).hexdigest()}]}
        content=json.dumps(catalog,ensure_ascii=False).encode()
        (public/'releases'/f'{catalog["release_id"]}.json').write_bytes(content)
        return catalog,content
    a,a_bytes=release('a','first')
    b,b_bytes=release('b','second')
    (public/'catalog.json').write_bytes(a_bytes)
    return public,local,a,a_bytes,b,b_bytes


def test_concurrent_pointer_promotion_cannot_mix_audited_and_planned_releases(workspace,monkeypatch):
    public,local,a,a_bytes,b,b_bytes=workspace
    original=audit_module.audit
    def advancing_writer(**kwargs):
        report=original(**kwargs)
        (public/'catalog.json').write_bytes(b_bytes)
        return report
    monkeypatch.setattr(audit_module,'audit',advancing_writer)
    plan=publication.prepare()
    expected=hashlib.sha256(a_bytes).hexdigest()
    assert plan['release_id']==a['release_id']
    assert plan['catalog_hash']==plan['audit']['catalog_hash']==expected
    assert plan['audit']['release_id']==a['release_id']
    assert {e['key'] for e in plan['files']}=={'public/first.geojson',f'public/releases/{a["release_id"]}.json'}
    manifest=next(e for e in plan['files'] if '/releases/' in e['key'])
    assert manifest['sha256']==expected
    assert json.loads((public/'catalog.json').read_bytes())['release_id']==b['release_id']
    saved_audit=json.loads((local/'audit'/f'publication-{a["release_id"]}.json').read_bytes())
    assert saved_audit['catalog_hash']==expected
    assert [c['id'] for c in saved_audit['checks']]==['first']


def test_immutable_release_not_pointer_payload_is_audited(workspace):
    public,_,a,_,_,_=workspace
    # The pointer selects a version; incidental content is not a second source.
    (public/'catalog.json').write_text(json.dumps({'release_id':a['release_id'],'assets':[]}),encoding='utf-8')
    plan=publication.prepare()
    assert 'public/first.geojson' in {e['key'] for e in plan['files']}


def test_selected_release_creation_gap_waits_for_same_id(workspace,monkeypatch):
    public,_,a,a_bytes,b,b_bytes=workspace
    selected=public/'releases'/f'{a["release_id"]}.json'
    selected.unlink()
    calls=[]
    def publish_immutable(_):
        calls.append(1)
        (public/'catalog.json').write_bytes(b_bytes)
        selected.write_bytes(a_bytes)
    monkeypatch.setattr(publication.time,'sleep',publish_immutable)
    plan=publication.prepare()
    assert plan['release_id']==a['release_id']
    assert len(calls)==1


def test_selected_release_mutation_during_audit_fails_without_saving_plan(workspace,monkeypatch):
    public,local,a,_,_,_=workspace
    original=audit_module.audit
    def tamper(**kwargs):
        report=original(**kwargs)
        path=public/'releases'/f'{a["release_id"]}.json'
        path.write_bytes(path.read_bytes()+b'\n')
        return report
    monkeypatch.setattr(audit_module,'audit',tamper)
    with pytest.raises(ValueError,match='changed during preparation'):
        publication.prepare()
    assert not (local/'deploy'/'upload-plan.json').exists()


def test_asset_mutation_after_audit_fails_instead_of_planning_wrong_hash(workspace,monkeypatch):
    public,_,_,_,_,_=workspace
    original=audit_module.audit
    def tamper(**kwargs):
        report=original(**kwargs)
        (public/'first.geojson').write_bytes(b'{}')
        return report
    monkeypatch.setattr(audit_module,'audit',tamper)
    with pytest.raises(ValueError,match='changed during preparation'):
        publication.prepare()


def test_wrong_release_content_and_path_traversal_are_rejected(workspace):
    public,_,a,_,_,b_bytes=workspace
    (public/'releases'/f'{a["release_id"]}.json').write_bytes(b_bytes)
    with pytest.raises(ValueError,match='ID mismatch'):
        publication.prepare()
    (public/'catalog.json').write_text(json.dumps({'release_id':'../../other'}),encoding='utf-8')
    with pytest.raises(ValueError,match='Invalid local release ID'):
        publication.prepare()


class MemoryS3:
    """Small conditional-object-store double; no credentials or network calls."""
    def __init__(self):
        self.objects={};self.calls=[];self.fail_key=None

    def error(self,code,operation):
        return ClientError({'Error':{'Code':code,'Message':'fixture'}},operation)

    def head_object(self,*,Bucket,Key):
        self.calls.append(('head',Bucket,Key))
        if (Bucket,Key) not in self.objects:raise self.error('NoSuchKey','HeadObject')
        body,metadata=self.objects[Bucket,Key]
        return {'ContentLength':len(body),'Metadata':metadata,'ETag':hashlib.sha256(body).hexdigest()}

    def get_object(self,*,Bucket,Key):
        self.calls.append(('get',Bucket,Key))
        if (Bucket,Key) not in self.objects:raise self.error('NoSuchKey','GetObject')
        body,metadata=self.objects[Bucket,Key]
        return {'Body':io.BytesIO(body),'ETag':hashlib.sha256(body).hexdigest(),'Metadata':metadata}

    def put_object(self,*,Bucket,Key,Body,Metadata=None,**kwargs):
        self.calls.append(('put',Bucket,Key))
        if Key==self.fail_key:raise self.error('ServiceUnavailable','PutObject')
        existing=self.objects.get((Bucket,Key))
        if kwargs.get('IfNoneMatch')=='*' and existing is not None:
            raise self.error('PreconditionFailed','PutObject')
        if 'IfMatch' in kwargs and (existing is None or hashlib.sha256(existing[0]).hexdigest()!=kwargs['IfMatch']):
            raise self.error('PreconditionFailed','PutObject')
        body=Body.read() if hasattr(Body,'read') else Body
        self.objects[Bucket,Key]=(body,Metadata or {})
        return {}


@pytest.fixture
def remote(workspace,monkeypatch):
    store=MemoryS3();bucket='test-fixture-bucket'
    monkeypatch.setattr(publication,'client',lambda:(store,bucket))
    return store,bucket


def test_complete_upload_receipt_is_last_and_promotion_rechecks_every_dependency(workspace,remote):
    _,local,a,a_bytes,_,_=workspace;s3,bucket=remote
    publication.upload()
    key=publication.receipt_key(a['release_id'])
    puts=[key for operation,_,key in s3.calls if operation=='put']
    assert puts[-1]==key and 'public/catalog.json' not in puts
    receipt=json.loads(s3.objects[bucket,key][0])
    assert receipt['manifest']['count']==2
    assert receipt['manifest']['bucket']==bucket
    assert (local/'deploy'/f'{a["release_id"]}.staged.json').is_file()
    s3.calls.clear()
    publication.promote(a['release_id'])
    assert s3.objects[bucket,'public/catalog.json'][0]==a_bytes
    calls_before_put=s3.calls[:s3.calls.index(('put',bucket,'public/catalog.json'))]
    assert {key for operation,_,key in calls_before_put if operation=='head'}=={e['key'] for e in receipt['manifest']['files']}


def test_interrupted_upload_cannot_promote_and_resume_finishes_receipt(workspace,remote):
    _,_,a,_,_,_=workspace;s3,bucket=remote
    s3.fail_key=f'public/releases/{a["release_id"]}.json'
    with pytest.raises(ClientError):publication.upload()
    assert (bucket,'public/first.geojson') in s3.objects
    assert (bucket,publication.receipt_key(a['release_id'])) not in s3.objects
    with pytest.raises(ValueError,match='Missing complete staging receipt'):publication.promote(a['release_id'])
    assert (bucket,'public/catalog.json') not in s3.objects
    s3.fail_key=None
    publication.upload();publication.promote(a['release_id'])
    # Idempotent upload accepts the exact existing immutable receipt.
    publication.upload()


def test_release_json_alone_cannot_promote(workspace,remote):
    _,_,a,a_bytes,_,_=workspace;s3,bucket=remote
    s3.objects[bucket,f'public/releases/{a["release_id"]}.json']=(a_bytes,{})
    with pytest.raises(ValueError,match='Missing complete staging receipt'):
        publication.promote(a['release_id'])
    assert not any(call[0]=='put' for call in s3.calls)


@pytest.mark.parametrize('fault',['missing','hash','size','release-body'])
def test_staged_dependency_damage_blocks_current_change(workspace,remote,fault):
    _,_,a,_,_,_=workspace;s3,bucket=remote
    publication.upload()
    key=(bucket,'public/first.geojson')
    if fault=='missing':del s3.objects[key]
    elif fault=='hash':s3.objects[key]=(s3.objects[key][0],{'sha256':'0'*64})
    elif fault=='size':s3.objects[key]=(s3.objects[key][0]+b' ',s3.objects[key][1])
    else:
        key=(bucket,f'public/releases/{a["release_id"]}.json')
        s3.objects[key]=(s3.objects[key][0]+b' ',s3.objects[key][1])
    with pytest.raises(ValueError,match='Staged'):
        publication.promote(a['release_id'])
    assert (bucket,'public/catalog.json') not in s3.objects


@pytest.mark.parametrize('fault',['bucket','release_id','catalog_hash','count','files','state','audit'])
def test_receipt_tampering_is_rejected(workspace,remote,fault):
    _,_,a,_,_,_=workspace;s3,bucket=remote
    publication.upload();key=(bucket,publication.receipt_key(a['release_id']))
    receipt=json.loads(s3.objects[key][0]);manifest=receipt['manifest']
    if fault=='state':receipt['state']='uploading'
    elif fault=='count':manifest['count']+=1
    elif fault=='files':manifest['files'].pop(0)
    elif fault=='audit':manifest['audit']['passed']=False
    else:manifest[fault]='wrong-value'
    s3.objects[key]=(json.dumps(receipt).encode(),{})
    with pytest.raises(ValueError,match='Invalid staging receipt'):
        publication.promote(a['release_id'])
    assert (bucket,'public/catalog.json') not in s3.objects


def test_copied_receipt_cannot_authorize_another_bucket(workspace,remote,monkeypatch):
    _,_,a,_,_,_=workspace;s3,bucket=remote
    publication.upload()
    for (_,key),value in list(s3.objects.items()):s3.objects['other-bucket',key]=value
    monkeypatch.setattr(publication,'client',lambda:(s3,'other-bucket'))
    with pytest.raises(ValueError,match='bucket'):
        publication.promote(a['release_id'])
    assert ('other-bucket','public/catalog.json') not in s3.objects


def test_rollback_uses_previous_remote_receipt_not_local_catalog(workspace,remote):
    public,_,a,a_bytes,b,b_bytes=workspace;s3,bucket=remote
    publication.upload();publication.promote(a['release_id'])
    (public/'catalog.json').write_bytes(b_bytes)
    publication.upload();publication.promote(b['release_id'])
    (public/'catalog.json').write_text('unreadable local pointer',encoding='utf-8')
    publication.promote(a['release_id'])
    assert s3.objects[bucket,'public/catalog.json'][0]==a_bytes


def test_local_mutation_after_prepare_cannot_mint_receipt(workspace,remote,monkeypatch):
    public,_,a,_,_,_=workspace;s3,bucket=remote
    plan=publication.prepare()
    monkeypatch.setattr(publication,'prepare',lambda:plan)
    (public/'first.geojson').write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed since preparation'):publication.upload()
    assert (bucket,publication.receipt_key(a['release_id'])) not in s3.objects


def test_pointer_conditional_write_still_rejects_concurrent_promotion(workspace,remote,monkeypatch):
    _,_,a,_,_,b_bytes=workspace;s3,bucket=remote
    publication.upload()
    original=s3.put_object
    def competing_write(**kwargs):
        if kwargs['Key']=='public/catalog.json':
            s3.objects[bucket,'public/catalog.json']=(b_bytes,{})
        return original(**kwargs)
    monkeypatch.setattr(s3,'put_object',competing_write)
    with pytest.raises(ClientError):publication.promote(a['release_id'])
    assert s3.objects[bucket,'public/catalog.json'][0]==b_bytes


def test_release_uploaded_but_nested_terrain_missing_cannot_promote(workspace,remote):
    public,_,a,_,_,_=workspace;s3,bucket=remote
    tile=public/'terrain'/'0'/'0'/'0.terrain'
    tile.parent.mkdir(parents=True);tile.write_bytes(bytes(100))
    layer=public/'terrain'/'layer.json'
    layer.write_text(json.dumps({'available':[[{'startX':0,'endX':0,'startY':0,'endY':0}]]}),encoding='utf-8')
    a['assets'].append({'id':'terrain','url':'/data/terrain/layer.json','format':'quantized-mesh',
                        'count':1,'sha256':hashlib.sha256(layer.read_bytes()).hexdigest()})
    content=json.dumps(a).encode()
    (public/'catalog.json').write_bytes(content)
    (public/'releases'/f'{a["release_id"]}.json').write_bytes(content)
    s3.fail_key='public/terrain/0/0/0.terrain'
    with pytest.raises(ClientError):publication.upload()
    assert (bucket,f'public/releases/{a["release_id"]}.json') in s3.objects
    with pytest.raises(ValueError,match='Missing complete staging receipt'):publication.promote(a['release_id'])
    s3.fail_key=None;publication.upload()
    receipt=json.loads(s3.objects[bucket,publication.receipt_key(a['release_id'])][0])
    assert receipt['manifest']['count']==4
    del s3.objects[bucket,'public/terrain/0/0/0.terrain']
    with pytest.raises(ValueError,match='Staged dependency missing'):publication.promote(a['release_id'])
    assert (bucket,'public/catalog.json') not in s3.objects


def test_existing_conflicting_receipt_is_never_overwritten(workspace,remote):
    _,_,a,_,_,_=workspace;s3,bucket=remote
    key=(bucket,publication.receipt_key(a['release_id']))
    old=b'{"state":"untrusted-old-marker"}';s3.objects[key]=(old,{})
    with pytest.raises(ValueError,match='receipt conflicts'):publication.upload()
    assert s3.objects[key][0]==old
    assert (bucket,'public/catalog.json') not in s3.objects


@pytest.mark.parametrize('identical',[True,False])
def test_concurrent_immutable_upload_is_accepted_only_for_same_hash(workspace,remote,monkeypatch,identical):
    public,_,a,_,_,_=workspace;s3,bucket=remote
    original=s3.put_object;won=False
    def competing_upload(**kwargs):
        nonlocal won
        if kwargs['Key']=='public/first.geojson' and not won:
            won=True
            data=(public/'first.geojson').read_bytes() if identical else b'conflicting data'
            s3.objects[bucket,kwargs['Key']]=(data,{'sha256':hashlib.sha256(data).hexdigest()})
        return original(**kwargs)
    monkeypatch.setattr(s3,'put_object',competing_upload)
    if identical:
        publication.upload();publication.promote(a['release_id'])
    else:
        with pytest.raises(ValueError,match='hash or size mismatch'):publication.upload()
        assert (bucket,publication.receipt_key(a['release_id'])) not in s3.objects
