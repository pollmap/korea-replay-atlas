import copy
import json

import numpy as np

from pipeline import retile
from pipeline.mesh import write_glb
from pipeline.mesh_metadata_audit import audit_glb, parse_glb


def building(identity='a', x=127, height=10, minimum=0):
    return {'type': 'Feature', 'id': identity, 'geometry': {'type': 'Polygon', 'coordinates': [
        [[x, 36], [x+.001, 36], [x+.001, 36.001], [x, 36.001], [x, 36]]]},
        'properties': {'height': height, 'min_height': minimum, 'base_height': 10, 'height_method': 'source',
                       'source_id': 'overture', 'dataset_version': '2026-08-19.0', 'name': '한글 건물',
                       'provenance': {'source_record_id': identity}, 'nested': {'preserve': [1, 2, None]}}}


def test_compact_preserves_properties_geometry_and_feature_extras():
    features = [building(), building('b', 128)]
    features[1]['properties']['name'] = ''
    features[1]['properties']['different'] = None
    features[0]['bbox'] = [127, 36, 128, 37]
    before = copy.deepcopy(features)
    compact = retile.compact_features(features)
    assert 'height' in compact['metadata']['shared']
    assert retile.restore_features(json.loads(retile.encoded(compact))) == before
    assert features == before


def test_mesh_hole_multipart_feature_identity_and_normals(tmp_path):
    feature = building()
    feature['geometry']['coordinates'].append([[127.0002, 36.0002], [127.0002, 36.0004], [127.0004, 36.0004], [127.0004, 36.0002], [127.0002, 36.0002]])
    second = building('b', 127.002)
    second['geometry'] = {'type': 'MultiPolygon', 'coordinates': [second['geometry']['coordinates'], building('c', 127.004)['geometry']['coordinates']]}
    path = tmp_path/'test.glb'
    tile, count = write_glb([feature, second], path)
    audit = audit_glb(path)
    assert count == audit['features'] == 2
    assert retile.glb_strings(path, 'source_record_id') == ['a', 'b']
    assert tile['extras']['coordinate_rounding_max_m'] < .001
    doc, blob = parse_glb(path.read_bytes())
    view = doc['bufferViews'][doc['accessors'][1]['bufferView']]
    normals = np.frombuffer(blob[view['byteOffset']:view['byteOffset']+view['byteLength']], dtype='<f4').reshape(-1, 3)
    assert np.allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-6)


def test_actual_byte_cap_splits_without_losing_identity(tmp_path):
    features = [building(str(i), 127+i*.001) for i in range(10)]
    for f in features:
        f['properties']['source_record_id'] = f['id']
    tiles = retile.emit_glbs(features, tmp_path/'public', tmp_path/'scratch', 'part', maximum=10000)
    assert len(tiles) > 1
    identities = [v for tile in tiles for v in retile.glb_strings(tmp_path/'public'/tile['content']['uri'], 'source_record_id')]
    assert sorted(identities) == sorted(str(i) for i in range(10))
    assert all(t['extras']['bytes'] <= 10000 or t['extras']['feature_count'] == 1 for t in tiles)


def test_compact_size_split_is_spatial_and_preserves_every_source_row(tmp_path, monkeypatch):
    public = tmp_path/'public'
    monkeypatch.setattr(retile, 'PUBLIC', public)
    # Interleaved edit order used to make both files intersect both neighborhoods.
    features = [building('west-a', 126), building('east-a', 128),
                building('west-b', 126.001), building('east-b', 128.001)]
    before = copy.deepcopy(features)
    maximum = max(len(retile.encoded(retile.compact_features(features[::2]))),
                  len(retile.encoded(retile.compact_features(features[1::2]))))
    assets = retile.emit_compact(features, public/'geometry', 'roads', {}, maximum=maximum)
    assert len(assets) == 2
    restored = [feature for asset in assets for feature in retile.restore_features(
        json.loads((public/asset['url'].removeprefix('/data/')).read_bytes()))]
    assert sorted(restored, key=lambda f: f['id']) == sorted(before, key=lambda f: f['id'])
    assert features == before
    assert assets[0]['bbox'][2] < assets[1]['bbox'][0]
    assert sum(a['vertex_count'] for a in assets) == sum(retile.vertex_count(f['geometry']) for f in before)
    assert all(a['byte_length'] <= maximum for a in assets)


def test_compact_spatial_split_uses_north_south_axis_and_keeps_crossing_lines(tmp_path, monkeypatch):
    public = tmp_path/'public'
    monkeypatch.setattr(retile, 'PUBLIC', public)
    features = []
    for i, latitude in enumerate((35, 38, 35.01, 38.01)):
        features.append({'type': 'Feature', 'id': str(i), 'properties': {'kind': 'road'},
                         'geometry': {'type': 'LineString', 'coordinates': [[127, latitude], [127.01, latitude+.01]]}})
    maximum = max(len(retile.encoded(retile.compact_features(features[::2]))),
                  len(retile.encoded(retile.compact_features(features[1::2]))))
    assets = retile.emit_compact(features, public/'geometry', 'north-south', {}, maximum=maximum)
    assert len(assets) == 2 and assets[0]['bbox'][3] < assets[1]['bbox'][1]
    restored = [feature for asset in assets for feature in retile.restore_features(
        json.loads((public/asset['url'].removeprefix('/data/')).read_bytes()))]
    assert sorted(restored, key=lambda f: f['id']) == sorted(features, key=lambda f: f['id'])
    assert retile.emit_compact(features, public/'geometry', 'north-south', {}, maximum=maximum) == assets


