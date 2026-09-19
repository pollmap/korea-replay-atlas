import json
import errno
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from types import SimpleNamespace
import time
import pytest
from pipeline import static_release as release
from pipeline.core import atomic_json,digest


@pytest.fixture
def fixture(tmp_path):
    base=tmp_path/'public'/'data';base.mkdir(parents=True)
    source=base/'roads'/'road.geojson'
    atomic_json(source,{'type':'FeatureCollection','features':[]})
    asset={'id':'roads','layer':'infrastructure','format':'geojson','url':'/data/roads/road.geojson',
        'bbox':[127,36,128,37],'source_id':'test','version':'v1','count':0,'sha256':digest(source),
        'feature_count':0,'vertex_count':0,'byte_length':source.stat().st_size}
    catalog={'schema_version':1,'release_id':'old','generated_at':'2026-09-16T00:00:00Z','layers':[],'assets':[asset]}
    input_path=tmp_path/'input.json';atomic_json(input_path,catalog)
    client=tmp_path/'dist'/'client';client.mkdir(parents=True)
    (client/'index.html').write_text('<!doctype html>test',encoding='utf-8')
    worker=tmp_path/'dist'/'worker';worker.mkdir()
    (worker/'index.js').write_text('export default {};',encoding='utf-8')
    return base,input_path,client,worker,tmp_path/'deploy'


def test_catalog_v2_is_small_and_legacy_assets_are_preserved(fixture):
    base,input_path,_,_,_=fixture
    before=input_path.read_bytes()
    current,legacy,_,_=release.build_catalog(input_path,base)
    assert current['schema_version']==2 and not current['assets']
    assert legacy['schema_version']==1 and legacy['assets'][0]['id']=='roads'
    index=json.loads(release.local_url(current['indexes'][0]['url'],base).read_bytes())
    assert index['assets']==legacy['assets']
    assert input_path.read_bytes()==before and not (base/'catalog.json').exists()
    assert release.build_catalog(input_path,base)[0]==current


def test_equivalent_json_never_changes_bytes_of_an_existing_immutable_url(tmp_path):
    path=tmp_path/'immutable.json'
    original=b'{"z":1, "a":{"y":2,"b":3}}\n'
    path.write_bytes(original)
    release.immutable_json(path,{'a':{'b':3,'y':2},'z':1})
    assert path.read_bytes()==original
    with pytest.raises(ValueError,match='Immutable release collision'):
        release.immutable_json(path,{'z':2,'a':{'y':2,'b':3}})
    assert path.read_bytes()==original


def test_new_immutable_json_has_deterministic_key_order(tmp_path):
    first,second=tmp_path/'a.json',tmp_path/'b.json'
    release.immutable_json(first,{'z':1,'a':{'y':2,'b':3}})
    release.immutable_json(second,{'a':{'b':3,'y':2},'z':1})
    assert first.read_bytes()==second.read_bytes()


def test_publisher_rejects_unreconciled_building_parts_before_writing_a_release(fixture):
    base,input_path,_,_,_=fixture
    catalog=json.loads(input_path.read_bytes())
    catalog['assets'][0].update(layer='buildings',format='3d-tiles',count=100,feature_count=100,
        logical_source_building_count=100,building_parts_parent_count=1,
        building_parts_feature_count=3,building_parts_publication_version='test')
    atomic_json(input_path,catalog)
    with pytest.raises(ValueError,match='Building count contract does not reconcile'):
        release.build_catalog(input_path,base)
    assert not (base/'releases').exists() and not (base/'catalog.json').exists()


