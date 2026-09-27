"""Build bounded, provenance-preserving property POI chunks from the audited OSM PBF.

This is a partial OSM observation, not a complete register or an accessibility
model. Existing source, map tiles and 3D publication are never modified.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path

import osmium
import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, shape

from pipeline.region_selection import ARCHIVE_SHA, decode_ring

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = '3135b6ec7b3d94294735de0aa47d49a58c06b638bf33ba44e30b5728cc5b76c7'
SOURCE_AS_OF = '2026-09-15T20:20:37Z'
SOURCE_URL = 'https://download.geofabrik.de/asia/south-korea-latest.osm.pbf'
SCOPE_CODES = ('11', '23', '31', '25', '29', '21', '34011', '34012', '34040',
               '33041', '33042', '33043', '33044')
TYPES = {
    'subway': ('transport', '지하철역'), 'rail': ('transport', '철도역'),
    'bus': ('transport', '버스정류장'),
    'elementary': ('school', '초등학교'), 'middle': ('school', '중학교'),
    'high': ('school', '고등학교'), 'university': ('school', '대학교'),
    'school': ('school', '학교'), 'shopping': ('life', '쇼핑시설'),
    'medical': ('life', '의료시설'), 'park': ('life', '공원'),
    'public': ('life', '공공시설'),
}
TAG_KEYS = ('amenity', 'healthcare', 'leisure', 'shop', 'railway', 'station',
            'public_transport', 'highway', 'bus', 'subway', 'train',
            'isced:level', 'office', 'operator', 'ref')
TARGET_BYTES = 256 * 1024
MAX_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_CHUNKS = 1000


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def osmium_path(path):
    # The Windows libosmium file opener cannot resolve this workspace's Korean
    # absolute path. A relative path keeps the already-open cwd out of its input.
    return os.path.relpath(Path(path).resolve(), Path.cwd())


def classify(tags):
    """One explicit source classification; school names never imply a level."""
    if any(tags.get(key) not in (None, '', 'no', 'false', '0') for key in
           ('disused', 'abandoned', 'demolished', 'razed', 'construction', 'proposed')):
        return None
    if tags.get('railway') in ('station', 'halt'):
        return 'subway' if tags.get('station') == 'subway' or tags.get('subway') == 'yes' else 'rail'
    if tags.get('highway') == 'bus_stop' or tags.get('amenity') == 'bus_station' or (
        tags.get('public_transport') in ('platform', 'stop_position') and tags.get('bus') == 'yes'
    ):
        return 'bus'
    amenity = tags.get('amenity')
    if amenity == 'school':
        return {'1': 'elementary', '2': 'middle', '3': 'high'}.get(tags.get('isced:level'), 'school')
    if amenity == 'university':
        return 'university'
    if amenity in ('hospital', 'clinic', 'doctors', 'dentist', 'pharmacy') or tags.get('healthcare') in (
        'hospital', 'clinic', 'doctor', 'dentist', 'pharmacy', 'centre', 'physiotherapist'
    ):
        return 'medical'
    if tags.get('leisure') == 'park':
        return 'park'
    if tags.get('shop') in ('supermarket', 'convenience', 'mall', 'department_store',
                           'greengrocer', 'bakery', 'butcher', 'general', 'wholesale') or amenity == 'marketplace':
        return 'shopping'
    if amenity in ('library', 'community_centre', 'townhall', 'police', 'fire_station', 'post_office') or tags.get('office') == 'government':
        return 'public'
    return None


def load_scope(manifest_path):
    path = Path(manifest_path)
    manifest = json.loads(path.read_bytes())
    if manifest.get('schema') != 1 or manifest.get('namespace') != 'SGIS administrative' or manifest.get('referenceDate') != '2025-06-30' or manifest.get('archiveSha256') != ARCHIVE_SHA:
        raise ValueError('Unexpected audited SGIS boundary source')
    error = manifest.get('maximumDisplayErrorMetres')
    if not isinstance(error, (int, float)) or not 0 <= error <= 20.02:
        raise ValueError('Unexpected SGIS boundary approximation')
    refs = {row[0]: row for row in manifest['chunks']}
    polygons = []
    for code in SCOPE_CODES:
        ref = refs.get(code)
        if not ref:
            raise ValueError(f'Missing scope boundary: {code}')
        chunk = path.parent / 'region-selection-areas' / f'boundary-{code}.json'
        body = chunk.read_bytes()
        if len(body) != ref[1] or hashlib.sha256(body).hexdigest() != ref[2]:
            raise ValueError(f'Boundary integrity mismatch: {code}')
        rows = json.loads(body)
        if len(rows) != 1 or rows[0][0] != code:
            raise ValueError('Boundary identity mismatch')
        _, _, precision, parts = rows[0]
        pieces = [Polygon(decode_ring(part[0], precision),
                          [decode_ring(ring, precision) for ring in part[1:]]) for part in parts]
        geometry = pieces[0] if len(pieces) == 1 else MultiPolygon(pieces)
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError('Invalid scope boundary')
        polygons.append(geometry)
    return Scope(polygons, SCOPE_CODES), {
        'boundaryDate': manifest['referenceDate'], 'regionCodes': list(SCOPE_CODES),
        'boundaryNamespace': manifest['namespace'], 'boundaryManifestSha256': sha256(path),
        'boundaryArchiveSha256': ARCHIVE_SHA, 'maximumDisplayErrorMetres': error,
        'coverageComplete': False,
    }


class Scope:
    def __init__(self, geometries, codes):
        self.geometries = list(geometries)
        self.codes = list(codes)
        self.tree = shapely.STRtree(self.geometries)
        for geometry in self.geometries:
            shapely.prepare(geometry)

    def region(self, point):
        # Covers includes the source boundary; holes remain excluded.
        for index in sorted(self.tree.query(point)):
            if self.geometries[index].covers(point):
                return self.codes[index]
        return None


def verify_source(path):
    path = Path(path)
    meta = json.loads(path.with_suffix(path.suffix + '.meta.json').read_bytes())
    if meta.get('url') != SOURCE_URL or meta.get('sha256') != SOURCE_SHA or meta.get('bytes') != path.stat().st_size:
        raise ValueError('OSM source metadata differs from audited source')
    if sha256(path) != SOURCE_SHA:
        raise ValueError('OSM source SHA differs from audited source')
    with osmium.io.Reader(osmium_path(path), osmium.osm.NOTHING) as reader:
        snapshot = reader.header().get('osmosis_replication_timestamp')
    if snapshot != SOURCE_AS_OF:
        raise ValueError('OSM snapshot differs from audited source')
    return {'id': 'osm', 'label': 'OpenStreetMap contributors', 'url': SOURCE_URL,
            'asOf': snapshot, 'sha256': SOURCE_SHA, 'license': 'ODbL-1.0'}


def extract(path, scope):
    records = {}
    counts = Counter()
    factory = osmium.geom.GeoJSONFactory()

    def emit(identifier, tags, geometry, method):
        kind = classify(tags)
        if kind is None:
            counts['unselected'] += 1
            return
        counts['classified'] += 1
        if geometry.is_empty or not geometry.is_valid:
            counts['invalidGeometry'] += 1
            return
        point = geometry if method == 'original_node' else (
            geometry.interpolate(.5, normalized=True) if method == 'line_midpoint' else geometry.representative_point())
        lon, lat = point.x, point.y
        if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            counts['invalidCoordinate'] += 1
            return
        region = scope.region(point)
        if region is None:
            counts['outsideScope'] += 1
            return
        raw_name = (tags.get('name:ko') or tags.get('name') or '').strip()
        record = {'id': identifier, 'name': raw_name or TYPES[kind][1],
                  'category': TYPES[kind][0], 'type': kind, 'longitude': lon, 'latitude': lat,
                  'positionMethod': method, 'scopeRegionCode': region,
                  'sourceTags': {key: tags[key] for key in TAG_KEYS if tags.get(key)}}
        if not raw_name:
            record['nameIsFallback'] = True
            counts['unnamedIncluded'] += 1
        address = ' '.join(tags.get(key, '').strip() for key in ('addr:city', 'addr:district', 'addr:street', 'addr:housenumber')).strip()
        if address:
            record['address'] = address
        if identifier in records:
            raise ValueError(f'Duplicate source representation: {identifier}')
        records[identifier] = record
        counts['included'] += 1
        if counts['included'] % 25000 == 0:
            print(json.dumps({'phase': 'extract', 'included': counts['included'], 'outsideScope': counts['outsideScope']}), flush=True)

    class Handler(osmium.SimpleHandler):
        def node(self, node):
            counts['filteredNodesRead'] += 1
            tags = dict(node.tags)
            if classify(tags) is None:
                counts['unselected'] += 1
                return
            if not node.location.valid():
                counts['nodeMissingLocation'] += 1
                return
            emit(f'node/{node.id}', tags, Point(node.location.lon, node.location.lat), 'original_node')

        def way(self, way):
            counts['filteredWaysRead'] += 1
            tags = dict(way.tags)
            if classify(tags) is None:
                counts['unselected'] += 1
                return
            if way.is_closed() and tags.get('area') != 'no':
                counts['waysDeferredToArea'] += 1
                return
            try:
                coordinates = [(node.lon, node.lat) for node in way.nodes]
                if len(coordinates) < 2:
                    counts['shortWay'] += 1
                    return
                emit(f'way/{way.id}', tags, LineString(coordinates), 'line_midpoint')
            except osmium.InvalidLocationError:
                counts['wayMissingLocation'] += 1

        def area(self, area):
            counts['filteredAreasRead'] += 1
            tags = dict(area.tags)
            if classify(tags) is None:
                counts['unselected'] += 1
                return
            identifier = f'{"way" if area.from_way() else "relation"}/{area.orig_id()}'
            try:
                geometry = shape(json.loads(factory.create_multipolygon(area)))
            except (RuntimeError, ValueError):
                counts['areaAssemblyError'] += 1
                return
            emit(identifier, tags, geometry, 'area_representative_point')

    keys = ('railway', 'public_transport', 'highway', 'amenity', 'healthcare', 'shop', 'leisure', 'office')
    # Locations are retained by libosmium before the tag filter. Python only sees
    # candidates; closed ways/relations preserve assembled holes in area().
    Handler().apply_file(osmium_path(path), locations=True, idx='flex_mem', filters=[osmium.filter.KeyFilter(*keys)])
    return sorted(records.values(), key=lambda row: row['id']), dict(sorted(counts.items()))


def partition(records, target_bytes=TARGET_BYTES):
    if not 1024 <= target_bytes <= MAX_BYTES:
        raise ValueError('Invalid POI chunk target')
    cells = defaultdict(list)
    for record in records:
        cells[(math.floor(record['longitude'] * 20), math.floor(record['latitude'] * 20))].append(record)

    def split(key, bounds, rows, depth=0):
        rows = sorted(rows, key=lambda row: row['id'])
        body = encoded({'schema': 1, 'records': rows})
        if len(body) <= target_bytes:
            yield key, bounds, body, len(rows)
            return
        if len(rows) == 1:
            if len(body) > MAX_BYTES:
                raise ValueError('One POI exceeds hard size limit')
            yield key, bounds, body, 1
            return
        west, south, east, north = bounds
        if depth < 12:
            mid_x, mid_y = (west + east) / 2, (south + north) / 2
            quadrants = defaultdict(list)
            for row in rows:
                quadrants[(int(row['longitude'] >= mid_x), int(row['latitude'] >= mid_y))].append(row)
            for (x, y), group in sorted(quadrants.items()):
                child = (mid_x if x else west, mid_y if y else south, east if x else mid_x, north if y else mid_y)
                yield from split(f'{key}-{x}{y}', child, group, depth + 1)
        else:
            # Coincident dense POIs must not be dropped just to meet a size target.
            middle = len(rows) // 2
            yield from split(key + '-a', bounds, rows[:middle], depth + 1)
            yield from split(key + '-b', bounds, rows[middle:], depth + 1)

    for (x, y), rows in sorted(cells.items()):
        yield from split(f'{x}-{y}', (x / 20, y / 20, (x + 1) / 20, (y + 1) / 20), rows)


def write_immutable(path, body):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('POI output symlink is not allowed')
    if path.exists():
        if path.read_bytes() != body:
            raise ValueError(f'Refusing to replace different POI output: {path.name}')
        return
    # Exclusive creation also prevents a concurrent writer from replacing data.
    with path.open('xb') as stream:
        stream.write(body)


def publish(records, source, scope, counters, output, target_bytes=TARGET_BYTES):
    output = Path(output)
    if output.is_symlink():
        raise ValueError('POI output symlink is not allowed')
    ids = [record['id'] for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate POI ID')
    chunks = list(partition(records, target_bytes))
    if len(chunks) > MAX_CHUNKS or sum(len(row[2]) for row in chunks) > MAX_TOTAL_BYTES:
        raise ValueError('POI release exceeds total chunk/byte budget')
    refs = []
    recovered_ids = []
    for key, bounds, body, count in chunks:
        if len(body) > MAX_BYTES:
            raise ValueError('POI chunk exceeds hard size limit')
        digest = hashlib.sha256(body).hexdigest()
        refs.append(dict(zip(('key', 'west', 'south', 'east', 'north'), (key, *bounds))))
        refs[-1].update(file=f'poi-{digest}.json', sha256=digest, bytes=len(body), count=count)
        recovered_ids.extend(row['id'] for row in json.loads(body)['records'])
    if sorted(ids) != sorted(recovered_ids):
        raise ValueError('Partition did not preserve all source IDs')
    manifest = {'schema': 1, 'source': source, 'scope': scope, 'chunks': refs}
    manifest_body = encoded(manifest)
    if len(manifest_body) > MAX_BYTES:
        raise ValueError('POI manifest exceeds size limit')
    audit = {'schema': 1, 'source': source, 'scope': scope, 'rawReadCompleted': True,
             'readCounters': counters, 'recordCount': len(records), 'uniqueSourceIds': len(set(ids)),
             'sourceIdSha256': hashlib.sha256(encoded(sorted(ids))).hexdigest(),
             'countsByCategory': dict(sorted(Counter(r['category'] for r in records).items())),
             'countsByType': dict(sorted(Counter(r['type'] for r in records).items())),
             'countsByScopeRegion': dict(sorted(Counter(r['scopeRegionCode'] for r in records).items())),
             'countsByPositionMethod': dict(sorted(Counter(r['positionMethod'] for r in records).items())),
             'chunkCount': len(chunks), 'totalChunkBytes': sum(len(row[2]) for row in chunks),
             'largestChunkBytes': max((len(row[2]) for row in chunks), default=0),
             'manifestSha256': hashlib.sha256(manifest_body).hexdigest(),
             'limitations': ['OSM coverage is partial; zero matches do not prove no real facilities.',
                            'Source nodes and calculated representatives are not surveyed entrances.',
                            'Scope uses SGIS administrative display geometry, not legal-dong geography.',
                            'No school assignment, walking route, timetable or current opening verification.']}
    output.mkdir(parents=True, exist_ok=True)
    for ref, (_, _, body, _) in zip(refs, chunks):
        write_immutable(output / ref['file'], body)
    write_immutable(output / 'audit.json', encoded(audit))
    # Promote the manifest last; a partial output is never a published candidate.
    write_immutable(output / 'manifest.json', manifest_body)
    return audit


def build(source, boundary, output):
    print(json.dumps({'phase': 'verify_source'}), flush=True)
    provenance = verify_source(source)
    scope, scope_info = load_scope(boundary)
    print(json.dumps({'phase': 'extract', 'sourceAsOf': provenance['asOf']}), flush=True)
    records, counters = extract(source, scope)
    audit = publish(records, provenance, scope_info, counters, output)
    print(json.dumps({key: audit[key] for key in ('recordCount', 'countsByType', 'chunkCount', 'totalChunkBytes', 'largestChunkBytes')}, ensure_ascii=False), flush=True)
    return audit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / '.local/raw/osm/south-korea-20260916.osm.pbf')
    parser.add_argument('--boundary', type=Path, default=ROOT / 'src/data/region-selection.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'src/data/property-poi')
    args = parser.parse_args()
    build(args.source, args.boundary, args.output)