def test_source_quality_partition_is_complete_and_resumeable(tmp_path, monkeypatch):
    public = tmp_path/'public'
    monkeypatch.setattr(retile, 'PUBLIC', public)
    source = tmp_path/'source.json'
    features = [building('good'), building('unknown', height=None), building('floating', minimum=3)]
    source.write_bytes(retile.encoded({'type': 'FeatureCollection', 'features': features}))
    asset = {'id': 'normalized-buildings-test', 'path': str(source), 'sha256': retile.digest(source), 'count': 3}
    job = (asset, str(public/'new'), str(tmp_path/'work'))
    result = retile.process_building(job)
    assert (result['detail_count'], result['footprint_count']) == (1, 2)
    assert retile.process_building(job) == result
    footprint = json.loads((public/result['footprints'][0]['url'].removeprefix('/data/')).read_text(encoding='utf-8'))
    restored = retile.restore_features(footprint)
    assert [f['id'] for f in restored] == ['unknown', 'floating']
    assert restored[1]['geometry'] == features[2]['geometry']
    path = public/'new'/'detail'/result['tiles'][0]['content']['uri']
    assert json.loads(retile.glb_strings(path, 'original_properties')[0]) == features[0]['properties']


def test_relative_transform_preserves_ecef_world_position():
    from pipeline.mesh import frame
    parent = np.asarray(frame(127, 36)[2]).reshape(4, 4, order='F')
    child = np.asarray(frame(127.2, 36.1)[2]).reshape(4, 4, order='F')
    relative = np.linalg.inv(parent) @ child
    point = np.array([24, -71, 41, 1])
    assert np.allclose(parent @ relative @ point, child @ point, atol=1e-7)


def test_compact_does_not_confuse_boolean_integer_or_missing_null():
    features = [building('a'), building('b')]
    features[0]['properties']['typed'] = True
    features[1]['properties']['typed'] = 1
    features[0]['properties']['nullable'] = None
    restored = retile.restore_features(retile.compact_features(features))
    assert type(restored[0]['properties']['typed']) is bool
    assert type(restored[1]['properties']['typed']) is int
    assert 'nullable' not in restored[1]['properties']


def test_hierarchy_has_actual_intermediate_lod_and_separate_detail_count(tmp_path, monkeypatch):
    from pipeline.mesh_metadata_audit import audit_catalog
    public = tmp_path/'public'
    monkeypatch.setattr(retile, 'PUBLIC', public)
    features = [building('good-a'), building('good-b', 127.13), building('unknown', height=None)]
    path = tmp_path/'source.json'
    path.write_bytes(retile.encoded({'type': 'FeatureCollection', 'features': features}))
    source = {'id': 'normalized-buildings-fixture', 'path': str(path), 'sha256': retile.digest(path), 'count': len(features)}
    output, work = public/'new', tmp_path/'work'
    checkpoint = retile.process_building((source, output, work))
    assets, audit = retile.build_hierarchy([checkpoint], output, work, compact=False)
    catalog = {'schema_version': 1, 'release_id': 'fixture', 'assets': assets}
    (public/'catalog.json').write_bytes(retile.encoded(catalog))
    report = audit_catalog(public)
    assert report['passed']
    assert report['features'] == report['source_identity_rows'] == audit['detail_count'] == 2
    assert report['lod_features'] == audit['lod_representative_count'] > 0
    assert audit['footprint_count'] == 1
    root = json.loads((output/'tileset.json').read_bytes())['root']
    assert root['refine'] == 'REPLACE' and root['children']
    assert root['content']['uri'].startswith('lod/')
    assert audit['hierarchy']['transform_proof']['passed']


def test_rail_overview_keeps_source_id_and_explicit_metric_generalization(tmp_path, monkeypatch):
    from pyproj import Transformer
    from shapely.geometry import shape
    from shapely.ops import transform
    public = tmp_path/'public'; public.mkdir()
    monkeypatch.setattr(retile, 'PUBLIC', public)
    feature = {'type': 'Feature', 'id': 'rail-1', 'geometry': {'type': 'LineString', 'coordinates': [
        [127, 36], [127.001, 36.0001], [127.002, 36], [127.003, 36.0001], [127.004, 36]]},
        'properties': {'name': '원본 철도', 'source_record_id': 'rail-1', 'retain': {'value': 3}}}
    source = public/'rail.geojson'
    source.write_bytes(retile.encoded({'type': 'FeatureCollection', 'features': [feature]}))
    before = source.read_bytes()
    catalog = {'assets': [{'id': 'osm-rail-korea', 'source_id': 'osm', 'version': 'test', 'count': 1,
                           'url': '/data/rail.geojson', 'sha256': retile.digest(source)}]}
    assets = retile.build_rail_overview(catalog, public/'new')
    output = json.loads((public/assets[0]['url'].removeprefix('/data/')).read_bytes())
    restored = retile.restore_features(output)[0]
    assert restored['id'] == feature['id']
    assert restored['properties']['retain'] == feature['properties']['retain']
    assert restored['properties']['simplification_tolerance_m'] == 100
    assert assets[0]['detail_level'] == 'overview' and assets[0]['min_camera_height'] == 200000
    metric = Transformer.from_crs(4326, 5179, always_xy=True).transform
    assert transform(metric, shape(feature['geometry'])).hausdorff_distance(transform(metric, shape(restored['geometry']))) < 100.001
    assert assets[0]['vertex_count'] < retile.vertex_count(feature['geometry'])
    assert source.read_bytes() == before


def test_resumed_descriptor_has_explicit_version_even_with_existing_vertex_budget():
    asset = {'id': 'old-footprint', 'format': 'geojson', 'dataset_version': '2026-08-19.0', 'vertex_count': 10}
    retile.enrich_geometry_budgets([asset])
    assert asset['version'] == '2026-08-19.0'
