import io
import struct
import zipfile

import numpy as np
import pytest
import shapely
from shapely.geometry import Polygon, box

from pipeline.admin_boundaries import (
    convert_features, encoded, immutable, read_dbf, read_shp,
    ring_displacement_bound, rings_to_geometry, simplify_coverage, validate_members,
)


def dbf_bytes(rows):
    fields=[('BASE_DATE',8),('SIDO_CD',2),('SIDO_NM',30)]
    header=bytearray(32);header[0]=3
    row_size=1+sum(w for _,w in fields);header_size=33+32*len(fields)
    struct.pack_into('<IHH',header,4,len(rows),header_size,row_size)
    output=bytes(header)
    for name,width in fields:
        f=bytearray(32);f[:len(name)]=name.encode();f[11]=ord('C');f[16]=width;output+=f
    output+=b'\r'
    for row in rows:
        output+=b' '+b''.join(row[name].encode().ljust(width,b' ') for name,width in fields)
    return output+b'\x1a'


def shp_bytes(rings):
    points=np.concatenate(rings);starts=np.cumsum([0]+[len(r) for r in rings[:-1]])
    lo=points.min(axis=0);hi=points.max(axis=0)
    record=struct.pack('<I4dII',5,lo[0],lo[1],hi[0],hi[1],len(rings),len(points))
    record+=struct.pack('<'+'i'*len(starts),*starts)+points.astype('<f8').tobytes()
    header=bytearray(100);struct.pack_into('>I',header,0,9994)
    struct.pack_into('>I',header,24,(108+len(record))//2);struct.pack_into('<II',header,28,1000,5)
    return bytes(header)+struct.pack('>II',1,len(record)//2)+record


def province_fixture():
    rows=[{'BASE_DATE':'20250630','SIDO_CD':str(11+i),'SIDO_NM':f'도시 {i}'} for i in range(17)]
    geoms=[box(950000+i*1000,1950000,950500+i*1000,1950500) for i in range(17)]
    return rows,geoms


def test_dbf_preserves_source_codes_and_korean_names():
    rows=[{'BASE_DATE':'20250630','SIDO_CD':'01','SIDO_NM':'테스트시'}]
    assert read_dbf(dbf_bytes(rows),'utf-8')==rows


def test_dbf_rejects_deleted_and_truncated_records():
    data=dbf_bytes([{'BASE_DATE':'20250630','SIDO_CD':'11','SIDO_NM':'서울'}])
    with pytest.raises(ValueError,match='Truncated'):
        read_dbf(data[:-10],'utf-8')
    modified=bytearray(data);modified[129]=ord('*')
    with pytest.raises(ValueError,match='Deleted'):
        read_dbf(modified,'utf-8')


def test_shp_preserves_islands_holes_and_coordinates():
    shell=np.array([[0,0],[0,10],[10,10],[10,0],[0,0]],dtype=float)
    hole=np.array([[2,2],[4,2],[4,4],[2,4],[2,2]],dtype=float)
    island=shell+20
    geometry=read_shp(io.BytesIO(shp_bytes([shell,hole,island])))[0]
    assert geometry.geom_type=='MultiPolygon'
    assert len(geometry.geoms)==2 and len(geometry.geoms[0].interiors)==1
    assert np.array_equal(np.asarray(geometry.geoms[0].exterior.coords),shell)
    assert np.array_equal(np.asarray(geometry.geoms[0].interiors[0].coords),hole)
    assert geometry.area==196


@pytest.mark.parametrize('ring',[
    [[0,0],[0,1],[1,1],[1,0]],
    [[0,0],[1,1],[0,1],[1,0],[0,0]],
    [[0,0],[0,1],[float('nan'),1],[0,0]],
])
def test_invalid_source_ring_is_never_silently_repaired(ring):
    with pytest.raises(ValueError):rings_to_geometry([np.array(ring)])


def test_shp_rejects_truncated_record():
    data=shp_bytes([np.array([[0,0],[0,1],[1,1],[1,0],[0,0]],dtype=float)])
    with pytest.raises(ValueError):read_shp(io.BytesIO(data[:-3]))


@pytest.mark.parametrize('path',['../outside.shp','/root.shp','C:/file.shp','folder\\escape.shp'])
def test_archive_paths_cannot_escape(path):
    buffer=io.BytesIO()
    # Windows ZipFile normalizes backslashes on write: construct that raw ZIP
    # name explicitly to exercise the reader's original-name validation.
    stored=path.replace(chr(92),'/')
    with zipfile.ZipFile(buffer,'w') as z:z.writestr(stored,b'x')
    buffer=io.BytesIO(buffer.getvalue().replace(stored.encode(),path.encode()))
    with zipfile.ZipFile(buffer) as z:
        with pytest.raises(ValueError,match='escape'):validate_members(z)


def test_ring_error_bound_measures_omitted_arc_in_metres():
    original=Polygon([(0,0),(0,10),(3,10.5),(6,10),(10,10),(10,0),(0,0)])
    simplified=Polygon([(0,0),(0,10),(10,10),(10,0),(0,0)])
    assert ring_displacement_bound(original,simplified)==pytest.approx(.5)
    reversed_ring=Polygon(list(simplified.exterior.coords)[::-1])
    assert ring_displacement_bound(original,reversed_ring)==pytest.approx(.5)


def test_ring_error_refuses_removed_hole_or_new_vertex():
    original=Polygon([(0,0),(0,10),(10,10),(10,0),(0,0)],holes=[[(2,2),(4,2),(4,4),(2,4),(2,2)]])
    with pytest.raises(ValueError,match='removed a ring'):ring_displacement_bound(original,box(0,0,10,10))
    with pytest.raises(ValueError,match='matched'):ring_displacement_bound(box(0,0,10,10),box(0,0,11,10))


def test_shared_boundary_simplification_remains_an_edge_matched_coverage():
    left=Polygon([(0,0),(0,10),(5,10),(5.1,7),(5,5),(5.1,3),(5,0),(0,0)])
    right=Polygon([(5,0),(5.1,3),(5,5),(5.1,7),(5,10),(10,10),(10,0),(5,0)])
    result,audit=simplify_coverage([left,right],1,5)
    assert shapely.coverage_is_valid(result)
    assert audit['fallback_reason'] is None
    assert audit['maximum_curve_displacement_bound_m']<=5
    assert audit['output_vertices']<audit['source_vertices']


def test_overlap_falls_back_to_exact_geometries():
    originals=[box(0,0,10,10),box(5,0,15,10)]
    result,audit=simplify_coverage(originals,10,100)
    assert [g.wkb for g in result]==[g.wkb for g in originals]
    assert audit['fallback_reason']=='source_is_not_an_edge_matched_coverage'


def test_error_budget_cannot_be_confused_with_algorithm_parameter():
    originals=[Polygon([(0,0),(0,10),(3,10.5),(6,10),(10,10),(10,0),(0,0)])]
    result,audit=simplify_coverage(originals,2,0)
    assert result[0].wkb==originals[0].wkb
    assert audit['fallback_reason']=='Simplification exceeded measured error budget'
    assert audit['attempted_curve_displacement_bound_m']>.4
    assert audit['maximum_curve_displacement_bound_m']==0


def test_conversion_preserves_source_contract_and_order_deterministically():
    rows,geoms=province_fixture()
    payload,audit=convert_features(rows,geoms,5179,'sido',0,0)
    again,_=convert_features(list(reversed(rows)),list(reversed(geoms)),5179,'sido',0,0)
    assert encoded(payload)==encoded(again)
    assert audit['feature_count']==17
    assert audit['roundtrip_sample_max_m']<.001
    assert audit['wgs84_coordinate_quantization'] is False
    for feature,row in zip(payload['features'],rows):
        assert all(feature['properties'][key]==value for key,value in row.items())
        assert feature['properties']['legal_dong_code'] is None
        assert feature['properties']['code_namespace']=='sgis'
        x,y=feature['geometry']['coordinates'][0][0]
        assert 123<x<133 and 32<y<40


def test_official_esri_prj_alias_is_recognized_without_guessing_crs():
    rows,geoms=province_fixture()
    wkt='PROJCS["Korea_2000_Korea_Unified_Coordinate_System",GEOGCS["GCS_Korea_2000",DATUM["D_Korea_2000",SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",1000000.0],PARAMETER["False_Northing",2000000.0],PARAMETER["Central_Meridian",127.5],PARAMETER["Scale_Factor",0.9996],PARAMETER["Latitude_Of_Origin",38.0],UNIT["Meter",1.0]]'
    payload,audit=convert_features(rows,geoms,wkt,'sido',0,0)
    assert len(payload['features'])==17
    assert audit['source_crs']=='EPSG:5179'


@pytest.mark.parametrize('failure',['crs','date','duplicate','count'])
def test_contract_rejects_wrong_crs_date_codes_and_missing_province(failure):
    rows,geoms=province_fixture();crs=5179
    if failure=='crs':crs=4326
    if failure=='date':rows[0]['BASE_DATE']='20250629'
    if failure=='duplicate':rows[1]['SIDO_CD']=rows[0]['SIDO_CD']
    if failure=='count':rows.pop();geoms.pop()
    with pytest.raises(ValueError):convert_features(rows,geoms,crs,'sido',0,0)


def test_immutable_output_never_replaces_different_existing_data(tmp_path):
    p=tmp_path/'data.json';immutable(p,{'a':1})
    assert immutable(p,{'a':1})['byte_length']==len(p.read_bytes())
    with pytest.raises(ValueError,match='differs'):immutable(p,{'a':2})
    assert p.read_bytes()==b'{"a":1}'
