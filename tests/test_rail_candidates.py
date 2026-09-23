import copy

import pytest
import osmium

from pipeline.rail_candidates import build_candidates, candidate_kind, extract


@pytest.mark.parametrize('tags,expected', [
    ({'railway': 'construction', 'construction': 'subway'}, ('construction', 'subway')),
    ({'railway': 'proposed', 'proposed': 'rail'}, ('proposed', 'rail')),
    ({'construction:railway': 'light_rail'}, ('construction', 'light_rail')),
    ({'railway': 'tram', 'construction': 'yes'}, ('construction', 'tram')),
    ({'railway': 'construction', 'construction': 'station'}, None),
    ({'railway': 'proposed'}, None),
    ({'railway': 'subway', 'name': '미래 계획선'}, None),
    ({'railway': 'construction', 'construction': 'subway', 'abandoned': 'yes'}, None),
])
def test_only_explicit_future_rail_types_are_candidates(tags, expected):
    assert candidate_kind(tags) == expected


def fixture():
    return ([{'id': 10, 'nodes': [1, 2, 3], 'tags': {'railway': 'construction', 'construction': 'subway', 'name': '후보선', 'opening_date': '2030'}}],
            {'1': [127, 37], '2': [127.001, 37.002], '3': [127.005, 37.001]})


def test_candidate_preserves_original_vertices_ids_tags_without_claiming_status_or_opening():
    ways, nodes = fixture()
    original = copy.deepcopy((ways, nodes))
    body, audit = build_candidates(ways, nodes, 'abc')
    feature = body['features'][0]
    assert feature['id'] == 'way/10'
    assert feature['geometry']['coordinates'] == list(nodes.values())
    properties = feature['properties']
    assert properties['source_node_ids'] == [1, 2, 3]
    assert properties['source_tags'] == ways[0]['tags']
    assert properties['official_status_verified'] is False
    assert properties['eligible_for_operating_routes'] is False
    assert properties['opening_date'] is None
    assert properties['publication_state'] == 'review_only'
    assert audit['by_status'] == {'construction': 1}
    assert audit['retained_vertices'] == 3
    assert (ways, nodes) == original


@pytest.mark.parametrize('invalid', [None, [127, float('nan')], [181, 37], [127, 37, -30]])
def test_missing_or_invalid_node_quarantines_the_whole_way_without_straight_gap_bridge(invalid):
    ways, nodes = fixture()
    nodes['2'] = invalid
    body, audit = build_candidates(ways, nodes, 'abc')
    assert body['features'] == []
    assert audit['unresolved'] == [{'source_record_id': 'way/10', 'reason': 'missing_or_invalid_original_nodes'}]


def test_two_pass_pbf_extraction_keeps_only_candidate_nodes_and_original_shape(tmp_path):
    path = tmp_path/'candidate.osm.pbf'
    with osmium.SimpleWriter(str(path)) as writer:
        for node, coord in [(1, (127, 37)), (2, (127.001, 37.002)), (3, (127.005, 37.001)), (99, (126, 36))]:
            writer.add_node(osmium.osm.mutable.Node(id=node, location=coord))
        writer.add_way(osmium.osm.mutable.Way(id=10, nodes=[1, 2, 3], tags={'railway': 'construction', 'construction': 'subway'}))
        writer.add_way(osmium.osm.mutable.Way(id=20, nodes=[3, 99], tags={'railway': 'rail'}))
    body, audit = extract(path)
    assert len(body['features']) == 1
    assert body['features'][0]['properties']['source_node_ids'] == [1, 2, 3]
    assert body['features'][0]['geometry']['coordinates'] == [[127, 37], [127.001, 37.002], [127.005, 37.001]]
    assert audit['retained_vertices'] == 3
    assert audit['unresolved'] == []