def test_national_overview_indexes_merge_without_changing_source_assets(fixture):
    base,input_path,_,_,_=fixture
    catalog=json.loads(input_path.read_bytes());template=catalog['assets'][0]
    geometry=release.local_url(template['url'],base);original_geometry=geometry.read_bytes()
    overview=[]
    for index in range(155):
        west,south=124.5+(index%13)*.5,33+(index//13)*.5
        overview.append({**template,'id':f'overview-{index:03}',
            'bbox':[west,south,west+.04,south+.04],
            'detail_level':'overview','min_camera_height':350000})
    rail=[{**template,'id':f'rail-overview-{index}','layer':'rail',
        'bbox':[125+index*4,33,125.1+index*4,34],
        'detail_level':'overview','min_camera_height':200000} for index in range(2)]
    detail=[{**template,'id':'detail-near','bbox':[127,36,127.1,36.1]},
            {**template,'id':'detail-far','bbox':[129,35,129.1,35.1]}]
    catalog['assets']=[*overview,*rail,*detail];atomic_json(input_path,catalog)
    original_input=input_path.read_bytes()
    current,legacy,_,_=release.build_catalog(input_path,base)
    indexes=current['indexes'];merged=[row for row in indexes if row['detail_level']=='overview']
    assert len(merged)==2
    assert len([row for row in indexes if row['detail_level']=='detail'])==2
    members={row['layer']:json.loads(release.local_url(row['url'],base).read_bytes())['assets'] for row in merged}
    assert {row['id'] for row in members['infrastructure']}=={row['id'] for row in overview}
    assert {row['id'] for row in members['rail']}=={row['id'] for row in rail}
    assert sum(len(rows) for rows in members.values())==157
    reconstructed=[asset for index in indexes for asset in json.loads(release.local_url(index['url'],base).read_bytes())['assets']]
    assert sorted(reconstructed,key=lambda a:a['id'])==legacy['assets']==sorted(catalog['assets'],key=lambda a:a['id'])
    assert geometry.read_bytes()==original_geometry and input_path.read_bytes()==original_input
    assert release.build_catalog(input_path,base)[0]==current


def test_overview_merge_preserves_visibility_boundaries_and_512_asset_cap(fixture):
    base,input_path,_,_,_=fixture
    catalog=json.loads(input_path.read_bytes());template=catalog['assets'][0]
    same=[{**template,'id':f'wide-{index:04}','detail_level':'overview','min_camera_height':200000}
          for index in range(513)]
    other=[{**template,'id':'other-min','detail_level':'overview','min_camera_height':300000},
           {**template,'id':'other-max','detail_level':'overview','min_camera_height':200000,'max_camera_height':500000}]
    catalog['assets']=[*same,*other];atomic_json(input_path,catalog)
    current,legacy,_,_=release.build_catalog(input_path,base)
    same_indexes=[row for row in current['indexes'] if row.get('min_camera_height')==200000 and 'max_camera_height' not in row]
    assert len(same_indexes)==2 and len(current['indexes'])==4
    all_assets=[]
    for index in current['indexes']:
        rows=json.loads(release.local_url(index['url'],base).read_bytes())['assets']
        assert len(rows)<=512 and index['byte_length']<=1024*1024
        assert all(row.get('min_camera_height')==index.get('min_camera_height') and row.get('max_camera_height')==index.get('max_camera_height') for row in rows)
        all_assets.extend(rows)
    assert len(all_assets)==515
    assert sorted(all_assets,key=lambda a:a['id'])==legacy['assets']


def test_recursive_tileset_dependencies_and_missing_children(fixture):
    base,_,_,_,_=fixture
    root=base/'tiles'/'tileset.json';region=root.parent/'region.json';glb=root.parent/'detail.glb'
    atomic_json(root,{'root':{'children':[{'content':{'uri':'region.json'}}]}})
    atomic_json(region,{'root':{'content':{'uri':'detail.glb'}}})
    glb.write_bytes(b'geometry')
    asset={'format':'3d-tiles','url':'/data/tiles/tileset.json'}
    assert release.dependencies(asset,base)=={root.resolve(),region.resolve(),glb.resolve()}
    atomic_json(region,{'root':{'content':{'uri':'missing.glb'}}})
    with pytest.raises(ValueError,match='Missing'):release.dependencies(asset,base)


def test_missing_asset_source_version_is_not_invented(fixture):
    base,input_path,_,_,_=fixture
    catalog=json.loads(input_path.read_bytes())
    del catalog['assets'][0]['version']
    atomic_json(input_path,catalog)
    with pytest.raises(ValueError,match='Missing source version: roads'):
        release.build_catalog(input_path,base)


@pytest.mark.parametrize('field', ['vertex_count','feature_count','byte_length'])
def test_catalog_rejects_missing_geojson_work_budgets(fixture,field):
    base,input_path,_,_,_=fixture
    catalog=json.loads(input_path.read_bytes())
    del catalog['assets'][0][field]
    atomic_json(input_path,catalog)
    with pytest.raises(ValueError,match='Missing GeoJSON work budget '+field):
        release.build_catalog(input_path,base)


def test_catalog_rejects_underreported_water_vertices(fixture):
    base,input_path,_,_,_=fixture
    source=base/'roads'/'road.geojson'
    atomic_json(source,{'type':'FeatureCollection','features':[{'type':'Feature','properties':{},
        'geometry':{'type':'Polygon','coordinates':[[[127,36],[128,36],[128,37],[127,37],[127,36]]]}}]})
    catalog=json.loads(input_path.read_bytes())
    catalog['assets'][0].update(sha256=digest(source),byte_length=source.stat().st_size,
                               feature_count=1,vertex_count=4,count=1)
    atomic_json(input_path,catalog)
    with pytest.raises(ValueError,match='Incorrect GeoJSON work budget vertex_count'):
        release.build_catalog(input_path,base)


def test_dependency_cycles_and_external_urls_are_rejected(fixture):
    base,_,_,_,_=fixture
    root=base/'tileset.json'
    atomic_json(root,{'root':{'content':{'uri':'tileset.json'}}})
    with pytest.raises(ValueError,match='Cyclic'):release.dependencies({'format':'3d-tiles','url':'/data/tileset.json'},base)
    atomic_json(root,{'root':{'content':{'uri':'https://example.org/other.glb'}}})
    with pytest.raises(ValueError,match='URI'):release.dependencies({'format':'3d-tiles','url':'/data/tileset.json'},base)


def test_tileset_leaf_metadata_is_verified_before_preparation(fixture):
    base,_,_,_,_=fixture
    leaf=base/'detail.glb';leaf.write_bytes(b'geometry')
    root=base/'tileset.json'
    atomic_json(root,{'root':{'content':{'uri':'detail.glb'},'extras':{'sha256':digest(leaf),'bytes':leaf.stat().st_size}}})
    assert len(release.dependencies({'format':'3d-tiles','url':'/data/tileset.json'},base))==2
    leaf.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='hash'):release.dependencies({'format':'3d-tiles','url':'/data/tileset.json'},base)


