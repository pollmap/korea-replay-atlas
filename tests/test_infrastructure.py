from shapely.geometry import LineString, Polygon, Point
from shapely.ops import unary_union
from pipeline.infrastructure import categories, cell_parts, overview_visible
import json
from pipeline import infrastructure
from pipeline.core import digest


def test_cross_cell_road_keeps_all_geometry_without_teleport():
    road = LineString([(127.1, 36.1), (127.8, 36.6)])
    parts = list(cell_parts(road, .25))
    assert len(parts) > 2
    combined = unary_union([part for _, _, part in parts])
    assert abs(combined.length-road.length) < 1e-12
    assert combined.hausdorff_distance(road) < 1e-12


def test_industrial_area_hole_preserved_when_partitioned():
    area = Polygon([(127,36),(127.5,36),(127.5,36.5),(127,36.5)], holes=[[(127.1,36.1),(127.2,36.1),(127.2,36.2),(127.1,36.2)]])
    combined = unary_union([part for _, _, part in cell_parts(area, .25)])
    assert combined.equals(area)
    assert not combined.contains(Point(127.15,36.15))


def test_boundary_point_has_one_owner():
    assert len(list(cell_parts(Point(127.5,36.5), .25))) == 1


def test_industrial_is_not_promoted_to_official_park():
    assert categories({'landuse':'industrial'}, 'area') == ['industrial_land']
    assert categories({'landuse':'industrial'}, 'point') == []
    assert categories({'highway':'construction','construction':'motorway'}, 'line') == []
    assert categories({'railway':'station','disused':'yes'}, 'point') == []


def test_overview_excludes_dense_paths_and_stops():
    assert overview_visible('road', {'highway':'motorway'})
    assert not overview_visible('road', {'highway':'footway'})
    assert not overview_visible('bus_stop', {})
    assert categories({'highway':'motorway','bridge':'yes','layer':'1'}, 'line') == ['road']
    assert categories({'highway':'bus_stop','public_transport':'platform'}, 'point') == ['bus_stop']


def test_real_osmium_handler_keeps_relation_identity_and_stages_without_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(infrastructure, 'LOCAL', tmp_path/'local')
    monkeypatch.setattr(infrastructure, 'PUBLIC', tmp_path/'public')
    source = tmp_path/'sample.osm'
    source.write_text('''<osm version="0.6">
      <node id="1" lat="36.1" lon="127.1"><tag k="place" v="town"/><tag k="name" v="검증읍"/></node>
      <node id="2" lat="36.1" lon="127.4"/>
      <node id="3" lat="36.4" lon="127.4"/>
      <node id="4" lat="36.4" lon="127.1"/>
      <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/></way>
      <way id="11"><nd ref="1"/><nd ref="2"/><tag k="highway" v="primary"/><tag k="bridge" v="yes"/></way>
      <relation id="20"><member type="way" ref="10" role="outer"/><tag k="type" v="multipolygon"/><tag k="landuse" v="industrial"/><tag k="name" v="검증산업용지"/></relation>
    </osm>''', encoding='utf-8')
    source_hash = digest(source)
    source.with_suffix('.osm.meta.json').write_text(json.dumps({'sha256':source_hash, 'retrieved_at':'2026-09-16T00:00:00Z', 'url':'https://example.com/source.osm'}), encoding='utf-8')
    report = infrastructure.extract_infrastructure(source)
    assert report['source_feature_counts'] == {'settlement':1, 'road':1, 'industrial_land':1}
    assert report['errors'] == {}
    assert not (tmp_path/'public'/'catalog.json').exists()
    assert digest(source) == source_hash
    features = []
    for asset in report['assets']:
        content = json.loads((tmp_path/'public'/asset['url'].removeprefix('/data/')).read_text(encoding='utf-8'))
        features.extend(content['features'])
    industrial = [item for item in features if item['properties']['kind']=='industrial_land']
    assert industrial
    assert all(item['properties']['source_record_id']=='relation/20' for item in industrial)
    assert all(item['properties']['height_m'] is None and item['properties']['depth_m'] is None for item in features)
    validation = infrastructure.validate_staged()
    assert validation['staged_geometry_validation'] == 'passed'
    assert validation['duplicate_representations_removed'] == 0
    assert validation['source_feature_counts_unique']['industrial_land'] == 1
    index = infrastructure.build_search_index()
    assert index['count'] == 2
    assert index['omitted_count'] == 0
    refinement = infrastructure.split_large_detail(max_bytes=1)
    assert refinement['coarse_detail_cells_refined'] > 0
    refined = infrastructure.validate_staged()
    assert refined['source_feature_counts_unique'] == validation['source_feature_counts_unique']
    assert infrastructure.build_search_index()['count'] == 2
    overview = infrastructure.partition_overview(max_bytes=1)
    assert overview['asset_count'] > 0
    assert infrastructure.validate_staged()['source_feature_counts_unique'] == validation['source_feature_counts_unique']
    limited = infrastructure.build_search_index(limit=1)
    assert limited['count'] == 1 and limited['omitted_count'] == 1
    assert limited['url'].endswith('search-index-1.json')
