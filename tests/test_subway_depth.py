import copy

import pytest

from pipeline.subway_depth import build, selected_relation, ordered_members_match, normalize_name


def fixture():
    relation = {'id': 100, 'tags': {'type': 'route', 'route': 'subway', 'ref': '5', 'name': '서울 지하철 5호선'},
                'members': [['n', 1, 'stop'], ['n', 2, 'stop'], ['n', 3, 'stop'], ['w', 10, ''], ['w', 20, '']]}
    extract = {'source_hash': 'abc', 'relations': [relation],
               'ways': {'10': {'nodes': [1, 2], 'tags': {'railway': 'subway'}},
                        '20': {'nodes': [2, 3], 'tags': {'railway': 'subway'}}},
               'nodes': {str(n): {'coord': [127+n*.01, 36], 'tags': {'name': name}}
                         for n, name in enumerate(('가역', '나역', '다역'), start=1)}}
    depths = [{'id': f'5:{n}', 'geometry': {'type': 'Point', 'coordinates': [127+n*.01, 36, 30]},
               'properties': {'name': name, 'evidence_type': 'official_record', 'surface_height': 50,
                              'rail_depth': 20+n, 'provenance': {'source_record_id': f'5:{n}'}}}
              for n, name in enumerate(('가역', '나역', '다역'), start=1)]
    return extract, depths


def test_real_relation_adjacency_and_depths_produce_only_supported_static_geometry():
    extract, depths = fixture()
    body, audit = build(extract, depths)
    assert audit['pair_attempts'] == audit['resolved_pair_attempts'] == 2
    assert audit['unique_station_pairs'] == 2
    assert audit['covered_depth_stations'] == 3
    f = body['features'][0]
    assert f['geometry']['coordinates'][0][2] == 29
    assert f['geometry']['coordinates'][-1][2] == pytest.approx(28)
    assert f['properties']['evidence_type'] == 'estimate'
    assert f['properties']['osm_relation_ids'] == [100]
    assert 'time' not in f['properties']


def test_missing_intermediate_depth_is_not_bridged_or_imputed():
    extract, depths = fixture()
    body, audit = build(extract, [depths[0], depths[2]])
    assert body['features'] == []
    assert audit['unresolved_reasons'] == {'no_official_depth_name_match': 2}
    assert audit['unresolved_pair_attempts'] == 2


def test_wrong_line_depth_and_geographic_mismatch_are_rejected():
    extract, depths = fixture()
    depths[1]['id'] = '3:2'
    assert build(extract, depths)[1]['unique_geometries'] == 0
    depths[1]['id'] = '5:2'
    depths[1]['geometry']['coordinates'][0] += 1
    assert build(extract, depths)[1]['unresolved_reasons'] == {'official_osm_station_position_mismatch': 2}


def test_disconnected_track_and_impossible_gradient_are_audited():
    extract, depths = fixture()
    del extract['ways']['10']
    body, audit = build(extract, depths)
    assert len(body['features']) == 1
    assert audit['unresolved_pair_attempts'] == 1
    extract, depths = fixture()
    depths[1]['properties']['surface_height'] = 250
    assert build(extract, depths)[1]['unresolved_reasons'] == {'implausible_interpolated_gradient': 2}


def test_relation_way_members_must_be_contiguous_in_path_order():
    assert ordered_members_match([{'10'}, {'10'}, {'20'}], ['10', '20'])
    assert ordered_members_match([{'20'}, {'10'}], ['10', '20'])
    assert not ordered_members_match([{'10'}, {'30'}], ['10', '20', '30'])
    assert not ordered_members_match([{'10'}, {'99'}], ['10', '20'])


def test_duplicate_relations_merge_provenance_without_double_geometry():
    extract, depths = fixture()
    duplicate = copy.deepcopy(extract['relations'][0])
    duplicate['id'] = 101
    extract['relations'].append(duplicate)
    body, audit = build(extract, depths)
    assert audit['resolved_pair_attempts'] == 4
    assert audit['unique_geometries'] == 2
    assert body['features'][0]['properties']['osm_relation_ids'] == [100, 101]


def test_only_target_region_and_explicit_name_normalization():
    assert selected_relation({'type': 'route', 'route': 'subway', 'ref': '3', 'name': '수도권 전철 3호선'})
    assert not selected_relation({'type': 'route', 'route': 'subway', 'ref': '3', 'name': '부산 도시철도 3호선'})
    assert not selected_relation({'type': 'route', 'route': 'subway', 'ref': '인천1', 'name': '수도권 인천1'})
    assert normalize_name('성신여대입구(돈암) · 4호선') == '성신여대입구'
    assert normalize_name('당고개') != normalize_name('불암산')