def test_bundle_counts_every_generated_file_and_pins_code_with_data(fixture):
    base,input_path,client,worker,output=fixture
    plan=release.prepare(input_path,client_dir=client,base=base,output=output)
    result=release.stage(plan,output=output,worker_dir=worker)
    bundle=Path(result['bundle'])
    assert result['count']==len([p for p in (bundle/'client').rglob('*') if p.is_file()])==plan['count']
    assert digest(bundle/'client'/'data'/'catalog.json')==plan['catalog_hash']
    config=json.loads((bundle/'wrangler.json').read_bytes())
    assert config['assets']['run_worker_first']==['/api/*'] and 'r2_buckets' not in config
    assert config['vars']['COLLECTORS_ENABLED']=='false'
    assert config['vars']['LIVE_TRANSIT_MODE']=='broker'
    assert config['services']==[{'binding':'LIVE_TRANSIT_BROKER','service':'korea-replay-live-broker'}]
    assert result['deployment_contract']=={'schema_version':1,'live_transit_mode':'broker',
                                          'broker':config['services'][0]}
    assert result['schema_version']==2
    assert result['config_hash']==digest(bundle/'wrangler.json')
    assert json.loads((bundle/'receipt.json').read_bytes())==result
    (worker/'index.js').write_text('export default {version:2};',encoding='utf-8')
    second=release.stage(plan,output=output,worker_dir=worker)
    assert second['bundle_id']!=result['bundle_id']
    assert (bundle/'worker'/'index.js').read_text()=='export default {};'
    assert json.loads((bundle/'receipt.json').read_bytes())==result
    assert json.loads((output/'static-stage.json').read_bytes())==second


def test_config_changes_create_new_bundle_identity_without_touching_old_data(fixture,monkeypatch):
    base,input_path,client,worker,output=fixture
    plan=release.prepare(input_path,client_dir=client,base=base,output=output)
    first=release.stage(plan,output=output,worker_dir=worker)
    original=Path(first['config']).read_bytes()
    make_config=release.deployment_config
    def next_config(release_id):
        config=make_config(release_id);config['compatibility_date']='2026-09-19';return config
    monkeypatch.setattr(release,'deployment_config',next_config)
    second=release.stage(plan,output=output,worker_dir=worker,reuse_bundle=Path(first['bundle']))
    assert first['bundle_id']!=second['bundle_id'] and first['config_hash']!=second['config_hash']
    assert first['catalog_hash']==second['catalog_hash'] and first['worker_files']==second['worker_files']
    assert Path(first['config']).read_bytes()==original


