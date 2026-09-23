import pytest

from pipeline.rail_lifecycle import rail_lifecycle
from pipeline.subway_depth import selected_relation, relation_graph


@pytest.mark.parametrize('tags,status', [
    ({'railway': 'subway'}, 'existing'),
    ({'railway': 'subway', 'construction': 'no', 'disused': 'no'}, 'existing'),
    ({'railway': 'construction', 'construction': 'subway'}, 'construction'),
    ({'railway': 'subway', 'construction': 'yes'}, 'construction'),
    ({'construction:railway': 'subway'}, 'construction'),
    ({'railway': 'proposed', 'proposed': 'rail'}, 'proposed'),
    ({'railway': 'subway', 'state': 'proposed'}, 'proposed'),
    ({'railway': 'subway', 'disused': 'yes'}, 'inactive'),
    ({'abandoned:railway': 'subway'}, 'inactive'),
    ({'railway': 'subway', 'state': 'suspended'}, 'unknown'),
])
def test_source_lifecycle_is_not_inferred_from_name_or_future_dates(tags, status):
    assert rail_lifecycle(tags) == status


@pytest.mark.parametrize('status', ['construction', 'proposed', 'disused', 'abandoned'])
def test_future_or_inactive_routes_cannot_be_selected_for_existing_depth_interpolation(status):
    tags = {'type': 'route', 'route': 'subway', 'ref': '5', 'name': '서울 지하철 5호선', status: 'yes'}
    assert not selected_relation(tags)


def test_mixed_route_relation_does_not_bridge_construction_track():
    relation = {'members': [['w', 10, ''], ['w', 20, ''], ['w', 30, '']]}
    extract = {'ways': {
        '10': {'nodes': [1, 2], 'tags': {'railway': 'subway'}},
        '20': {'nodes': [2, 3], 'tags': {'railway': 'subway', 'construction': 'yes'}},
        '30': {'nodes': [3, 4], 'tags': {'railway': 'subway', 'proposed': 'yes'}},
    }, 'nodes': {str(n): {'coord': [127+n*.001, 37]} for n in range(1, 5)}}
    graph, ordered = relation_graph(relation, extract)
    assert list(graph.edges) == [(1, 2)]
    assert ordered == ['10', '20', '30']
