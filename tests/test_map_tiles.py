import gzip
import hashlib
import json

import mapbox_vector_tile
import pytest
from pmtiles.reader import MemorySource,Reader
from pmtiles.tile import zxy_to_tileid
from shapely.geometry import box,LineString,Polygon

from pipeline.admin_boundaries import bounded_coverage,convert_features
from pipeline.map_tiles import (HARD,archive_bytes,pack_archives,tile_bounds,encode_tile,
    stable_id,open_work,add_record,pack_details,source_features,classify,build_topic_tiles,tile_rows,compact_tile_ids,verify_display_ids)


def test_binary_archive_roundtrip_is_deterministic_and_keeps_absent_tiles_absent():
    z,x,y=12,3493,1583
    bounds=tile_bounds(z,x,y);g=box(bounds[0]+20,bounds[1]+20,bounds[2]-20,bounds[3]-20)
    sid=stable_id('osm','way/한글')
    body,_,ids=encode_tile('water',[(sid,g,{'name':'검증 호수'})],z,x,y)
    data=archive_bytes([(zxy_to_tileid(z,x,y),body)],'water',[126,36,128,38])
    assert data==archive_bytes([(zxy_to_tileid(z,x,y),body)],'water',[126,36,128,38])
    reader=Reader(MemorySource(data));assert reader.get(z,x,y)==body
    assert reader.get(z,x+1,y) is None
    features=mapbox_vector_tile.decode(gzip.decompress(body))['water']['features']
    assert ids=={sid} and features[0]['properties']['stable_id']==sid[:16]
    assert features[0]['properties']['name']=='검증 호수'


def test_small_source_polygon_stays_accessible_as_explicit_anchor():
    z,x,y=8,219,98;b=tile_bounds(z,x,y);cx=(b[0]+b[2])/2;cy=(b[1]+b[3])/2
    sid=stable_id('overture','tiny')
    body,notices,ids=encode_tile('buildings',[(sid,box(cx,cy,cx+.001,cy+.001),{})],z,x,y)
    assert body and ids=={sid} and notices['subpixel_anchor']==1
    decoded=mapbox_vector_tile.decode(gzip.decompress(body))['buildings']['features']
    assert decoded[0]['geometry']['type']=='Point'
    assert decoded[0]['properties']['representation']=='subpixel_anchor'


def test_polygon_validity_is_checked_after_the_final_integer_quantization():
    z,x,y=7,109,49;b=tile_bounds(z,x,y);unit=(b[2]-b[0])/8192
    ring=[(0,0),(2,0),(2,2),(1.49,2),(1.49,.49),(.51,.49),(.51,2),(0,2),(0,0)]
    original=Polygon([(b[0]+(u+2000)*unit,b[1]+(v+4000)*unit) for u,v in ring]);before=original.wkb
    assert original.is_valid
    sid=stable_id('osm','narrow-lake');body,notices,ids=encode_tile('water',[(sid,original,{})],z,x,y)
    assert ids=={sid} and notices['quantized_outline']==1 and original.wkb==before
    decoded=mapbox_vector_tile.decode(gzip.decompress(body))['water']['features']
    assert decoded[0]['geometry']['type'] in ('LineString','MultiLineString')


def test_tile_clipping_keeps_source_id_on_both_sides():
    z,x,y=12,3493,1583;b=tile_bounds(z,x,y);sid=stable_id('osm','crossing')
    geometry=LineString([(b[2]-100,(b[1]+b[3])/2),(b[2]+100,(b[1]+b[3])/2)])
    for column in (x,x+1):
        body,_,ids=encode_tile('roads',[(sid,geometry,{'highway':'primary'})],z,column,y)
        assert body and ids=={sid}


def test_projected_road_fragment_collapsing_to_equal_doubles_keeps_its_id():
    z,x,y=14,13976,6328;b=tile_bounds(z,x,y);cx=(b[0]+b[2])/2;cy=(b[1]+b[3])/2
    for delta in (0,1e-9):
        geometry=LineString([(cx,cy),(cx+delta,cy)]);before=geometry.wkb
        sid=stable_id('osm','tiny-fragment')
        body,notices,ids=encode_tile('detail-roads',[(sid,geometry,{'highway':'service'})],z,x,y)
        assert body and ids=={sid} and notices['subpixel_line_anchor']==1
        decoded=mapbox_vector_tile.decode(gzip.decompress(body))['detail-roads']['features']
        assert decoded[0]['geometry']['type']=='Point'
        assert geometry.wkb==before


def test_single_oversized_archive_is_rejected_and_pack_is_bounded(tmp_path):
    with pytest.raises(ValueError,match='1 MiB'):
        archive_bytes([(100,b'x'*HARD)],'roads',[126,36,128,38])
    tiles=[(100+i,hashlib.shake_256(str(i).encode()).digest(40000)) for i in range(12)]
    refs=pack_archives(tiles,'roads',[126,36,128,38],tmp_path,'/data/test')
    assert len(refs)>1 and all(r['byte_length']<=HARD for r in refs)
    assert sum(r['tile_count'] for r in refs)==12
    assert all(a['last_tile_id']<b['first_tile_id'] for a,b in zip(refs,refs[1:]))
    for ref in refs:
        data=(tmp_path/ref['url'].split('/')[-1]).read_bytes()
        assert hashlib.sha256(data).hexdigest()==ref['sha256']