def test_staging_cannot_silently_generate_direct_mode(fixture,monkeypatch):
    base,input_path,client,worker,output=fixture
    plan=release.prepare(input_path,client_dir=client,base=base,output=output)
    make_config=release.deployment_config
    def bypass(release_id):
        config=make_config(release_id);config['vars']['LIVE_TRANSIT_MODE']='direct';return config
    monkeypatch.setattr(release,'deployment_config',bypass)
    with pytest.raises(ValueError,match='Broker deployment'):
        release.stage(plan,output=output,worker_dir=worker)
    assert not (output/'static-stage.json').exists() and not (output/'bundles').exists()


def test_quota_and_file_size_fail_before_staging(fixture,monkeypatch):
    base,input_path,client,_,output=fixture
    monkeypatch.setattr(release,'MAX_FILES',3)
    with pytest.raises(ValueError,match='count'):release.prepare(input_path,client_dir=client,base=base,output=output)
    monkeypatch.setattr(release,'MAX_FILES',20000)
    monkeypatch.setattr(release,'MAX_FILE_BYTES',20)
    with pytest.raises(ValueError,match='ceiling'):release.prepare(input_path,client_dir=client,base=base,output=output)
    assert not (output/'static-release-plan.json').exists()


def test_staging_excludes_vite_copied_secrets_and_local_configs(fixture):
    base,input_path,client,worker,output=fixture
    (worker/'.dev.vars').write_text('DATA_GO_KR_SERVICE_KEY=fixture-private-value',encoding='utf-8')
    (worker/'wrangler.json').write_text('{"local_only":true}',encoding='utf-8')
    (worker/'.vite').mkdir()
    (worker/'.vite'/'manifest.json').write_text('{}',encoding='utf-8')
    plan=release.prepare(input_path,client_dir=client,base=base,output=output)
    result=release.stage(plan,output=output,worker_dir=worker)
    assert [row['target'] for row in result['worker_files']]==['worker/index.js']
    staged=Path(result['bundle'])/'worker'
    assert not (staged/'.dev.vars').exists() and not (staged/'wrangler.json').exists()
    assert (worker/'.dev.vars').exists()


def test_changed_assets_and_catalog_never_receive_complete_receipt(fixture):
    base,input_path,client,worker,output=fixture
    plan=release.prepare(input_path,client_dir=client,base=base,output=output)
    (base/'roads'/'road.geojson').write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='changed'):release.stage(plan,output=output,worker_dir=worker)
    assert not (output/'static-stage.json').exists()


def test_search_shards_are_part_of_dependency_closure(fixture):
    base,_,_,_,_=fixture
    atomic_json(base/'bucket.json',{});atomic_json(base/'page.json',{})
    descriptor=lambda name:{'url':'/data/'+name,'sha256':digest(base/name),'byte_length':(base/name).stat().st_size}
    atomic_json(base/'search.json',{'schema_version':2,'buckets':[descriptor('bucket.json')],'pages':[descriptor('page.json')]})
    result=release.dependencies({'format':'search-index','url':'/data/search.json'},base)
    assert len(result)==3
    atomic_json(base/'page.json',{'tampered':True})
    with pytest.raises(ValueError,match='integrity'):release.dependencies({'format':'search-index','url':'/data/search.json'},base)


def test_national_transit_catalog_preserves_city_shards_in_deployment(fixture):
    base,input_path,client,_,output=fixture
    folder=base/'live-transit'/'routes'/'1234567890abcdef'
    shard=folder/'12.json'
    route={'id':'tago-12-sjb1','city_code':'12','route_id':'SJB1'}
    atomic_json(shard,{'schema_version':1,'city_code':'12','city_name':'세종','routes':[route]})
    ref=lambda p:{'url':'/data/'+p.relative_to(base).as_posix(),'sha256':digest(p),'byte_length':p.stat().st_size}
    city={'city_code':'12','city_name':'세종','status':'complete','location_service_supported':True,'route_count':1,**ref(shard)}
    manifest=folder/'manifest.json';atomic_json(manifest,{'schema_version':1,'cities':[city]})
    catalog=json.loads(input_path.read_bytes());catalog['live_transit_routes']=ref(manifest);atomic_json(input_path,catalog)
    plan=release.prepare(input_path,client_dir=client,base=base,output=output)
    targets={entry['target'] for entry in plan['files']}
    assert 'data/live-transit/routes/1234567890abcdef/manifest.json' in targets
    assert 'data/live-transit/routes/1234567890abcdef/12.json' in targets
    assert release.build_catalog(input_path,base)[0]['live_transit_routes']==ref(manifest)
    shard.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='integrity'):release.prepare(input_path,client_dir=client,base=base,output=output)


