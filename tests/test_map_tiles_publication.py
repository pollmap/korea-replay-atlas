import json
import gzip
import hashlib

import pytest
from shapely.geometry import box

from pipeline import map_tiles_publication as publication
from pipeline.admin_boundaries import encoded
from pipeline.map_tiles import open_work,add_record,tile_bounds,encode_tile,stable_id
from pmtiles.tile import zxy_to_tileid


def source_work(tmp_path,monkeypatch,represented=2):
    catalog=tmp_path/'source.json';catalog.write_bytes(encoded({'release_id':'fixture','sources':[]}))
    monkeypatch.setattr(publication,'SOURCE_CATALOG',catalog)
    inputs={'source_catalog_sha256':hashlib.sha256(catalog.read_bytes()).hexdigest(),'admin_sha256':'a'*64,'proof_bbox':None}
    work=tmp_path/'work';work.mkdir();(work/'inputs.json').write_bytes(encoded(inputs))
    db=open_work(work/'index.sqlite',hashlib.sha256(encoded(inputs)).hexdigest())
    z,x,y=5,27,12;b=tile_bounds(z,x,y);rows=[]
    for i in range(2):
        g=box(b[0]+i*100+10,b[1]+10,b[0]+i*100+80,b[1]+80)
        props={'name':f'시도 {i}','admin_code':str(i),'legal_dong_code':None}
        add_record(db,'admin-sido',str(i),'sgis','20250630',props,g,'source','f'*64)
        rows.append((stable_id('sgis',str(i)),g,props))
    body,_,_=encode_tile('admin-sido',rows,z,x,y)
    db.execute('INSERT INTO tiles VALUES(?,?,?)',('admin-sido',zxy_to_tileid(z,x,y),body))
    db.execute('INSERT INTO stages VALUES(?,?)',('admin-sido-z5',json.dumps({'tile_count':1,'represented_feature_count':represented})))
    db.commit();db.close();return work


def test_committed_subset_has_accurate_zooms_records_hashes_and_no_unfinished_topic(tmp_path,monkeypatch):
    work=source_work(tmp_path,monkeypatch);output=tmp_path/'candidate'
    before=(work/'inputs.json').read_bytes()
    result=publication.publish_completed(work,output)
    c=json.loads((output/result['map_catalog']['path']).read_bytes())
    assert len(c['topics'])==1 and c['topics'][0]['id']=='admin-sido'
    assert c['topics'][0]['maxzoom']==5 and c['topics'][0]['feature_count']==2
    assert 'admin-sigungu' in c['coverage']['excluded_topics']
    for ref in result['files']:
        data=(output/ref['path']).read_bytes()
        assert len(data)==ref['byte_length'] and hashlib.sha256(data).hexdigest()==ref['sha256']
    rows=json.loads(gzip.decompress((output/c['topics'][0]['details'][0]['url'].lstrip('/')).read_bytes()))['records']
    assert {r['source_record_id'] for r in rows}=={'0','1'}
    assert all(r['properties']['legal_dong_code'] is None for r in rows)
    assert (work/'inputs.json').read_bytes()==before
    with pytest.raises(ValueError,match='new directory'):publication.publish_completed(work,output)


def test_unrepresented_partial_topic_cannot_be_promoted(tmp_path,monkeypatch):
    work=source_work(tmp_path,monkeypatch,represented=1);output=tmp_path/'candidate'
    with pytest.raises(ValueError,match='all its indexed source'):publication.publish_completed(work,output)
    assert not output.exists()
