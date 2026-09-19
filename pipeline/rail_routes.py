"""OSM-constrained calculated positions between consecutive official rail events.

No GPS, platform identity, track allocation or tunnel elevation is inferred.
Network calls are deliberately absent: feed bounded, archived official snapshots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import networkx as nx
from pyproj import Geod

from .core import LOCAL, PUBLIC, atomic_json, digest, publish_file
from .rail import parse_korail_time

GEOD = Geod(ellps='WGS84')
TRANSFORM = 'rail-distance-time-v1'


def distance(a, b):
    return float(GEOD.inv(a[0], a[1], b[0], b[1])[2])


def make_graph(payload, way_properties=None, railway='rail'):
    """Preserve real OSM node connectivity; never bridge disconnected tracks."""
    graph = nx.Graph()
    for a, b, lon1, lat1, lon2, lat2, way in payload['edges']:
        props = (way_properties or {}).get(str(way), {})
        if way_properties is not None and props.get('railway') != railway:
            continue
        if not all(math.isfinite(v) for v in (lon1, lat1, lon2, lat2)):
            raise ValueError('Non-finite graph coordinate')
        if not all(-180 <= v <= 180 for v in (lon1, lon2)) or not all(-90 <= v <= 90 for v in (lat1, lat2)):
            raise ValueError('Graph coordinate outside WGS84')
        length = distance((lon1, lat1), (lon2, lat2))
        if a == b or length <= 0:
            continue
        for identifier, coord in ((a, (lon1, lat1)), (b, (lon2, lat2))):
            if identifier in graph and graph.nodes[identifier]['coord'] != coord:
                raise ValueError('OSM node coordinate conflict')
            graph.add_node(identifier, coord=coord)
        # Collocated duplicate ways are not separate evidence for a route.
        if graph.has_edge(a, b) and graph[a][b]['length'] <= length:
            continue
        graph.add_edge(a, b, length=length, way_id=str(way),
                       tunnel=props.get('tunnel'), bridge=props.get('bridge'))
    return graph


def station_feature(features, name):
    matches = [f for f in features if f['properties'].get('name') == name
               and f['properties'].get('station') == 'train'
               and f['properties'].get('operator') in ('한국철도공사', 'Korail', 'KORAIL')]
    if len(matches) != 1:
        raise ValueError(f'Expected one named Korail station, found {len(matches)}: {name}')
    return matches[0]


def snap_candidates(graph, coord, radius=300, limit=8):
    candidates = sorted((distance(coord, data['coord']), node)
                        for node, data in graph.nodes(data=True)
                        if abs(data['coord'][0]-coord[0]) < .005
                        and abs(data['coord'][1]-coord[1]) < .005)
    return [(node, offset) for offset, node in candidates[:limit] if offset <= radius]


def route_between(graph, origin, destination, max_snap=300):
    """Find a plausible rail geometry, explicitly not the actual allocated track."""
    starts = snap_candidates(graph, origin, max_snap)
    ends = snap_candidates(graph, destination, max_snap)
    if not starts or not ends:
        raise ValueError('No rail node within station snap tolerance')
    possibilities = []
    for start, start_offset in starts:
        lengths, paths = nx.single_source_dijkstra(graph, start, weight='length', cutoff=1_000_000)
        for end, end_offset in ends:
            if end in lengths and end != start:
                possibilities.append((lengths[end]+start_offset+end_offset,
                                      lengths[end], start_offset, end_offset, paths[end]))
    if not possibilities:
        raise ValueError('Disconnected OSM railway; refusing straight-line fallback')
    # First preserve the closest connected station anchors. Minimizing path length
    # plus snap offsets can arbitrarily trim hundreds of metres at both ends.
    _, length, start_offset, end_offset, nodes = min(possibilities, key=lambda p: (p[2]+p[3], p[1], p[4]))
    direct = distance(origin, destination)
    if direct < 100 or length/direct > 3.5:
        raise ValueError('Route detour or station separation exceeds validation policy')
    coordinates = [graph.nodes[n]['coord'] for n in nodes]
    segments = [graph[a][b] for a, b in zip(nodes, nodes[1:])]
    return {'coordinates': coordinates, 'length_m': length, 'node_ids': nodes,
            'way_ids': sorted({s['way_id'] for s in segments}),
            'origin_snap_m': start_offset, 'destination_snap_m': end_offset,
            'candidate_paths': len(possibilities), 'route_evidence': 'calculation',
            'vertical_evidence': 'unverified',
            'contains_tunnel': any(s.get('tunnel') not in (None, '', 'no') for s in segments),
            'contains_bridge': any(s.get('bridge') not in (None, '', 'no') for s in segments)}


def validate_events(first, last):
    for field in ('run_ymd', 'trn_no', 'mrnt_cd', 'uppln_dn_se_cd'):
        if not first.get(field) or first[field] != last.get(field):
            raise ValueError(f'Incompatible official event field: {field}')
    if int(last['trn_run_sn']) != int(first['trn_run_sn'])+1:
        raise ValueError('Intermediate station events missing; consecutive sequence required')
    start = parse_korail_time(first.get('trn_dptre_dt'))
    end = parse_korail_time(last.get('trn_arvl_dt'))
    if not start or not end:
        raise ValueError('Missing official departure or arrival')
    start_dt, end_dt = datetime.fromisoformat(start), datetime.fromisoformat(end)
    duration = (end_dt-start_dt).total_seconds()
    if not 0 < duration <= 7200:
        raise ValueError('Invalid or excessive event interval')
    return start_dt, end_dt


def interpolate_points(route, start, end):
    """Retain every geometry vertex and distribute time by geodesic distance."""
    duration = (end-start).total_seconds()
    length = route['length_m']
    if duration <= 0 or length <= 0 or length/duration*3.6 > 350:
        raise ValueError('Invalid length/duration or implausible average train speed')
    points = []
    accumulated = 0.0
    coords = route['coordinates']
    for i, (lon, lat) in enumerate(coords):
        if i:
            accumulated += distance(coords[i-1], coords[i])
        time = end if i == len(coords)-1 else start+timedelta(seconds=duration*accumulated/length)
        points.append({'time': time.isoformat(timespec='microseconds').replace('+00:00', 'Z'),
                       'lon': lon, 'lat': lat})
    return points


def build_replay(first, last, graph, stations, source):
    start, end = validate_events(first, last)
    origin = station_feature(stations, first['stn_nm'])
    destination = station_feature(stations, last['stn_nm'])
    route = route_between(graph, origin['geometry']['coordinates'], destination['geometry']['coordinates'])
    points = interpolate_points(route, start, end)
    identifier = f'{first["run_ymd"]}:{first["trn_no"]}:{first["trn_run_sn"]}-{last["trn_run_sn"]}'
    track = {'id': identifier, 'label': f'{first["trn_no"]} · {first["stn_nm"]} → {last["stn_nm"]}',
             'layer': 'rail', 'position_evidence': 'calculation',
             'max_gap_seconds': (end-start).total_seconds(),
             'description': '공식 출발·도착 시각 사이를 OSM 선로의 최단 연결 경로와 거리 비례 시간으로 계산했습니다. 실제 GPS·배정 선로·정확한 속도·지하 높이가 아닙니다. 지상 투영입니다.',
             'provenance': {'source_id': 'korail', 'source_record_id': identifier,
                            'dataset_version': first['run_ymd'], 'observed_at': None,
                            'retrieved_at': source['retrieved_at'], 'evidence_type': 'official_record',
                            'input_hash': source['event_hash'], 'transform_version': TRANSFORM},
             'coordinate_provenance': {'source_id': 'osm', 'input_hash': source['graph_hash'],
                                       'station_hash': source['station_hash'],
                                       'station_ids': [origin['id'], destination['id']],
                                       'route_method': 'undirected shortest connected railway path',
                                       'way_ids': route['way_ids'], 'evidence_type': 'calculation',
                                       'vertical_evidence': 'unverified', 'display_mode': 'surface_projection'},
             'timing_evidence': {'departure': first['trn_dptre_dt'], 'arrival': last['trn_arvl_dt'],
                                 'timezone': 'Asia/Seoul', 'evidence_type': 'official_record'},
             'points': points}
    audit = {k: v for k, v in route.items() if k not in ('coordinates', 'node_ids', 'way_ids')}
    audit.update({'train': first['trn_no'], 'day': first['run_ymd'], 'points': len(points),
                  'departure': points[0]['time'], 'arrival': points[-1]['time'],
                  'average_speed_kmh': route['length_m']/(end-start).total_seconds()*3.6,
                  'scope': 'one verified consecutive station interval, not the complete train journey',
                  'geometry_snapshot_may_differ_from_event_date': True,
                  'exact_track_allocation_verified': False, 'source': source})
    return {'schema_version': 1, 'tracks': [track]}, audit


def interpolate_station_depths(route, origin, destination):
    """Interpolate endpoint ellipsoidal rail heights, not a surveyed tunnel model."""
    if origin['id'].split(':')[0] != destination['id'].split(':')[0]:
        raise ValueError('Station depth records must belong to the same line')
    heights = []
    for feature in (origin, destination):
        props = feature['properties']
        if props.get('evidence_type') != 'official_record':
            raise ValueError('Official endpoint depth is required')
        try:
            surface, depth = float(props['surface_height']), float(props['rail_depth'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('Missing station ground or rail depth') from None
        if not all(map(math.isfinite, (surface, depth))) or not -50 < depth < 150:
            raise ValueError('Invalid station depth')
        heights.append(surface-depth)
    coords = route['coordinates']
    cumulative = 0.0
    output = []
    for i, (lon, lat) in enumerate(coords):
        if i:
            cumulative += distance(coords[i-1], coords[i])
        fraction = min(1.0, cumulative/route['length_m'])
        output.append([lon, lat, heights[0]+fraction*(heights[1]-heights[0])])
    return {'type': 'Feature', 'id': f'depth-route:{origin["id"]}-{destination["id"]}',
            'geometry': {'type': 'LineString', 'coordinates': output},
            'properties': {'name': f'{origin["properties"]["name"]} → {destination["properties"]["name"]}',
                           'source_id': 'seoul-depth', 'evidence_type': 'estimate',
                           'horizontal_evidence': 'calculation', 'vertical_evidence': 'estimate',
                           'vertical_datum': 'WGS84 ellipsoid from EGM96-corrected terrain minus official rail depth',
                           'description': 'OSM 연결 선형에 공식 양끝 역 선로 심도로 계산한 높이를 거리 비례로 보간했습니다. 역간 터널의 실측 깊이·경사·단면은 확인되지 않았습니다.',
                           'endpoint_records': [origin['properties']['provenance'], destination['properties']['provenance']],
                           'osm_way_ids': route['way_ids'], 'source_station_ids': [origin['id'], destination['id']]}}


def depth_sample():
    """Bounded, reproducible Line 5 geometry check; kept private for UI review."""
    payload = json.loads((LOCAL/'silver'/'rail-graph.json').read_text(encoding='utf-8'))
    prefix = payload['source_hash'][:12]
    features = json.loads((PUBLIC/'osm'/f'rail-{prefix}.geojson').read_text(encoding='utf-8'))['features']
    # Prevent snapping a Line 5 depth record onto the nearby Line 1/3 track.
    # Missing/unlabelled links are rejected, not connected geometrically.
    properties = {f['id'].split('/')[-1]: f['properties'] for f in features
                  if f['properties']['name'] in ('5호선', '서울 지하철 5호선')}
    graph = make_graph(payload, properties, railway='subway')
    catalog = json.loads((PUBLIC/'catalog.json').read_text(encoding='utf-8'))
    asset = next(a for a in catalog['assets'] if a['id'] == 'depth-seoul')
    depth_path = PUBLIC/asset['url'].removeprefix('/data/')
    depths = json.loads(depth_path.read_text(encoding='utf-8'))['features']
    origin = next(f for f in depths if f['id'] == '5:2534')
    destination = next(f for f in depths if f['id'] == '5:2535')
    route = route_between(graph, origin['geometry']['coordinates'], destination['geometry']['coordinates'])
    feature = interpolate_station_depths(route, origin, destination)
    feature['properties'].update({'osm_extract_hash': payload['source_hash'],
                                  'depth_file_sha256': digest(depth_path),
                                  'line_filter': 'OSM way name is 5호선 or 서울 지하철 5호선'})
    target = LOCAL/'review'/'rail-routes'/'seoul-line5-gwanghwamun-jongno3ga-depth.geojson'
    atomic_json(target, {'type': 'FeatureCollection', 'features': [feature]})
    audit = {k: v for k, v in route.items() if k not in ('coordinates', 'node_ids')}
    audit.update({'output': str(target), 'osm_extract_hash': payload['source_hash'],
                  'depth_file_sha256': digest(depth_path), 'points': len(route['coordinates']),
                  'endpoint_ellipsoidal_rail_heights': [feature['geometry']['coordinates'][i][2] for i in (0, -1)],
                  'scope': 'one subway interval, endpoint depth interpolation only'})
    atomic_json(LOCAL/'audit'/'subway-depth-line5-sample.json', audit)
    print(json.dumps(audit, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--day', default='20260909')
    parser.add_argument('--train', default='01001')
    parser.add_argument('--origin', default='조치원')
    parser.add_argument('--destination', default='대전')
    parser.add_argument('--publish', action='store_true', help='Publish validated calculated route to local catalogue')
    parser.add_argument('--depth-sample', action='store_true', help='Write the bounded Line 5 depth interpolation review sample')
    args = parser.parse_args()
    if args.depth_sample:
        if args.publish:
            raise ValueError('Subway depth geometry requires renderer review before publication')
        depth_sample()
        return
    datetime.strptime(args.day, '%Y%m%d')
    if not args.train.isdigit() or any('/' in v or '\\' in v or '..' in v for v in (args.origin, args.destination)):
        raise ValueError('Invalid snapshot identifier')
    paths = [LOCAL/'raw'/'korail'/f'{args.day}-{name}.json' for name in (args.origin, args.destination)]
    rows = []
    for path in paths:
        matches = [r for r in json.loads(path.read_text(encoding='utf-8'))['records']
                   if r['run_ymd'] == args.day and r['trn_no'] == args.train]
        if len(matches) != 1:
            raise ValueError('Expected exactly one event per selected train and station')
        rows.append(matches[0])
    graph_path = LOCAL/'silver'/'rail-graph.json'
    graph_payload = json.loads(graph_path.read_text(encoding='utf-8'))
    prefix = graph_payload['source_hash'][:12]
    station_path = PUBLIC/'osm'/f'stations-{prefix}.geojson'
    rail_path = PUBLIC/'osm'/f'rail-{prefix}.geojson'
    stations = json.loads(station_path.read_text(encoding='utf-8'))['features']
    way_props = {f['id'].split('/')[-1]: f['properties']
                 for f in json.loads(rail_path.read_text(encoding='utf-8'))['features']}
    graph = make_graph(graph_payload, way_props)
    source = {'event_hash': hashlib.sha256(''.join(digest(p) for p in paths).encode()).hexdigest(),
              'event_files': [{'name': p.name, 'sha256': digest(p)} for p in paths],
              'retrieved_at': max(json.loads(p.with_suffix('.meta.json').read_text(encoding='utf-8'))['retrieved_at'] for p in paths),
              'graph_hash': digest(graph_path), 'osm_extract_hash': graph_payload['source_hash'],
              'station_hash': digest(station_path), 'rail_properties_hash': digest(rail_path)}
    body, audit = build_replay(*rows, graph, stations, source)
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    target = (PUBLIC/'replay'/'korail' if args.publish else LOCAL/'review'/'rail-routes')/f'route-{args.day}-{args.train}-{fingerprint}.json'
    atomic_json(target, body)
    atomic_json(LOCAL/'audit'/f'rail-route-{args.day}-{args.train}.json', audit)
    if args.publish:
        points = body['tracks'][0]['points']
        publish_file(target, asset_id=f'korail-route-{args.day}-{args.train}-{rows[0]["trn_run_sn"]}',
                     layer='rail', format='replay', source_id='korail', version=args.day, count=1,
                     bbox=[min(p['lon'] for p in points), min(p['lat'] for p in points),
                           max(p['lon'] for p in points), max(p['lat'] for p in points)],
                     **{'from': points[0]['time'], 'to': points[-1]['time'], 'label': '공식 시각 · 선로 계산 위치'})
    print(json.dumps({'output': str(target), 'audit': audit}, ensure_ascii=False))


if __name__ == '__main__':
    main()
