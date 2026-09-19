from datetime import datetime, timezone, timedelta

import pytest

from pipeline.rail_routes import make_graph, route_between, validate_events, interpolate_points, interpolate_station_depths


def event(sequence, **changes):
    return {'run_ymd': '20260909', 'trn_no': '01001', 'mrnt_cd': '01',
            'uppln_dn_se_cd': 'D', 'trn_run_sn': str(sequence),
            'trn_dptre_dt': '2026-09-09 07:26:00.0',
            'trn_arvl_dt': '2026-09-09 07:49:00.0', **changes}


def test_consecutive_official_events_keep_korean_timezone():
    start, end = validate_events(event(6), event(7))
    assert start.isoformat() == '2026-09-08T22:26:00+00:00'
    assert (end-start).total_seconds() == 23*60
    with pytest.raises(ValueError, match='Intermediate'):
        validate_events(event(6), event(8))
    with pytest.raises(ValueError, match='trn_no'):
        validate_events(event(6), event(7, trn_no='01002'))
    with pytest.raises(ValueError, match='interval'):
        validate_events(event(6), event(7, trn_arvl_dt='2026-09-09 07:25:00.0'))


def test_no_straight_line_fallback_between_disconnected_tracks():
    graph = make_graph({'edges': [
        [1, 2, 127, 36, 127.001, 36, 10],
        [3, 4, 127.02, 36, 127.021, 36, 11],
    ]})
    with pytest.raises(ValueError, match='Disconnected'):
        route_between(graph, (127, 36), (127.021, 36), max_snap=30)


def test_graph_excludes_subway_and_rejects_conflicting_nodes():
    payload = {'edges': [[1, 2, 127, 36, 127.001, 36, 10],
                         [2, 3, 127.001, 36, 127.002, 36, 11]]}
    graph = make_graph(payload, {'10': {'railway': 'rail'}, '11': {'railway': 'subway'}})
    assert set(graph.nodes) == {1, 2}
    payload['edges'][1][2] = 127.005
    with pytest.raises(ValueError, match='conflict'):
        make_graph(payload)


def test_curved_route_preserves_vertices_and_endpoint_times_without_height():
    graph = make_graph({'edges': [
        [1, 2, 127, 36, 127.005, 36.002, 10],
        [2, 3, 127.005, 36.002, 127.01, 36, 10],
    ]})
    route = route_between(graph, (127, 36), (127.01, 36), max_snap=20)
    start = datetime(2026, 9, 9, tzinfo=timezone.utc)
    points = interpolate_points(route, start, start+timedelta(minutes=2))
    assert len(points) == 3
    assert points[1]['lat'] == 36.002
    assert datetime.fromisoformat(points[0]['time']) == start
    assert datetime.fromisoformat(points[-1]['time']) == start+timedelta(minutes=2)
    assert points[0]['time'] < points[1]['time'] < points[-1]['time']
    assert all('height' not in point for point in points)
    with pytest.raises(ValueError, match='speed'):
        interpolate_points(route, start, start+timedelta(seconds=1))


def test_far_station_is_rejected_instead_of_silent_relocation():
    graph = make_graph({'edges': [[1, 2, 127, 36, 127.001, 36, 10]]})
    with pytest.raises(ValueError, match='tolerance'):
        route_between(graph, (127.1, 36), (127.001, 36))


def test_depth_interpolation_requires_two_official_same_line_endpoints():
    graph = make_graph({'edges': [[1, 2, 127, 36, 127.01, 36, 10]]})
    route = route_between(graph, (127, 36), (127.01, 36), max_snap=20)
    def endpoint(identifier, depth):
        return {'id': identifier, 'properties': {'name': identifier, 'evidence_type': 'official_record',
                                                'surface_height': 50, 'rail_depth': depth, 'provenance': {}}}
    a, b = endpoint('5:1', 20), endpoint('5:2', 30)
    result = interpolate_station_depths(route, a, b)
    assert result['geometry']['coordinates'][0][2] == 30
    assert result['geometry']['coordinates'][-1][2] == pytest.approx(20)
    assert result['properties']['evidence_type'] == 'estimate'
    with pytest.raises(ValueError, match='same line'):
        interpolate_station_depths(route, a, endpoint('3:2', 30))
    del b['properties']['rail_depth']
    with pytest.raises(ValueError, match='Missing'):
        interpolate_station_depths(route, a, b)


def test_snapping_keeps_closest_connected_anchor_not_shortest_trimmed_route():
    graph = make_graph({'edges': [[1, 2, 127, 36, 127.001, 36, 10],
                                 [2, 3, 127.001, 36, 127.01, 36, 10]]})
    route = route_between(graph, (127, 36), (127.01, 36))
    assert route['node_ids'] == [1, 2, 3]
    assert route['origin_snap_m'] == 0