@pytest.mark.parametrize('entry',[
    {'city_code':'12','status':'failed','route_count':1,'location_service_supported':True},
    {'city_code':'12','status':'complete','url':'/data/escape.json','location_service_supported':True},
    {'city_code':'../escape','status':'failed','route_count':0,'location_service_supported':True},
])
def test_transit_manifest_rejects_false_or_unsafe_city_references(fixture,entry):
    base,_,_,_,_=fixture;manifest=base/'live-transit'/'routes'/'1234567890abcdef'/'manifest.json'
    atomic_json(manifest,{'schema_version':1,'cities':[entry]})
    ref={'url':'/data/'+manifest.relative_to(base).as_posix(),'sha256':digest(manifest),'byte_length':manifest.stat().st_size}
    with pytest.raises(ValueError):release.transit_dependencies(ref,base)


def staged_fixture(fixture):
    base,input_path,client,worker,output=fixture
    plan=release.prepare(input_path,client_dir=client,base=base,output=output)
    first=release.stage(plan,output=output,worker_dir=worker)
    bundle=Path(first['bundle'])
    before={p.relative_to(bundle).as_posix():p.read_bytes() for p in bundle.rglob('*') if p.is_file()}
    (worker/'index.js').write_text('export default {version:2};',encoding='utf-8')
    return plan,first,bundle,before


def test_stage_rejects_reusing_a_public_data_url_with_different_bytes(fixture):
    _,_,_,worker,output=fixture
    plan,_,bundle,_=staged_fixture(fixture)
    source=next(entry for entry in plan['files'] if entry['target'].startswith('data/'))
    changed={**plan,'files':[{**entry,'sha256':'f'*64} if entry is source else entry for entry in plan['files']]}
    before=set((output/'bundles').iterdir())
    with pytest.raises(ValueError,match='Immutable public data URL changed'):
        release.stage(changed,output=output,worker_dir=worker,reuse_bundle=bundle)
    assert set((output/'bundles').iterdir())==before


def test_reuses_only_immutable_client_files_and_preserves_previous_bundle(fixture):
    _,_,_,worker,output=fixture
    (worker/'shared.js').write_text('export const shared=1;',encoding='utf-8')
    plan,first,bundle,before=staged_fixture(fixture)
    second=release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    new=Path(second['bundle']);assert new!=bundle
    for entry in plan['files']:
        assert os.path.samefile(new/'client'/entry['target'],bundle/'client'/entry['target'])
        assert not os.path.samefile(new/'client'/entry['target'],entry['path'])
        assert digest(new/'client'/entry['target'])==entry['sha256']
    assert second['staging']['linked_files']==len(plan['files'])
    assert second['staging']['linked_bytes']==sum(entry['bytes'] for entry in plan['files'])
    assert second['staging']['copied_files']==0
    assert second['staging']['reuse_bundle']=={'bundle_id':first['bundle_id'],'receipt_hash':digest(bundle/'receipt.json')}
    for relative in ['worker/shared.js','wrangler.json','asset-manifest.json','receipt.json']:
        assert not os.path.samefile(new/relative,bundle/relative)
    assert {p.relative_to(bundle).as_posix():p.read_bytes() for p in bundle.rglob('*') if p.is_file()}==before
    assert json.loads((new/'receipt.json').read_bytes())==second


