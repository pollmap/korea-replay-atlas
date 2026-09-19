"""Small synthetic coastlines; no network or nationwide data processing."""
from copy import deepcopy
import json
import pytest
from pipeline.coastline import build_landmask, read_coastline_snapshot, stage_dokdo
from pipeline.core import digest


def fixture():
    ways=[{'id':10,'version':3,'timestamp':'2026-09-16T00:00:00Z','refs':[1,2,3,4,1],
           'tags':{'natural':'coastline','name:ko':'합성 섬','source':'synthetic fixture'}}]
    points={1:(131.86,37.24),2:(131.861,37.24),3:(131.861,37.241),4:(131.86,37.241)}
    metadata={'url':'https://example.invalid/fixture.osm','retrieved_at':'2026-09-16T00:00:00Z'}
    return ways,points,metadata


def build(ways,points,metadata):
    return build_landmask(ways,points,metadata,'a'*64,required_ids={10})


def test_exact_source_vertices_and_provenance_survive_without_heights():
    ways,points,metadata=fixture();before=deepcopy((ways,points,metadata))
    result,audit=build(ways,points,metadata);feature=result['features'][0];p=feature['properties']
    assert (ways,points,metadata)==before
    assert feature['geometry']['coordinates']==[[list(points[ref]) for ref in ways[0]['refs']]]
    assert p['source_record_id']=='way/10' and p['source_version']==3 and p['source_tags']==ways[0]['tags']
    assert p['source_node_ids']==ways[0]['refs'] and p['evidence_type']=='source_attribute'
    assert p['height_m'] is None and p['vertical_geometry']=='terrain_draped_display_only'
    assert audit['source_vertices_changed']==audit['height_values_created']==0


@pytest.mark.parametrize('case,reason',[
    ('open','Open'),('missing','Missing'),('clockwise','orientation'),('crossed','Invalid'),
    ('nan','finite'),('height','2D'),('outside','region'),('duplicate','Duplicate'),('tag','non-coastline')])
def test_invalid_coastlines_are_not_repaired_or_published(case,reason):
    ways,points,metadata=fixture()
    if case=='open':ways[0]['refs']=ways[0]['refs'][:-1]
    elif case=='missing':del points[3]
    elif case=='clockwise':ways[0]['refs']=list(reversed(ways[0]['refs']))
    elif case=='crossed':ways[0]['refs']=[1,3,2,4,1]
    elif case=='nan':points[3]=(float('nan'),37.241)
    elif case=='height':points[3]=(131.861,37.241,-20)
    elif case=='outside':points[3]=(132.,37.241)
    elif case=='duplicate':ways.append(deepcopy(ways[0]))
    elif case=='tag':ways[0]['tags']['natural']='water'
    with pytest.raises(ValueError,match=reason):build(ways,points,metadata)


def test_missing_main_islands_and_overlapping_coasts_fail_closed():
    ways,points,metadata=fixture()
    with pytest.raises(ValueError,match='Required'):build_landmask(ways,points,metadata,'a'*64)
    duplicate=deepcopy(ways[0]);duplicate['id']=11;ways.append(duplicate)
    with pytest.raises(ValueError,match='Overlapping'):build(ways,points,metadata)


def test_sparse_snapshot_extraction_preserves_main_islands_and_catalog(tmp_path):
    source=tmp_path/'fixture.osm';public=tmp_path/'public';public.mkdir()
    (public/'catalog.json').write_text('preserved catalog',encoding='utf-8')
    (public/'fixture.terrain').write_bytes(b'preserved negative-elevation terrain')
    xml='''<osm version="0.6">
      <node id="1" lat="37.24" lon="131.86"/><node id="2" lat="37.24" lon="131.861"/>
      <node id="3" lat="37.241" lon="131.861"/><node id="4" lat="37.241" lon="131.86"/>
      <node id="5" lat="37.24" lon="131.87"/><node id="6" lat="37.24" lon="131.871"/>
      <node id="7" lat="37.241" lon="131.871"/><node id="8" lat="37.241" lon="131.87"/>
      <node id="9" lat="36.0" lon="127.0"/>
      <way id="456788679" version="1" timestamp="2026-09-16T00:00:00Z"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/><tag k="natural" v="coastline"/></way>
      <way id="456788681" version="2" timestamp="2026-09-16T00:00:00Z"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/><tag k="natural" v="coastline"/></way>
      <way id="20" version="1"><nd ref="9"/><nd ref="1"/><tag k="natural" v="coastline"/></way>
    </osm>'''
    source.write_text(xml,encoding='utf-8');original_hash=digest(source)
    source.with_suffix('.osm.meta.json').write_text(json.dumps({'url':'https://example.invalid/fixture.osm',
        'retrieved_at':'2026-09-16T00:00:00Z','sha256':original_hash,'bytes':source.stat().st_size}),encoding='utf-8')
    ways,points,scan=read_coastline_snapshot(source)
    assert len(ways)==2 and len(points)==8 and scan['coastline_ways_scanned']==3
    report=stage_dokdo(source,public=public,audit=tmp_path/'audit');asset=report['asset']
    output=public/asset['url'].removeprefix('/data/')
    assert digest(output)==asset['sha256'] and output.stat().st_size==asset['byte_length']
    assert asset['source_id']=='osm' and asset['layer']=='terrain' and asset['max_camera_height']==60000
    assert stage_dokdo(source,public=public,audit=tmp_path/'audit')['asset']==asset
    assert (public/'catalog.json').read_text(encoding='utf-8')=='preserved catalog'
    assert (public/'fixture.terrain').read_bytes()==b'preserved negative-elevation terrain'
    assert digest(source)==original_hash


def test_source_checksum_mismatch_stops_before_output(tmp_path):
    source=tmp_path/'fixture.osm';source.write_text('<osm version="0.6"/>',encoding='utf-8')
    source.with_suffix('.osm.meta.json').write_text(json.dumps({'sha256':'0'*64,'bytes':source.stat().st_size}),encoding='utf-8')
    with pytest.raises(ValueError,match='checksum'):stage_dokdo(source,public=tmp_path/'public',audit=tmp_path/'audit')
    assert not (tmp_path/'public').exists()
