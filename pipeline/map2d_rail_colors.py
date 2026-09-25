"""Build a small, evidence-linked 2D rail identity table without editing map tiles.

Only direct members of explicitly named Seoul lines 1..8, without lifecycle
flags, qualify. Shared/conflicting routes remain neutral. OSM identities are
not operator-verified route geometry and do not establish future openings.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import zlib

import osmium

LINE_NAME = re.compile(r'^(?:서울 지하철|수도권 전철) ([1-8])호선(?:$|[: ])')


def seoul_line(tags):
    if tags.get('type') != 'route' or tags.get('route') not in ('subway', 'train'):
        return None
    if any(tags.get(key) for key in ('construction', 'construction:route', 'proposed', 'disused', 'abandoned')):
        return None
    if tags.get('state') not in (None, '', 'active'):
        return None
    match = LINE_NAME.match(tags.get('name', ''))
    if not match or tags.get('ref') != match[1]:
        return None
    if tags.get('network') != '수도권 전철':
        return None
    return match[1]


def build(pbf: Path, indexes: list[Path]):
    identities = defaultdict(set)
    relations = []

    class Routes(osmium.SimpleHandler):
        def relation(self, relation):
            tags = dict(relation.tags)
            line = seoul_line(tags)
            if not line:
                return
            relations.append({'id': str(relation.id), 'line': line, 'name': tags['name']})
            for member in relation.members:
                # Platforms can be shared by multiple lines. Conflicts below
                # deliberately retain neutral station styling.
                if member.type in ('w', 'n'):
                    identities[f"{'way' if member.type == 'w' else 'node'}/{member.ref}"].add(line)

    Routes().apply_file(str(pbf), filters=[osmium.filter.EntityFilter(osmium.osm.RELATION)])
    records = {}
    for index in indexes:
        with sqlite3.connect(index.resolve().as_uri() + '?mode=ro', uri=True) as db:
            for stable, raw_record in db.execute("SELECT stable,record FROM records WHERE topic='rail'"):
                record = json.loads(zlib.decompress(raw_record))
                if record['source_id'] != 'osm':
                    continue
                identity = record['source_record_id']
                lines = identities.get(identity, set())
                if len(lines) != 1:
                    continue
                # General-purpose tracks such as 경부선 carry intercity trains
                # as well as metro services. Keep them neutral.
                if record['properties'].get('railway') != 'subway':
                    continue
                value = {'source_record_id': identity, 'line': next(iter(lines))}
                key = stable[:16]
                if key in records and records[key] != value:
                    raise ValueError('Map display identity collision')
                records[key] = value
    h = hashlib.sha256()
    with pbf.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return {'schema_version': 1, 'source': 'OpenStreetMap contributors',
            'license': 'ODbL-1.0', 'source_url': 'https://www.openstreetmap.org/copyright',
            'source_date': '2026-09-16', 'source_sha256': h.hexdigest(),
            'identity_method': 'direct_operating_route_membership_and_subway_way',
            'relations': sorted(relations, key=lambda row: int(row['id'])),
            'records': dict(sorted(records.items()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pbf', type=Path, default=Path('.local/raw/osm/south-korea-20260916.osm.pbf'))
    parser.add_argument('--indexes', type=Path, default=Path('.local/map-tiles-20260920/work'))
    parser.add_argument('--output', type=Path, default=Path('shared/data/map2d-rail-colors.json'))
    args = parser.parse_args()
    result = build(args.pbf, sorted(args.indexes.glob('*/index.sqlite')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n', encoding='utf-8')
    print(json.dumps({'relations': len(result['relations']), 'features': len(result['records']),
                      'source_sha256': result['source_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
