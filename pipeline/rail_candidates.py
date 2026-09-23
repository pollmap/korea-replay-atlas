"""Extract review-only planned/construction rail geometry from an existing PBF.

Two bounded passes retain only candidate ways and their nodes. This command never
publishes a catalog, geocodes a station or infers an official opening date.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path

import osmium

from .core import LOCAL, atomic_json, digest
from .rail_lifecycle import rail_lifecycle

RAIL_TYPES = ('rail', 'subway', 'light_rail', 'tram', 'monorail')
MAX_WAYS = 5000
MAX_NODES = 250000


def candidate_kind(tags):
    status = rail_lifecycle(tags)
    if status not in ('construction', 'proposed'):
        return None
    kind = tags.get('railway')
    if kind not in RAIL_TYPES:
        kind = tags.get(status + ':railway') or tags.get(status)
    return (status, kind) if kind in RAIL_TYPES else None


def build_candidates(ways, nodes, source_hash):
    if len(ways) > MAX_WAYS or len(nodes) > MAX_NODES:
        raise ValueError('Candidate extraction exceeds the bounded review budget')
    features, unresolved = [], []
    for way in ways:
        kind = candidate_kind(way['tags'])
        if not kind:
            continue
        coords = [nodes.get(str(node)) for node in way['nodes']]
        valid = len(coords) >= 2 and all(
            isinstance(point, (tuple, list)) and len(point) == 2
            and all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in point)
            and -180 <= point[0] <= 180 and -90 <= point[1] <= 90 for point in coords)
        if not valid:
            unresolved.append({'source_record_id': f'way/{way["id"]}', 'reason': 'missing_or_invalid_original_nodes'})
            continue
        status, rail_type = kind
        features.append({'type': 'Feature', 'id': f'way/{way["id"]}',
                         'geometry': {'type': 'LineString', 'coordinates': coords},
                         'properties': {
                             'name': way['tags'].get('name', ''), 'kind': 'rail_candidate',
                             'source_id': 'osm', 'source_record_id': f'way/{way["id"]}',
                             'source_sha256': source_hash, 'source_tags': dict(way['tags']),
                             'source_node_ids': list(way['nodes']),
                             'source_version': way.get('version'), 'source_timestamp': way.get('timestamp'),
                             'railway_kind': rail_type, 'rail_status': status,
                             'status_evidence': 'osm_tag', 'official_status_verified': False,
                             'geometry_review': 'unverified', 'publication_state': 'review_only',
                             'eligible_for_operating_routes': False, 'opening_date': None,
                             'evidence_type': 'source_attribute',
                             'description': 'OSM 시점의 공사·계획 표기입니다. 공식 사업 단계, 개통일, 노선 위치·범위와 대조하지 않은 검토 후보입니다.',
                         }})
    report = {'schema_version': 1, 'source_id': 'osm', 'source_sha256': source_hash,
              'publication_state': 'review_only', 'official_status_verified': False,
              'candidate_ways': len(features),
              'by_status': dict(Counter(f['properties']['rail_status'] for f in features)),
              'by_rail_type': dict(Counter(f['properties']['railway_kind'] for f in features)),
              'retained_vertices': sum(len(f['geometry']['coordinates']) for f in features),
              'unresolved': unresolved,
              'geometry_policy': 'original ordered PBF nodes; no simplification, station joining or gap bridging',
              'limitations': ['Not a complete inventory of nationwide projects',
                              'OSM status may be outdated or incorrect; official status and geometry review required',
                              'No official opening date, underground elevation or train operations inferred']}
    return {'type': 'FeatureCollection', 'features': features}, report


def extract(path):
    filename = os.path.relpath(path, Path.cwd())
    ways, needed = [], set()
    class Ways(osmium.SimpleHandler):
        def way(self, way):
            if (way.tags.get('railway') not in (*RAIL_TYPES, 'construction', 'proposed')
                    and not way.tags.get('construction:railway') and not way.tags.get('proposed:railway')):
                return
            tags = dict(way.tags)
            if not candidate_kind(tags):
                return
            refs = [node.ref for node in way.nodes]
            ways.append({'id': way.id, 'version': way.version, 'timestamp': str(way.timestamp), 'nodes': refs, 'tags': tags})
            needed.update(refs)
            if len(ways) > MAX_WAYS or len(needed) > MAX_NODES:
                raise ValueError('Candidate extraction exceeds the bounded review budget')
    Ways().apply_file(filename)
    nodes = {}
    class Nodes(osmium.SimpleHandler):
        def node(self, node):
            if node.id in needed and node.location.valid():
                nodes[str(node.id)] = [node.location.lon, node.location.lat]
    # Reject unrelated node IDs in libosmium, before millions of Python callbacks.
    Nodes().apply_file(filename, filters=[osmium.filter.IdFilter(needed)])
    return build_candidates(ways, nodes, digest(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pbf', type=Path, default=LOCAL/'raw/osm/south-korea-20260916.osm.pbf')
    parser.add_argument('--output', type=Path, default=LOCAL/'review/rail-candidates')
    args = parser.parse_args()
    body, report = extract(args.pbf)
    filename = f'rail-candidates-{report["source_sha256"][:12]}.geojson'
    output = args.output/filename
    atomic_json(output, body)
    report.update({'generated_at': datetime.now(timezone.utc).isoformat(), 'candidate_file': filename,
                   'candidate_sha256': digest(output), 'candidate_bytes': output.stat().st_size})
    atomic_json(args.output/'audit.json', report)
    print(json.dumps({key: value for key, value in report.items() if key != 'unresolved'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
