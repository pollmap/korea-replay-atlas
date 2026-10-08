import copy
import json
from pipeline import geometry_compaction as retile

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


def test_compact_does_not_confuse_boolean_integer_or_missing_null():
    features = [building('a'), building('b')]
    features[0]['properties']['typed'] = True
    features[1]['properties']['typed'] = 1
    features[0]['properties']['nullable'] = None
    restored = retile.restore_features(retile.compact_features(features))
    assert type(restored[0]['properties']['typed']) is bool
    assert type(restored[1]['properties']['typed']) is int
    assert 'nullable' not in restored[1]['properties']


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