def test_selection_shards_preserve_nested_quality_properties_and_original_id(tmp_path):
    db=open_work(tmp_path/'work.sqlite','test')
    props={'name':'건물 이름','raw_height':.01,'render_height':None,'render_eligible':False,
        'quality_flags':['height_conflict'],'nested':{'source':[1,None,'한글']}}
    assert add_record(db,'buildings','원본/0001','overture','release',props,box(0,0,2,2),'asset','f'*64)
    assert not add_record(db,'buildings','원본/0001','overture','release',props,box(0,0,2,2),'asset','f'*64)
    with pytest.raises(ValueError,match='Conflicting'):
        add_record(db,'buildings','원본/0001','overture','release',{**props,'name':'changed'},box(0,0,2,2),'asset','f'*64)
    refs=pack_details(db,'buildings',tmp_path/'details','/data/test')
    rows=json.loads(gzip.decompress((tmp_path/'details'/refs[0]['url'].split('/')[-1]).read_bytes()))['records']
    assert rows[0]['properties']==props and rows[0]['source_record_id']=='원본/0001'
    assert rows[0]['stable_id']==stable_id('overture','원본/0001')
    db.close()


def test_compact_source_attributes_restore_without_mutating_geometry():
    geometry={'type':'LineString','coordinates':[[127,37],[128,37]]}
    document={'features':[{'id':'a','geometry':geometry,'properties':{'metadata_index':0,'name':'원래 이름'}}],
        'metadata':{'schema_version':1,'shared':{'source':'공유'},'rows':[{'nested':[1,2]}]}}
    feature,props=next(source_features(document))
    assert feature['geometry'] is geometry and props=={'name':'원래 이름','source':'공유','nested':[1,2]}
    assert document['features'][0]['properties']['metadata_index']==0


def test_admin_source_metadata_does_not_impersonate_compact_dictionary():
    rows=[{'BASE_DATE':'20250630','SIGUNGU_CD':'11010','SIGUNGU_NM':'테스트'}]
    result,_=convert_features(rows,[box(950000,1950000,950500,1950500)],5179,'sigungu',0,0)
    assert 'metadata' not in result and result['source_metadata']['reference_date']=='2025-06-30'


def test_shared_arc_calibration_is_measured_and_does_not_revert_the_entire_level():
    left=Polygon([(0,0),(0,10),(5,10),(5.2,7),(5,5),(5.2,3),(5,0),(0,0)])
    right=Polygon([(5,0),(5.2,3),(5,5),(5.2,7),(5,10),(10,10),(10,0),(5,0)])
    result,audit=bounded_coverage([left,right],.5)
    import shapely
    assert shapely.coverage_is_valid(result)
    assert audit['maximum_curve_displacement_bound_m']<=.5
    assert audit['source_ring_count']==audit['output_ring_count']==2
    assert len(audit['per_feature_tolerance_m'])==2


def test_checkpoint_refuses_different_input_without_replacing_records(tmp_path):
    path=tmp_path/'work.sqlite';db=open_work(path,'first');db.close()
    with pytest.raises(ValueError,match='another input'):open_work(path,'second')


def test_distinct_clipped_shapes_keep_the_same_original_id_without_overwriting(tmp_path):
    db=open_work(tmp_path/'work.sqlite','test')
    for suffix,geometry in [('left',box(0,0,1,1)),('right',box(1,0,2,1))]:
        add_record(db,'roads','original/123','osm','v',{'name':'같은 원천 도로'},geometry,'asset',suffix,
            display_identity='original/123\0geometry:'+suffix)
    refs=pack_details(db,'roads',tmp_path/'out','/data/test')
    rows=json.loads(gzip.decompress((tmp_path/'out'/refs[0]['url'].split('/')[-1]).read_bytes()))['records']
    assert len(rows)==2 and len({r['stable_id'] for r in rows})==2
    assert {r['source_record_id'] for r in rows}=={'original/123'}
    assert {r['identity_scope'] for r in rows}=={'source_record_and_geometry_fragment'}
    db.close()


def test_legacy_terrain_road_is_not_misclassified_as_water():
    assert classify({'id':'road-daejeon','layer':'terrain','source_id':'osm'}, {},LineString([(0,0),(1,1)]))[0]=='roads'


