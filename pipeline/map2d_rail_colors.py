"""Build a small, evidence-linked 2D rail identity table without editing map tiles.

Only direct members of explicitly identified operating Seoul lines 1..8,
Suin-Bundang and Daejeon line 1 qualify. Shared/conflicting routes remain neutral. OSM identities are
not operator-verified route geometry and do not establish future openings.
Explicit crossover/siding/yard source ways separately suppress route labels.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import zlib

import osmium

LINE_NAME = re.compile(r'^(?:서울 지하철|수도권 전철) ([1-8])호선(?:$|[: ])')
SUIN_BUNDANG_NAME = re.compile(r'^수인·분당선(?: 급행)?:')
NON_ROUTE_SERVICES = frozenset(('crossover', 'siding', 'yard'))
SUIN_BUNDANG_DISPLAY = {
    'web_display_rgb': '#FFC500', 'meaning': 'operator_web_map_display_not_universal_rgb_standard',
    'operator': '한국철도공사', 'reference_date': '2026-09-01', 'checked_date': '2026-09-27',
    'page_url': 'https://info.korail.com/info/contents.do?key=2950',
    'web_image_url': 'https://info.korail.com/DATA/contents/main/20260831064821771_8r6s.jpg',
    'web_image_sha256': 'ff8b924e9777fc85d06abce1b724fbe67b7c32c431e10536793f31383d7fef28',
    'vector_url': 'https://info.korail.com/downloadContentsFile.do?key=2950&fileNo=1605',
    'vector_sha256': '625ea7c69fb91f27a50441d1b12c3c180da99ce642ad8856e8d0534472538c2b',
    'vector_device_cmyk': [0, 0.25, 1, 0],
    'web_pixel_rgb_samples': [[255, 197, 0], [255, 197, 1], [255, 197, 2]],
    'pdfium_5_13_0_render_rgb': '#FEC210',
}
DAEJEON_DISPLAY = {
    'web_display_rgb': '#016934', 'meaning': 'official_city_web_map_display_not_universal_rgb_standard',
    'source_authority': '대전광역시 철도정책과', 'reference_date': '2025-05-09', 'checked_date': '2026-09-27',
    'page_url': 'https://daejeon.go.kr/drh/drhStoryDaejeonView.do?boardId=blog_0001&menuSeq=1629&ntatcSeq=1482201145&pageIndex=1',
    'web_image_url': 'https://www.daejeon.go.kr/plugins/crosseditor4/binary/images/000373/20250512083526901_6JURABR9.jpg',
    'web_image_sha256': 'd094f38257330267e30c0c637c99eaaad51f86e5c61074ba0db9da53362b2525',
    'web_pixel_rgb_samples': [[1, 105, 52], [2, 104, 54], [0, 106, 52]],
    'scope': 'existing_operating_line_1_only_no_planned_lines',
    'conflicting_operator_artwork': {
        'url': 'https://www.djtc.kr/kor/resources/routemap/korean.zip',
        'ai_sha256': 'dc7a4265ad2f209f542bf32ae2317e4595994e4e12175f44e84c3dd7b3bc4231',
        'vector_cmyk': [0, 0.631, 1, 0], 'appearance': 'orange',
        'decision': 'use_dated_2025_city_map_existing_line_1_green',
    },
}


def operating_route(tags):
    return (tags.get('type') == 'route' and tags.get('route') in ('subway', 'train')
            and not any(tags.get(key) for key in ('construction', 'construction:route', 'proposed', 'disused', 'abandoned'))
            and tags.get('state') in (None, '', 'active'))


def suin_bundang_line(tags):
    if (operating_route(tags) and SUIN_BUNDANG_NAME.match(tags.get('name', ''))
            and tags.get('network') == '수도권 전철' and tags.get('ref') == '수인·분당'
            and tags.get('operator') == '한국철도공사'):
        return 'suin-bundang'
    return None


def daejeon_line(tags):
    if (operating_route(tags) and tags.get('route') == 'subway'
            and tags.get('name', '').startswith('대전 도시철도 1호선:')
            and tags.get('network') == '대전 도시철도' and tags.get('ref') == '1'
            and tags.get('operator') == '대전교통공사'):
        return 'daejeon-1'
    return None


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
    operating_memberships = defaultdict(set)
    relations = []
    non_route_ways = {}

    class Routes(osmium.SimpleHandler):
        def way(self, way):
            # Source service tags describe operational track parts, not the
            # advertised passenger route. Retain geometry and suppress only names.
            service = way.tags.get('service')
            if way.tags.get('railway') in ('rail', 'subway', 'light_rail', 'tram') and service in NON_ROUTE_SERVICES:
                non_route_ways[f'way/{way.id}'] = service

        def relation(self, relation):
            tags = dict(relation.tags)
            line = seoul_line(tags) or suin_bundang_line(tags) or daejeon_line(tags)
            if operating_route(tags):
                for member in relation.members:
                    if member.type == 'w':
                        operating_memberships[f'way/{member.ref}'].add(line or f'other-route/{relation.id}')
            if not line:
                return
            relations.append({'id': str(relation.id), 'line': line, 'name': tags['name']})
            for member in relation.members:
                # Platforms can be shared by multiple lines. Conflicts below
                # deliberately retain neutral station styling.
                if member.type in ('w', 'n'):
                    identities[f"{'way' if member.type == 'w' else 'node'}/{member.ref}"].add(line)

    Routes().apply_file(str(pbf), filters=[osmium.filter.EntityFilter(osmium.osm.WAY | osmium.osm.RELATION)])
    records = {}
    suppressed_labels = {}
    for index in indexes:
        with closing(sqlite3.connect(index.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            for stable, raw_record in db.execute("SELECT stable,record FROM records WHERE topic='rail'"):
                record = json.loads(zlib.decompress(raw_record))
                if record['source_id'] != 'osm':
                    continue
                identity = record['source_record_id']
                key = stable[:16]
                if identity in non_route_ways:
                    value = {'source_record_id': identity, 'service': non_route_ways[identity]}
                    if key in suppressed_labels and suppressed_labels[key] != value:
                        raise ValueError('Map label identity collision')
                    suppressed_labels[key] = value
                lines = identities.get(identity, set())
                if len(lines) != 1:
                    continue
                line = next(iter(lines))
                # General-purpose tracks such as 경부선 carry intercity trains
                # as well as metro services. The new commuter route permits rail
                # only when no other operating train/subway route shares the way.
                if line in ('suin-bundang', 'daejeon-1'):
                    allowed_types = ('rail', 'subway') if line == 'suin-bundang' else ('subway',)
                    if (record['properties'].get('railway') not in allowed_types
                            or operating_memberships.get(identity) != {line}):
                        continue
                elif record['properties'].get('railway') != 'subway':
                    continue
                value = {'source_record_id': identity, 'line': line}
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
            'identity_method': 'direct_operating_route_membership_subway_or_exclusive_suin_bundang_rail_way',
            'display_colors': {'suin-bundang': SUIN_BUNDANG_DISPLAY, 'daejeon-1': DAEJEON_DISPLAY},
            'label_suppression_method': 'explicit_osm_way_service_crossover_siding_yard',
            'suppressed_labels': dict(sorted(suppressed_labels.items())),
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
                      'suppressed_labels': len(result['suppressed_labels']),
                      'source_sha256': result['source_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