def test_reuse_falls_back_to_exclusive_copy_when_hardlinks_are_unavailable(fixture,monkeypatch):
    _,_,_,worker,output=fixture;plan,_,bundle,before=staged_fixture(fixture)
    def cross_volume(*args,**kwargs):raise OSError(errno.EXDEV,'fixture cross-volume boundary')
    monkeypatch.setattr(release.os,'link',cross_volume)
    second=release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle);new=Path(second['bundle'])
    assert second['staging']['copied_files']==len(plan['files']) and second['staging']['linked_files']==0
    for entry in plan['files']:
        assert not os.path.samefile(new/'client'/entry['target'],bundle/'client'/entry['target'])
        assert digest(new/'client'/entry['target'])==entry['sha256']
    assert {p.relative_to(bundle).as_posix():p.read_bytes() for p in bundle.rglob('*') if p.is_file()}==before


@pytest.mark.parametrize('changed',['client/data/roads/road.geojson','worker/index.js','asset-manifest.json','wrangler.json'])
def test_reuse_rejects_changed_source_bundle_without_creating_a_new_bundle(fixture,changed):
    _,_,_,worker,output=fixture;plan,_,bundle,_=staged_fixture(fixture)
    path=bundle/changed;content=path.read_bytes();path.write_bytes(bytes([content[0]^1])+content[1:])
    with pytest.raises(ValueError,match='changed|hash'):release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    assert list((output/'bundles').iterdir())==[bundle]
    assert path.read_bytes()==bytes([content[0]^1])+content[1:]


@pytest.mark.parametrize('changed',['missing','extra','incomplete-receipt','unsafe-path'])
def test_reuse_rejects_invalid_prior_bundle_contract(fixture,changed):
    _,_,_,worker,output=fixture;plan,_,bundle,_=staged_fixture(fixture)
    if changed=='missing':
        # Only remove this disposable fixture, never an actual project artifact.
        (bundle/'client/index.html').unlink()
    elif changed=='extra':(bundle/'client/extra.txt').write_text('fixture',encoding='utf-8')
    elif changed=='incomplete-receipt':
        receipt=json.loads((bundle/'receipt.json').read_bytes());receipt['complete']=False;atomic_json(bundle/'receipt.json',receipt)
    else:
        manifest=json.loads((bundle/'asset-manifest.json').read_bytes());manifest[0]['target']='../escape.txt';atomic_json(bundle/'asset-manifest.json',manifest)
        receipt=json.loads((bundle/'receipt.json').read_bytes());receipt['manifest_hash']=digest(bundle/'asset-manifest.json');atomic_json(bundle/'receipt.json',receipt)
    with pytest.raises(ValueError):release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    assert list((output/'bundles').iterdir())==[bundle]


def test_reuse_does_not_mask_a_changed_prepared_public_source(fixture):
    base,_,_,worker,output=fixture;plan,first,bundle,before=staged_fixture(fixture)
    (base/'roads/road.geojson').write_bytes(b'changed public fixture')
    with pytest.raises(ValueError,match='Source changed'):release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    assert json.loads((output/'static-stage.json').read_bytes())==first
    assert {p.relative_to(bundle).as_posix():p.read_bytes() for p in bundle.rglob('*') if p.is_file()}==before


def test_resume_reuses_valid_linked_targets_without_writing_previous_inodes(fixture,monkeypatch):
    _,_,_,worker,output=fixture;plan,_,bundle,before=staged_fixture(fixture)
    second=release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle);new=Path(second['bundle'])
    # A valid unfinished bundle is safe to resume; only fixture metadata is removed.
    (new/'receipt.json').unlink()
    def unexpected_link(*args,**kwargs):raise AssertionError('Valid existing targets should not be linked again')
    monkeypatch.setattr(release.os,'link',unexpected_link)
    resumed=release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    assert resumed['staging']['resumed_files']==len(plan['files'])
    assert resumed['staging']['linked_files']==resumed['staging']['copied_files']==0
    assert {p.relative_to(bundle).as_posix():p.read_bytes() for p in bundle.rglob('*') if p.is_file()}==before


@pytest.mark.parametrize('target',['client/index.html','client/_headers','client/data/catalog.json','worker/index.js','wrangler.json','asset-manifest.json'])
def test_existing_incomplete_or_changed_targets_are_never_overwritten(fixture,target):
    _,_,_,worker,output=fixture;plan,first,bundle,_=staged_fixture(fixture)
    second=release.stage(plan,output=output,worker_dir=worker);new=Path(second['bundle'])
    damaged=new/target;damaged.write_bytes(b'fixture incomplete')
    before={p.relative_to(bundle).as_posix():p.read_bytes() for p in bundle.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='Existing staged file'):release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    assert damaged.read_bytes()==b'fixture incomplete'
    assert {p.relative_to(bundle).as_posix():p.read_bytes() for p in bundle.rglob('*') if p.is_file()}==before
    assert json.loads((output/'static-stage.json').read_bytes())==second
    assert first['bundle_id']!=second['bundle_id']