def test_spatial_tile_lookup_and_maxzoom_coverage_use_bounded_record_bitmap(tmp_path,monkeypatch):
    import pipeline.map_tiles as m
    monkeypatch.setitem(m.TOPICS,'roads',(5,5))
    db=open_work(tmp_path/'work.sqlite','test');b=tile_bounds(5,27,12)
    g=LineString([(b[0]+10,b[1]+10),(b[0]+100,b[1]+100)])
    add_record(db,'roads','one','osm','v',{},g,'source','a'*64,minzoom=5)
    assert len(tile_rows(db,'roads',5,b))==1
    assert not tile_rows(db,'roads',5,tile_bounds(5,20,12))
    audit=build_topic_tiles(db,'roads',tmp_path)['5']
    assert audit['every_source_id_represented'] is True
    assert audit['represented_feature_count']==1 and audit['coverage_bitmap_bytes']==2
    assert build_topic_tiles(db,'roads',tmp_path)['5']==audit
    db.close()


def test_display_prefix_is_collision_checked_across_the_entire_topic(tmp_path):
    db=open_work(tmp_path/'work.sqlite','test')
    for i in range(2):add_record(db,'roads',str(i),'osm','v',{},box(i,0,i+1,1),'source','a'*64)
    assert verify_display_ids(db,'roads')==2
    db.execute('UPDATE records SET stable=? WHERE n=1',('a'*16+'0'*48,))
    db.execute('UPDATE records SET stable=? WHERE n=2',('a'*16+'f'*48,))
    with pytest.raises(ValueError,match='prefix collision'):verify_display_ids(db,'roads')
    db.close()


def test_reused_mvt_identity_rewrite_keeps_geometry_commands_and_other_properties():
    from mapbox_vector_tile.Mapbox import vector_tile_pb2
    full=stable_id('sgis','province')
    payload=mapbox_vector_tile.encode({'name':'admin-sido','features':[{'geometry':box(1,2,10,20),'properties':{'stable_id':full,'name':'원본 명칭','source_id':'sgis'}}]})
    before=vector_tile_pb2.tile();before.ParseFromString(payload)
    body=compact_tile_ids(gzip.compress(payload,mtime=0))
    after=vector_tile_pb2.tile();after.ParseFromString(gzip.decompress(body))
    assert list(before.layers[0].features[0].geometry)==list(after.layers[0].features[0].geometry)
    decoded=mapbox_vector_tile.decode(gzip.decompress(body))['admin-sido']['features'][0]
    assert decoded['properties']=={'stable_id':full[:16],'name':'원본 명칭','source_id':'sgis'}


def test_readonly_checkpoint_fork_keeps_records_and_rebuilds_only_changed_road_policy(tmp_path):
    import pipeline.map_tiles as m
    from pipeline.admin_boundaries import encoded
    donor=tmp_path/'donor';donor.mkdir()
    inputs={'version':'map-tiles-1.3','display_id_hex_length':16,'source_catalog_sha256':'a'*64,'admin_sha256':'b'*64,
        'levels':[],'assets':[['source','f'*64]],'proof_bbox':None,'source_profile':'national-existing-overview-basemap','topics':m.TOPICS}
    (donor/'inputs.json').write_bytes(encoded(inputs));old=open_work(donor/'index.sqlite',m.sha(encoded(inputs)))
    add_record(old,'roads','road','osm','v',{'highway':'trunk'},box(0,0,1,1),'source','c'*64,minzoom=7)
    add_record(old,'land','land','osm','v',{},box(2,2,3,3),'source','d'*64,minzoom=5)
    old.execute('INSERT INTO sources VALUES(?,?,?)',('source','f'*64,2))
    for topic in ('roads','land'):
        old.execute('INSERT INTO tiles VALUES(?,?,?)',(topic,100,b'unchanged-'+topic.encode()))
        old.execute('INSERT INTO stages VALUES(?,?)',(topic+'-z5','{}'))
    old.commit();before=old.execute('SELECT n,topic,stable,geom,record,render FROM records ORDER BY n').fetchall();old.close()
    current={**inputs,'assets':[('source','f'*64)],'version':m.VERSION};destination=open_work(tmp_path/'new.sqlite','new')
    m.fork_index(destination,donor,current)
    assert destination.execute('SELECT n,topic,stable,geom,record,render FROM records ORDER BY n').fetchall()==before
    assert destination.execute("SELECT minzoom FROM records WHERE topic='roads'").fetchone()==(9,)
    assert destination.execute("SELECT COUNT(*) FROM tiles WHERE topic='roads'").fetchone()==(0,)
    assert destination.execute("SELECT body FROM tiles WHERE topic='land'").fetchone()==(b'unchanged-land',)
    import sqlite3
    check=sqlite3.connect(f'file:{(donor/"index.sqlite").as_posix()}?mode=ro',uri=True)
    assert check.execute("SELECT minzoom FROM records WHERE topic='roads'").fetchone()==(7,)
    assert check.execute('SELECT n,topic,stable,geom,record,render FROM records ORDER BY n').fetchall()==before
    check.close();destination.close()
    empty=open_work(tmp_path/'bad.sqlite','bad')
    with pytest.raises(ValueError,match='source contract'):m.fork_index(empty,donor,{**current,'assets':[['different','e'*64]]})
    assert empty.execute('SELECT COUNT(*) FROM records').fetchone()==(0,)
    empty.close()
