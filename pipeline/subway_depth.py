"""Bounded Seoul lines 1–8 depth interpolation on OSM route-member geometry.

Three streaming PBF passes retain only target relations, their ways and nodes.
No network fetches, missing-depth imputation or train schedules are generated.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re

import networkx as nx
import osmium

from .core import LOCAL, PUBLIC, atomic_json, digest
from .rail_routes import distance, interpolate_station_depths

VERSION = 'seoul-subway-route-depth-v1'
LIMITS = {'station_match_m': 400, 'snap_m': 120, 'min_length_m': 150,
          'max_length_m': 6000, 'max_detour': 3.5, 'max_gradient': .08}


class Unresolved(ValueError):
    pass


def selected_relation(tags):
    return (tags.get('type') == 'route' and tags.get('route') == 'subway'
            and tags.get('ref') in tuple('12345678')
            and any(s in tags.get('name', '') for s in ('서울', '수도권'))
            and tags.get('state') not in ('proposed', 'temporary'))


def normalize_name(name):
    # Drop parenthetical secondary names; never fuzzy-match or rename a station.
    return re.sub(r'\s+', '', re.sub(r'\([^)]*\)', '', name.split(' · ')[0])).strip()


def extract_pbf(path, source_hash):
    # libosmium's Windows file opening can reject an absolute Unicode parent
    # path. The project-relative filename stays ASCII in the standard layout.
    filename = os.path.relpath(path, Path.cwd())
    relations = []
    class Relations(osmium.SimpleHandler):
        def relation(self, item):
            tags = dict(item.tags)
            if selected_relation(tags):
                relations.append({'id': item.id, 'tags': tags,
                                  'members': [[m.type, m.ref, m.role] for m in item.members]})
    Relations().apply_file(filename)
    way_ids = {ref for r in relations for kind, ref, role in r['members']
               if kind == 'w' and role in ('', 'forward', 'backward')}
    needed_nodes = {ref for r in relations for kind, ref, role in r['members']
                    if kind == 'n' and (role.startswith('stop') or role.startswith('platform'))}
    ways = {}
    class Ways(osmium.SimpleHandler):
        def way(self, item):
            if item.id in way_ids:
                refs = [n.ref for n in item.nodes]
                ways[str(item.id)] = {'nodes': refs, 'tags': dict(item.tags)}
                needed_nodes.update(refs)
    Ways().apply_file(filename)
    nodes = {}
    class Nodes(osmium.SimpleHandler):
        def node(self, item):
            if item.id in needed_nodes and item.location.valid():
                nodes[str(item.id)] = {'coord': [item.location.lon, item.location.lat],
                                       'tags': dict(item.tags)}
    Nodes().apply_file(filename)
    return {'source_hash': source_hash, 'relations': relations, 'ways': ways, 'nodes': nodes}


def relation_stops(relation):
    members = relation['members']
    stops = [(kind, ref, role) for kind, ref, role in members if role.startswith('stop')]
    if not stops:
        stops = [(kind, ref, role) for kind, ref, role in members if role.startswith('platform')]
    # Preserve unresolved members in the sequence; never skip over missing stops.
    result = []
    for member in stops:
        if not result or member[:2] != result[-1][:2]:
            result.append(member)
    if relation['tags'].get('roundtrip') == 'yes' and len(result) > 1 and result[0][:2] != result[-1][:2]:
        result.append(result[0])
    return result


def relation_graph(relation, extract):
    graph = nx.Graph()
    ordered_ways = []
    for kind, ref, role in relation['members']:
        if kind != 'w' or role not in ('', 'forward', 'backward'):
            continue
        ordered_ways.append(str(ref))
        way = extract['ways'].get(str(ref))
        if not way or way['tags'].get('railway') not in ('subway', 'rail', 'light_rail'):
            continue
        if way['tags'].get('disused') == 'yes' or way['tags'].get('abandoned') == 'yes':
            continue
        for a, b in zip(way['nodes'], way['nodes'][1:]):
            if str(a) not in extract['nodes'] or str(b) not in extract['nodes']:
                continue
            ca, cb = extract['nodes'][str(a)]['coord'], extract['nodes'][str(b)]['coord']
            length = distance(ca, cb)
            if not math.isfinite(length) or not 0 < length < 10000:
                continue
            graph.add_node(a, coord=ca)
            graph.add_node(b, coord=cb)
            if graph.has_edge(a, b):
                graph[a][b]['way_ids'].add(str(ref))
            else:
                graph.add_edge(a, b, length=length, way_ids={str(ref)})
    return graph, ordered_ways


def depth_index(features):
    index = defaultdict(list)
    for feature in features:
        line = feature['id'].split(':')[0]
        index[(line, normalize_name(feature['properties']['name']))].append(feature)
    return index


def resolve_depth(member, line, extract, index):
    kind, ref, role = member
    if kind != 'n':
        raise Unresolved('unsupported_stop_member_geometry')
    node = extract['nodes'].get(str(ref))
    if not node:
        raise Unresolved('missing_stop_node')
    names = {normalize_name(node['tags'].get(k, '')) for k in ('name', 'name:ko', 'official_name', 'short_name')}
    names.discard('')
    if not names:
        raise Unresolved('missing_stop_name')
    matches = {f['id']: f for name in names for f in index.get((line, name), [])}
    if not matches:
        raise Unresolved('no_official_depth_name_match')
    if len(matches) != 1:
        raise Unresolved('ambiguous_official_depth_name')
    feature = next(iter(matches.values()))
    props = feature['properties']
    if props.get('evidence_type') != 'official_record':
        raise Unresolved('nonofficial_depth')
    try:
        depth, surface = float(props['rail_depth']), float(props['surface_height'])
    except (KeyError, ValueError, TypeError):
        raise Unresolved('missing_depth_or_ground') from None
    if not all(map(math.isfinite, (depth, surface))) or not -50 < depth < 150:
        raise Unresolved('invalid_depth_or_ground')
    separation = distance(node['coord'], feature['geometry']['coordinates'])
    if separation > LIMITS['station_match_m']:
        raise Unresolved('official_osm_station_position_mismatch')
    return feature, node, separation


def anchor_node(graph, ref, node):
    if ref in graph:
        return ref, 0.0
    matches = sorted((distance(node['coord'], d['coord']), n) for n, d in graph.nodes(data=True)
                     if abs(d['coord'][0]-node['coord'][0]) < .003
                     and abs(d['coord'][1]-node['coord'][1]) < .003)
    if not matches or matches[0][0] > LIMITS['snap_m']:
        raise Unresolved('stop_not_on_relation_geometry')
    return matches[0][1], matches[0][0]


def ordered_members_match(edge_way_sets, ordered_ways):
    """Every path edge must traverse consecutive relation way members in order."""
    # Multiple member occurrences and overlapping ways are retained rather than
    # choosing an arbitrary shared-edge way ID. Static geometry may be reversed.
    for sequence in (ordered_ways, list(reversed(ordered_ways))):
        states = set()
        for i, options in enumerate(edge_way_sets):
            positions = {j for j, way in enumerate(sequence) if way in options}
            states = positions if i == 0 else {j for j in positions if j in states or j-1 in states}
            if not states:
                break
        if states:
            return True
    return False


def segment_geometry(relation, pair, graph, ordered_ways, extract, index, all_stops):
    first, last = pair
    a, na, da = resolve_depth(first, relation['tags']['ref'], extract, index)
    b, nb, db = resolve_depth(last, relation['tags']['ref'], extract, index)
    if a['id'] == b['id']:
        raise Unresolved('same_station_pair')
    start, sa = anchor_node(graph, first[1], na)
    end, sb = anchor_node(graph, last[1], nb)
    try:
        path = nx.shortest_path(graph, start, end, weight='length')
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        raise Unresolved('disconnected_relation_geometry') from None
    if len(path) < 2:
        raise Unresolved('collapsed_station_pair')
    if set(path[1:-1]) & (all_stops-{first[1], last[1]}):
        raise Unresolved('path_crosses_another_route_stop')
    edges = [graph[u][v] for u, v in zip(path, path[1:])]
    if not ordered_members_match([e['way_ids'] for e in edges], ordered_ways):
        raise Unresolved('path_conflicts_with_relation_way_order')
    length = sum(e['length'] for e in edges)
    direct = distance(na['coord'], nb['coord'])
    if not LIMITS['min_length_m'] <= length <= LIMITS['max_length_m']:
        raise Unresolved('implausible_interval_length')
    if direct <= 0 or length/direct > LIMITS['max_detour']:
        raise Unresolved('implausible_interval_detour')
    za = a['properties']['surface_height']-a['properties']['rail_depth']
    zb = b['properties']['surface_height']-b['properties']['rail_depth']
    gradient = abs(zb-za)/length
    if gradient > LIMITS['max_gradient']:
        raise Unresolved('implausible_interpolated_gradient')
    route = {'coordinates': [graph.nodes[n]['coord'] for n in path], 'length_m': length,
             'way_ids': sorted(set.union(*(e['way_ids'] for e in edges)))}
    feature = interpolate_station_depths(route, a, b)
    node_key = min(tuple(path), tuple(reversed(path)))
    key = f'{relation["tags"]["ref"]}:{min(a["id"], b["id"])}:{max(a["id"], b["id"])}:'+hashlib.sha256(json.dumps(node_key).encode()).hexdigest()[:12]
    feature['id'] = 'subway-depth:'+key
    feature['properties'].update({'line': relation['tags']['ref'], 'osm_relation_ids': [relation['id']],
                                  'osm_stop_node_ids': [first[1], last[1]],
                                  'stop_sequence_evidence': 'OSM route relation member order',
                                  'length_m': length, 'interpolated_gradient': gradient,
                                  'official_osm_station_distance_m': [da, db],
                                  'stop_anchor_distance_m': [sa, sb],
                                  'endpoint_rail_depth_m': [a['properties']['rail_depth'], b['properties']['rail_depth']],
                                  'transform_version': VERSION})
    return key, feature


def build(extract, depths):
    index = depth_index(depths)
    output = {}
    unresolved = []
    relation_counts = Counter()
    resolved_pairs = 0
    attempts = 0
    for relation in extract['relations']:
        line = relation['tags']['ref']
        relation_counts[line] += 1
        graph, ways = relation_graph(relation, extract)
        stops = relation_stops(relation)
        stop_ids = {ref for kind, ref, _ in stops if kind == 'n'}
        for first, last in zip(stops, stops[1:]):
            attempts += 1
            try:
                key, feature = segment_geometry(relation, (first, last), graph, ways, extract, index, stop_ids)
            except Unresolved as error:
                unresolved.append({'relation_id': relation['id'], 'line': line,
                                   'stop_ids': [first[1], last[1]], 'reason': str(error),
                                   'stop_names': [extract['nodes'].get(str(m[1]), {}).get('tags', {}).get('name') for m in (first, last)]})
                continue
            resolved_pairs += 1
            if key in output:
                if relation['id'] not in output[key]['properties']['osm_relation_ids']:
                    output[key]['properties']['osm_relation_ids'].append(relation['id'])
            else:
                output[key] = feature
    features = list(output.values())
    covered_ids = {sid for f in features for sid in f['properties']['source_station_ids']}
    by_line = {}
    for line in '12345678':
        subset = [f for f in features if f['properties']['line'] == line]
        by_line[line] = {'relations': relation_counts[line], 'geometries': len(subset),
                         'unique_station_pairs': len({tuple(sorted(f['properties']['source_station_ids'])) for f in subset}),
                         'covered_depth_stations': len({sid for f in subset for sid in f['properties']['source_station_ids']}),
                         'unresolved_pair_attempts': sum(u['line'] == line for u in unresolved)}
    report = {'transform_version': VERSION, 'osm_extract_hash': extract['source_hash'],
              'relations': len(extract['relations']), 'pair_attempts': attempts,
              'resolved_pair_attempts': resolved_pairs, 'unresolved_pair_attempts': len(unresolved),
              'unresolved_reasons': dict(Counter(u['reason'] for u in unresolved)),
              'unique_geometries': len(features),
              'unique_station_pairs': len({tuple(sorted(f['properties']['source_station_ids'])) for f in features}),
              'official_depth_stations': len(depths), 'covered_depth_stations': len(covered_ids),
              'uncovered_depth_station_ids': sorted(f['id'] for f in depths if f['id'] not in covered_ids),
              'by_line': by_line, 'validation_limits': LIMITS, 'unresolved': unresolved,
              'scope': 'static estimated vertical geometry, no train events or measured tunnel profile'}
    return {'type': 'FeatureCollection', 'features': features}, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pbf', type=Path, default=LOCAL/'raw'/'osm'/'south-korea-20260916.osm.pbf')
    args = parser.parse_args()
    directory = LOCAL/'review'/'subway-depth'
    source_hash = digest(args.pbf)
    cache = directory/f'osm-route-extract-{source_hash[:12]}.json'
    if cache.exists():
        extract = json.loads(cache.read_text(encoding='utf-8'))
        if extract.get('source_hash') != source_hash:
            raise ValueError('PBF extraction cache source mismatch')
    else:
        extract = extract_pbf(args.pbf, source_hash)
        atomic_json(cache, extract)
    catalog = json.loads((PUBLIC/'catalog.json').read_text(encoding='utf-8'))
    asset = next(a for a in catalog['assets'] if a['id'] == 'depth-seoul')
    depth_path = PUBLIC/asset['url'].removeprefix('/data/')
    if digest(depth_path) != asset['sha256']:
        raise ValueError('Official depth asset hash mismatch')
    depths = json.loads(depth_path.read_text(encoding='utf-8'))['features']
    body, audit = build(extract, depths)
    for feature in body['features']:
        feature['properties']['osm_extract_hash'] = source_hash
        feature['properties']['depth_file_sha256'] = asset['sha256']
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    output = directory/f'seoul-lines1-8-{fingerprint}.geojson'
    atomic_json(output, body)
    audit.update({'output': str(output), 'depth_file_sha256': asset['sha256'], 'output_sha256': digest(output)})
    atomic_json(directory/'audit.json', audit)
    print(json.dumps({k: v for k, v in audit.items() if k not in ('unresolved', 'uncovered_depth_station_ids')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