def test_incomplete_hardlinked_target_is_rejected_without_truncating_its_other_name(fixture):
    _,_,_,worker,output=fixture;plan,_,bundle,_=staged_fixture(fixture)
    second=release.stage(plan,output=output,worker_dir=worker);new=Path(second['bundle']);target=new/'client/index.html'
    # Replace only disposable fixture content with a deliberately wrong hardlink.
    target.unlink();origin=new.parent/'fixture-preserve.txt';origin.write_bytes(b'fixture data must survive');os.link(origin,target)
    with pytest.raises(ValueError,match='Existing staged file'):release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    assert target.read_bytes()==origin.read_bytes()==b'fixture data must survive'
    assert os.path.samefile(origin,target)


def test_reuse_budget_charges_new_payload_only_and_retains_30gib(fixture,monkeypatch):
    _,_,client,worker,output=fixture
    (client/'large-static.bin').write_bytes(b'a'*(1024*1024))
    plan,first,bundle,before=staged_fixture(fixture)
    free=release.DISK_RESERVE_BYTES+64*1024
    assert free < release.DISK_RESERVE_BYTES+plan['bytes']
    assert release.DISK_RESERVE_BYTES==30*1024**3
    monkeypatch.setattr(release.shutil,'disk_usage',lambda path:SimpleNamespace(free=free))
    result=release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    assert result['bundle_id']!=first['bundle_id']
    assert result['staging']['copied_files']==0
    assert result['staging']['linked_files']==len(plan['files'])
    assert os.path.samefile(Path(result['bundle'])/'client/large-static.bin',bundle/'client/large-static.bin')
    assert {p.relative_to(bundle).as_posix():p.read_bytes() for p in bundle.rglob('*') if p.is_file()}==before


def test_new_asset_bytes_cannot_spend_reserved_space_before_stage_creation(fixture,monkeypatch):
    base,input_path,client,worker,output=fixture
    _,first,bundle,before=staged_fixture(fixture)
    (client/'new-static.bin').write_bytes(b'b'*(1024*1024))
    plan=release.prepare(input_path,client_dir=client,base=base,output=output)
    monkeypatch.setattr(release.shutil,'disk_usage',lambda path:SimpleNamespace(free=release.DISK_RESERVE_BYTES+64*1024))
    with pytest.raises(ValueError,match='30 GiB disk reserve'):
        release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    assert list((output/'bundles').iterdir())==[bundle]
    assert json.loads((output/'static-stage.json').read_bytes())==first
    assert {p.relative_to(bundle).as_posix():p.read_bytes() for p in bundle.rglob('*') if p.is_file()}==before


def test_cross_volume_assets_are_budgeted_as_copies_before_attempting_links(fixture,monkeypatch):
    _,_,client,worker,output=fixture
    (client/'large-static.bin').write_bytes(b'c'*(1024*1024))
    plan,first,bundle,_=staged_fixture(fixture)
    monkeypatch.setattr(release,'_same_volume',lambda source,device:False)
    monkeypatch.setattr(release.shutil,'disk_usage',lambda path:SimpleNamespace(free=release.DISK_RESERVE_BYTES+64*1024))
    monkeypatch.setattr(release.os,'link',lambda *args,**kwargs:pytest.fail('Insufficient cross-volume copy budget must fail first'))
    with pytest.raises(ValueError,match='30 GiB disk reserve'):
        release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    assert list((output/'bundles').iterdir())==[bundle]
    assert json.loads((output/'static-stage.json').read_bytes())==first


def test_failed_hardlink_rechecks_copy_budget_without_truncating_prior_data(fixture,monkeypatch):
    _,_,client,worker,output=fixture
    (client/'large-static.bin').write_bytes(b'd'*(1024*1024))
    plan,first,bundle,before=staged_fixture(fixture)
    monkeypatch.setattr(release.shutil,'disk_usage',lambda path:SimpleNamespace(free=release.DISK_RESERVE_BYTES+64*1024))
    def failed_link(*args,**kwargs):raise OSError(errno.EPERM,'fixture unsupported hardlinks')
    monkeypatch.setattr(release.os,'link',failed_link)
    with pytest.raises(ValueError,match='30 GiB disk reserve'):
        release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    incomplete=next(p for p in (output/'bundles').iterdir() if p!=bundle)
    assert not (incomplete/'client/large-static.bin').exists()
    assert not (incomplete/'receipt.json').exists()
    assert json.loads((output/'static-stage.json').read_bytes())==first
    assert {p.relative_to(bundle).as_posix():p.read_bytes() for p in bundle.rglob('*') if p.is_file()}==before


def test_four_fallback_copies_cannot_spend_the_same_free_bytes(tmp_path,monkeypatch):
    source=tmp_path/'source.bin';source.write_bytes(b'e'*100)
    sha=digest(source);disk=release._StageDiskBudget(tmp_path)
    state={'free':release.DISK_RESERVE_BYTES+250,'active':0,'peak':0};state_lock=Lock();start=Barrier(4)
    original=release._copy_new
    monkeypatch.setattr(release.shutil,'disk_usage',lambda path:SimpleNamespace(free=state['free']))
    def tracked_copy(*args):
        with state_lock:
            state['active']+=1;state['peak']=max(state['peak'],state['active'])
        try:
            time.sleep(.01)
            original(*args)
            with state_lock:state['free']-=100
        finally:
            with state_lock:state['active']-=1
    monkeypatch.setattr(release,'_copy_new',tracked_copy)
    def run(index):
        start.wait()
        try:disk.copy(source,tmp_path/f'copy-{index}.bin',100,sha);return True
        except ValueError as error:
            assert '30 GiB disk reserve' in str(error)
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run,range(4)))
    assert results.count(True)==2 and results.count(False)==2
    assert state['peak']==1 and state['free']==release.DISK_RESERVE_BYTES+50
    assert len(list(tmp_path.glob('copy-*.bin')))==2


def test_metadata_and_operational_pointer_each_recheck_current_space(tmp_path,monkeypatch):
    disk=release._StageDiskBudget(tmp_path)
    target=tmp_path/'receipt.json';pointer=tmp_path/'static-stage.json'
    atomic_json(pointer,{'preserve':'old'})
    old=pointer.read_bytes()
    monkeypatch.setattr(release.shutil,'disk_usage',lambda path:SimpleNamespace(free=release.DISK_RESERVE_BYTES+1))
    with pytest.raises(ValueError,match='30 GiB disk reserve'):disk.write(target,b'new metadata')
    with pytest.raises(ValueError,match='30 GiB disk reserve'):disk.pointer(pointer,{'new':'release'})
    assert not target.exists() and pointer.read_bytes()==old
    assert not list(tmp_path.glob('*.tmp'))


def test_metadata_failure_after_assets_does_not_mark_a_bundle_complete(fixture,monkeypatch):
    _,_,_,worker,output=fixture
    plan,first,bundle,before=staged_fixture(fixture)
    state={'free':release.DISK_RESERVE_BYTES+1024*1024}
    monkeypatch.setattr(release.shutil,'disk_usage',lambda path:SimpleNamespace(free=state['free']))
    original=release._copy_new
    def changed_free_space(source,target,size,sha):
        original(source,target,size,sha)
        if target.parent.name=='worker':state['free']=release.DISK_RESERVE_BYTES-1
    monkeypatch.setattr(release,'_copy_new',changed_free_space)
    with pytest.raises(ValueError,match='30 GiB disk reserve'):
        release.stage(plan,output=output,worker_dir=worker,reuse_bundle=bundle)
    incomplete=next(p for p in (output/'bundles').iterdir() if p!=bundle)
    assert not (incomplete/'receipt.json').exists()
    assert json.loads((output/'static-stage.json').read_bytes())==first
    assert {p.relative_to(bundle).as_posix():p.read_bytes() for p in bundle.rglob('*') if p.is_file()}==before


def test_concurrently_grown_source_cannot_write_beyond_checked_copy_size(tmp_path):
    source=tmp_path/'growing.bin';target=tmp_path/'partial.bin'
    source.write_bytes(b'0123456789')
    with pytest.raises(ValueError,match='Source changed while copying'):
        release._copy_new(source,target,3,'0'*64)
    assert target.read_bytes()==b'012' and source.read_bytes()==b'0123456789'
