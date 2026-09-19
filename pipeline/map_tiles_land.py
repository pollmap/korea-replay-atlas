"""Replace only a private MVT candidate's land theme with preserved SGIS polygons.

The census polygons are a land display source, not a surveyed coastline. The
existing OSM Dokdo records remain available. Other themes are hash-checked and
reused byte for byte, and no public pointer or existing candidate is written.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
import gzip
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import sqlite3
import time
import zlib

import mapbox_vector_tile
import numpy as np
from pmtiles.tile import zxy_to_tileid
import shapely
from shapely.geometry import MultiPolygon, Point, box, shape

from .admin_boundaries import encoded, immutable, require
from .core import digest
from .map_tiles import (DETAIL_TARGET, EXTENT, HARD, LOW_TARGET, TO_GEO,
    bbox_tiles, pack_archives, pack_details, reserve, sha, tile_bounds)

VERSION = 'sgis-land-display-1'
MINZOOM = 5
MAXZOOM = 12
SOURCE_RULE = "topic='admin-sido' OR (topic='land' AND json_extract(render,'$.source_id')='osm')"


@dataclass
class LandSource:
    stable: str
    geometry: object
    record: dict
    render: dict


def local_path(root, relative):
    path = PurePosixPath(relative)
    require(not path.is_absolute() and path.parts and all(p not in ('.', '..') for p in path.parts)
            and ':' not in relative and '\\' not in relative, 'Unsafe publication path')
    target = Path(root).joinpath(*path.parts)
    require(not target.is_symlink() and target.resolve().is_relative_to(Path(root).resolve()), 'Publication path escape')
    return target


def load_baseline(root):
    root = Path(root)
    publication = json.loads((root / 'publication.json').read_bytes())
    require(publication.get('status') == 'validated', 'Baseline publication is not validated')
    entry = publication['map_catalog']
    catalog_file = local_path(root, entry['path'])
    require(digest(catalog_file) == entry['sha256'], 'Baseline catalog hash differs')
    catalog = json.loads(catalog_file.read_bytes())
    require(catalog['release_id'] == entry['release_id'], 'Baseline release differs')
    files = {ref['path']: ref for ref in publication['files']}
    require(len(files) == len(publication['files']) == publication['file_count'], 'Duplicate baseline file')
    referenced = {entry['path']}
    for topic in catalog['topics']:
        for ref in topic['chunks'] + topic['details']:
            path = ref['url'].lstrip('/')
            require(path in files and all(files[path][k] == ref[k] for k in ('sha256', 'byte_length')), 'Baseline closure differs')
            require(ref['url'].startswith('/data/map-tiles/' + catalog['release_id'] + '/'), 'External baseline reference')
            referenced.add(path)
    require(referenced == set(files), 'Unexpected baseline file inventory')
    for ref in files.values():
        target = local_path(root, ref['path'])
        require(target.stat().st_size == ref['byte_length'] and digest(target) == ref['sha256'], 'Baseline asset hash differs')
    return publication, catalog


def load_sources(work, baseline, catalog):
    work = Path(work)
    inputs = json.loads((work / 'inputs.json').read_bytes())
    fingerprint = sha(encoded(inputs))
    db = sqlite3.connect(f'file:{(work / "index.sqlite").resolve().as_posix()}?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    require(db.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone() == (fingerprint,), 'Donor work identity differs')
    require(inputs['admin_sha256'] == 'f1cf0f9de453ac7eaacb273f39cee52851183372b9ddfda428a967c3a670b2c6', 'Unexpected SGIS original archive')
    expected = {}
    for topic in catalog['topics']:
        if topic['id'] not in ('admin-sido', 'land'):
            continue
        for ref in topic['details']:
            data = json.loads(gzip.decompress(local_path(baseline, ref['url'].lstrip('/')).read_bytes()))
            for record in data['records']:
                if topic['id'] == 'admin-sido' or record['source_id'] == 'osm':
                    expected[record['stable_id']] = record
    result = []; evidence = []
    for stable, raw_geometry, compressed, raw_render in db.execute(
            f'SELECT stable,geom,record,render FROM records WHERE {SOURCE_RULE} ORDER BY stable'):
        record = json.loads(zlib.decompress(compressed)); geometry = shapely.from_wkb(raw_geometry)
        require(record == expected.get(stable), 'Land source does not match published selection metadata')
        require(geometry.is_valid and geometry.geom_type in ('Polygon', 'MultiPolygon') and not geometry.is_empty, 'Invalid source land polygon')
        require(record['source_id'] in ('sgis', 'osm'), 'Unexpected land source')
        if record['source_id'] == 'sgis':
            require(record['version'] == '2025-06-30' and record['properties']['BASE_DATE'] == '20250630', 'SGIS reference date differs')
        result.append(LandSource(stable, geometry, record, json.loads(raw_render)))
        evidence.append({'stable_id': stable, 'source_record_id': record['source_record_id'],
            'source_id': record['source_id'], 'source_geometry_sha256': record['geometry_sha256'],
            'projected_geometry_wkb_sha256': sha(raw_geometry), 'record_sha256': sha(encoded(record)),
            'source_vertices': int(shapely.get_num_coordinates(geometry))})
    db.close()
    require(len(result) == len(expected) == 103 and Counter(s.record['source_id'] for s in result) == {'sgis': 17, 'osm': 86}, 'Expected 17 SGIS and 86 preserved Dokdo records')
    require(len({s.stable[:16] for s in result}) == len(result), 'Display identity collision')
    return result, {'work_fingerprint': fingerprint, 'source_archive_sha256': inputs['admin_sha256'], 'records': evidence}


def polygon_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    if hasattr(geometry, 'geoms'):
        return [p for g in geometry.geoms for p in polygon_parts(g)]
    return []


def quantized_fill(clipped, bounds):
    """Round once to the actual integer display grid and preserve valid fill.

    GEOS valid_output can remove sub-grid spikes/holes and split narrow necks.
    This is declared display quantization; original polygons/records stay intact.
    A collapsed whole source fragment keeps an explicit selectable point.
    """
    require(clipped.is_valid and clipped.area > 0, 'Expected valid clipped land')
    unit = (bounds[2] - bounds[0]) / EXTENT
    origin = np.asarray(bounds[:2])
    scaled = shapely.transform(clipped, lambda coordinates: (coordinates - origin) / unit)
    snapped = shapely.set_precision(scaled, 1, mode='valid_output')
    if snapped.is_empty:
        result = shapely.transform(scaled.representative_point(), lambda coordinates: np.rint(coordinates))
        representation = 'subpixel_anchor'
    else:
        # A zero-tolerance pass removes redundant collinear integer vertices;
        # it introduces no further displacement or simplification tolerance.
        result = snapped.simplify(0, preserve_topology=True)
        require(result.equals(snapped), 'Zero-tolerance pass changed filled geometry')
        representation = 'quantized_valid_fill'
    coordinates = shapely.get_coordinates(result)
    require(result.is_valid and np.isfinite(coordinates).all() and
            np.equal(coordinates, np.rint(coordinates)).all(), 'Invalid integer land geometry')
    require(coordinates.min() >= 0 and coordinates.max() <= EXTENT, 'Land escaped tile boundary')
    return result, representation, {
        'source_polygon_components': len(polygon_parts(clipped)),
        'display_polygon_components': len(polygon_parts(result)),
        'source_holes': sum(len(g.interiors) for g in polygon_parts(clipped)),
        'display_holes': sum(len(g.interiors) for g in polygon_parts(result)),
        'source_area_m2': clipped.area, 'display_area_m2': result.area * unit * unit,
        'absolute_area_difference_m2': abs(clipped.area - result.area * unit * unit),
    }


def encode_land_tile(rows, z, x, y):
    bounds = tile_bounds(z, x, y); features = []; ids = set(); statistics = Counter(); shapes = []
    for source, clipped in rows:
        geometry, representation, info = quantized_fill(clipped, bounds)
        features.append({'geometry': geometry, 'properties': {**source.render,
            'stable_id': source.stable[:16], 'representation': representation}})
        ids.add(source.stable); shapes.append((source.stable[:16], geometry)); statistics[representation] += 1
        for key, value in info.items():
            statistics[key] += value
    if not features:
        return None, {}, set()
    raw = mapbox_vector_tile.encode({'name': 'land', 'features': features}, default_options={
        'extents': EXTENT, 'on_invalid_geometry': mapbox_vector_tile.encoder.on_invalid_geometry_raise})
    require(len(raw) <= HARD, f'Decoded land tile exceeds 1 MiB: {z}/{x}/{y}: {len(raw)}')
    decoded = mapbox_vector_tile.decode(raw)['land']['features']
    require({f['properties']['stable_id'] for f in decoded} == {s[:16] for s in ids}, 'Land encoder omitted a source ID')
    decoded_shapes = {f['properties']['stable_id']: shape(f['geometry']) for f in decoded}
    require(len(decoded_shapes) == len(shapes), 'Duplicate source fragment in a tile')
    for stable, geometry in shapes:
        rendered = decoded_shapes[stable]
        require(rendered.is_valid and rendered.equals(geometry), 'MVT changed filled polygon or holes')
    body = gzip.compress(raw, mtime=0)
    require(len(body) + 512 <= HARD, 'Land tile exceeds archive hard limit')
    statistics['decoded_bytes'] = len(raw)
    return body, dict(statistics), ids


class LandIndex:
    def __init__(self, sources, fragments=None):
        self.sources = sources; self.parents = []; self.polygons = []
        if fragments is None:
            fragments = [(i, p) for i, s in enumerate(sources) for p in polygon_parts(s.geometry)]
        for i, polygon in fragments:
            self.parents.append(i); self.polygons.append(polygon)
        self.tree = shapely.STRtree(self.polygons)
        shapely.prepare(self.polygons)

    def query(self, bounds):
        extent = box(*bounds); groups = defaultdict(list)
        for i in self.tree.query(extent, predicate='intersects'):
            polygon = self.polygons[i]
            clipped = extent if polygon.covers(extent) else polygon.intersection(extent)
            groups[self.parents[i]].extend(polygon_parts(clipped))
        result = []
        for parent, polygons in groups.items():
            clipped = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
            require(clipped.is_valid, 'Clipping made invalid source polygons')
            if clipped.area > 0:
                result.append((self.sources[parent], clipped))
        return sorted(result, key=lambda row: row[0].stable)

    def tile_coordinates(self, z):
        result = set()
        for polygon in self.polygons:
            result.update(bbox_tiles(polygon.bounds, z))
        return sorted(result, key=lambda xy: zxy_to_tileid(z, *xy))


def build_tiles(sources, work, fingerprint, maxzoom=MAXZOOM):
    work = Path(work); work.mkdir(parents=True, exist_ok=True); reserve(work, 256 * 1024**2)
    db = sqlite3.connect(work / 'land.sqlite')
    db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS records(topic TEXT NOT NULL,stable TEXT PRIMARY KEY,record BLOB NOT NULL);
      CREATE TABLE IF NOT EXISTS tiles(tileid INTEGER PRIMARY KEY,body BLOB NOT NULL);
      CREATE TABLE IF NOT EXISTS stages(zoom INTEGER PRIMARY KEY,value TEXT NOT NULL);''')
    previous = db.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()
    require(previous is None or previous == (fingerprint,), 'Land checkpoint belongs to other inputs')
    db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('fingerprint', fingerprint))
    for source in sources:
        db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?)', ('land', source.stable, zlib.compress(encoded(source.record), 1)))
    db.commit(); index = LandIndex(sources); audits = {}
    for z in range(MINZOOM, maxzoom + 1):
        previous = db.execute('SELECT value FROM stages WHERE zoom=?', (z,)).fetchone()
        if previous:
            audits[str(z)] = json.loads(previous[0]); continue
        started = time.perf_counter(); present = set(); statistics = Counter(); count = 0; maxraw = 0; maxcompressed = 0
        for ordinal, (x, y) in enumerate(index.tile_coordinates(z)):
            if ordinal % 128 == 0:
                reserve(work, 128 * 1024**2)
            rows = index.query(tile_bounds(z, x, y))
            body, info, ids = encode_land_tile(rows, z, x, y)
            if body is None:
                continue
            tileid = zxy_to_tileid(z, x, y)
            existing = db.execute('SELECT body FROM tiles WHERE tileid=?', (tileid,)).fetchone()
            require(existing is None or existing[0] == body, 'Land resume tile differs')
            db.execute('INSERT OR IGNORE INTO tiles VALUES(?,?)', (tileid, body))
            count += 1; present.update(ids); maxraw = max(maxraw, info.pop('decoded_bytes')); maxcompressed = max(maxcompressed, len(body))
            statistics.update(info)
            if ordinal % 128 == 0:
                db.commit()
        require(present == {s.stable for s in sources}, 'Land zoom omitted a source identity')
        audit = {'tile_count': count, 'represented_feature_count': len(present), 'maximum_decoded_tile_bytes': maxraw,
            'maximum_compressed_tile_bytes': maxcompressed, 'representations': dict(statistics),
            'all_source_ids_represented': True, 'polygon_roundtrip_validated': True,
            'grid_unit_web_mercator_m': (tile_bounds(z, 0, 0)[2] - tile_bounds(z, 0, 0)[0]) / EXTENT,
            'elapsed_seconds': round(time.perf_counter() - started, 3)}
        db.execute('INSERT INTO stages VALUES(?,?)', (z, json.dumps(audit))); db.commit(); audits[str(z)] = audit
        immutable(work / f'zoom-{z}.json', audit)
        print(json.dumps({'stage': 'sgis-land', 'zoom': z, 'tile_count': count, 'max_decoded': maxraw,
            'max_gzip': maxcompressed, 'elapsed_seconds': audit['elapsed_seconds']}), flush=True)
    return db, audits


def reuse_topics(baseline, old_catalog, output, release):
    topics = []; reused = []
    old_prefix = '/data/map-tiles/' + old_catalog['release_id'] + '/'
    new_prefix = '/data/map-tiles/' + release + '/'
    for original in old_catalog['topics']:
        if original['id'] == 'land':
            continue
        topic = deepcopy(original)
        for ref in topic['chunks'] + topic['details']:
            old_url = ref['url']; require(old_url.startswith(old_prefix), 'External reuse reference')
            ref['url'] = new_prefix + old_url[len(old_prefix):]
            source = local_path(baseline, old_url.lstrip('/')); target = local_path(output, ref['url'].lstrip('/'))
            require(digest(source) == ref['sha256'] and source.stat().st_size == ref['byte_length'], 'Reused source changed')
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                os.link(source, target)
            require(digest(target) == ref['sha256'] and target.stat().st_size == ref['byte_length'], 'Reused target differs')
            reused.append({'original_url': old_url, 'new_url': ref['url'], 'sha256': ref['sha256'], 'byte_length': ref['byte_length']})
        topics.append(topic)
    return topics, reused


def source_registry(source_ids):
    registry = {
        'sgis': {'id': 'sgis', 'title': 'SGIS 행정구역 통계경계', 'url': 'https://www.data.go.kr/data/15129688/fileData.do',
            'license': '이용허락범위 제한 없음', 'description': '2025-06-30 통계 행정경계 기반의 육지 표시입니다. 공식 해안측량이나 법정동·지적 경계가 아닙니다.'},
        'osm': {'id': 'osm', 'title': 'OpenStreetMap contributors', 'url': 'https://www.openstreetmap.org/copyright',
            'license': 'ODbL-1.0', 'description': '보존된 도로·철도·수역 및 독도 해안 원본. 개별 선택 정보의 원천 버전과 ID를 유지합니다.'},
    }
    require(set(source_ids) <= registry.keys(), 'Missing actual source attribution')
    return [registry[s] for s in sorted(source_ids)]


def sample_proof(db, sources, zoom):
    locations = {'busan-north-port': (129.05, 35.12), 'dokdo': (131.867, 37.242),
        'ulleung': (130.88, 37.49), 'jeju': (126.53, 33.50)}
    evidence = {}
    for name, (lon, lat) in locations.items():
        x = int((lon + 180) / 360 * 2**zoom)
        y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2**zoom)
        body = db.execute('SELECT body FROM tiles WHERE tileid=?', (zxy_to_tileid(zoom, x, y),)).fetchone()
        require(body is not None, 'Required island/city tile missing')
        decoded = mapbox_vector_tile.decode(gzip.decompress(body[0]))['land']['features']
        polygons = [shape(f['geometry']) for f in decoded if f['geometry']['type'] in ('Polygon', 'MultiPolygon')]
        require(polygons and all(g.is_valid and g.area > 0 for g in polygons), 'Required land is not filled')
        bounds = tile_bounds(zoom, x, y); unit = (bounds[2] - bounds[0]) / EXTENT; origin = np.asarray(bounds[:2])
        rendered = shapely.union_all([shapely.transform(g, lambda c: c * unit + origin) for g in polygons])
        originals = [s.geometry.intersection(box(*bounds)) for s in sources if s.geometry.intersects(box(*bounds))]
        original = shapely.union_all(originals)
        difference = original.symmetric_difference(rendered)
        # Every changed fill region must lie close to an original boundary;
        # test this on city/island samples rather than claiming a nationwide
        # continuous coastline accuracy guarantee from integer vertex rounding.
        outside_band = difference.difference(original.boundary.buffer(unit * 2)).area
        require(outside_band <= 1e-3, 'Quantization changed fill beyond the two-grid sample band')
        evidence[name] = {'tile': [zoom, x, y], 'polygon_features': len(polygons), 'gzip_bytes': len(body[0]),
            'filled_area_web_mercator_m2': rendered.area, 'symmetric_difference_web_mercator_m2': difference.area,
            'sample_boundary_band_web_mercator_m': unit * 2, 'outside_band_area_m2': outside_band,
            'geometry_valid': True, 'filled_polygon': True}
    return evidence


def build(baseline, source_work, output, maxzoom=MAXZOOM):
    baseline = Path(baseline).resolve(); source_work = Path(source_work).resolve(); output = Path(output).resolve()
    require(not output.exists(), 'Land candidate output must be a new directory')
    require(MINZOOM <= maxzoom <= MAXZOOM, 'Unsupported land zoom')
    reserve(output, 512 * 1024**2)
    old_publication, old_catalog = load_baseline(baseline)
    sources, source_evidence = load_sources(source_work, baseline, old_catalog)
    identity = {'version': VERSION, 'baseline_catalog_sha256': old_publication['map_catalog']['sha256'],
        'baseline_publication_sha256': digest(baseline / 'publication.json'), 'source_evidence': source_evidence,
        'transform_sha256': digest(Path(__file__)), 'map_tiles_transform_sha256': digest(Path(__file__).with_name('map_tiles.py')),
        'minzoom': MINZOOM, 'maxzoom': maxzoom, 'extent': EXTENT, 'precision_mode': 'GEOS valid_output; zero tolerance collinear cleanup'}
    fingerprint = sha(encoded(identity)); release = 'map2d-' + fingerprint[:20]
    output.mkdir(parents=True); immutable(output / 'inputs.json', identity)
    work = output / 'work'; db, audits = build_tiles(sources, work, fingerprint, maxzoom)
    samples = sample_proof(db, sources, maxzoom)
    base = output / 'data/map-tiles' / release; prefix = '/data/map-tiles/' + release
    low = min(s.geometry.bounds[0] for s in sources); south = min(s.geometry.bounds[1] for s in sources)
    high = max(s.geometry.bounds[2] for s in sources); north = max(s.geometry.bounds[3] for s in sources)
    lo, la = TO_GEO.transform(low, south); hi, ha = TO_GEO.transform(high, north); bounds = [lo, la, hi, ha]
    chunks = pack_archives(db.execute('SELECT tileid,body FROM tiles ORDER BY tileid'), 'land', bounds, base / 'land/tiles', prefix + '/land/tiles')
    details = pack_details(db, 'land', base / 'land/details', prefix + '/land/details'); db.close()
    topics, reused = reuse_topics(baseline, old_catalog, output, release)
    topics.append({'id': 'land', 'source_layer': 'land', 'minzoom': MINZOOM, 'maxzoom': maxzoom,
        'feature_count': len(sources), 'display_id_hex_length': 16, 'bounds': bounds, 'chunks': chunks, 'details': details,
        'description': '2025-06-30 SGIS 통계경계 기반 육지 표시 + 기존 OSM 독도 해안. 공식 해안측량이 아닙니다.',
        'geometry_precision': '원본 보존; MVT 8192 정수 격자 valid fill. 격자보다 좁은 부분은 병합/축소될 수 있으며 overzoom 시 화면 오차가 커집니다.'})
    old_order = {topic['id']: i for i, topic in enumerate(old_catalog['topics'])}; topics.sort(key=lambda t: old_order[t['id']])
    used_sources = {s.record['source_id'] for s in sources}
    for topic in topics:
        for ref in topic['details']:
            records = json.loads(gzip.decompress(local_path(output, ref['url'].lstrip('/')).read_bytes()))['records']
            used_sources.update(r['source_id'] for r in records)
    catalog = {**deepcopy(old_catalog), 'release_id': release, 'topics': topics,
        'sources': source_registry(used_sources), 'reference_dates': {'sgis': '2025-06-30', 'osm_dokdo_snapshot': '2026-09-16'},
        'attribution': '© OpenStreetMap contributors · SGIS 국가데이터처',
        'land_display': {'basis': 'census_administrative_polygons', 'reference_date': '2025-06-30',
            'surveyed_coastline': False, 'preserved_dokdo_osm_records': 86,
            'supersedes_land_catalog_sha256': old_publication['map_catalog']['sha256']}}
    ref = immutable(base / 'catalog.json', catalog)
    files = [{'path': p.relative_to(output).as_posix(), 'sha256': digest(p), 'byte_length': p.stat().st_size}
        for p in sorted(base.rglob('*')) if p.is_file()]
    require(len(files) < 3870 and all(f['byte_length'] <= HARD for f in files), 'Map file count/size budget exceeded')
    entry = {'path': (base / 'catalog.json').relative_to(output).as_posix(), 'sha256': ref['sha256'], 'release_id': release}
    report = {'schema_version': 1, 'status': 'validated', 'version': VERSION, 'profile': 'national-sgis-land-existing-basemap',
        'map_catalog': entry, 'files': files, 'file_count': len(files), 'bytes': sum(f['byte_length'] for f in files),
        'sources': identity, 'topics': {**old_publication['topics'], 'land': audits},
        'limits': {'archive_hard_bytes': HARD, 'low_zoom_target_bytes': LOW_TARGET, 'detail_target_bytes': DETAIL_TARGET},
        'reused_files': reused, 'reused_file_count': len(reused), 'reused_bytes': sum(f['byte_length'] for f in reused),
        'sample_fill_validation': samples, 'land_original_record_count': len(sources),
        'land_original_properties_preserved': True, 'land_original_geometry_unchanged': True,
        'limitations': ['SGIS 2025-06-30 census polygons are used as a land display, not an official shoreline survey.',
            'MVT integer precision can merge/remove sub-grid polygon parts; original polygons and source IDs remain preserved.',
            'Land maxzoom overzoom increases display error; no address, parcel or legal-dong location is inferred.',
            'SGIS and the preserved OSM Dokdo polygons can overlap and have different reference dates.',
            'Other themes remain the existing generalized basemap; this does not add all buildings or detailed roads.',
            'Offline hash, polygon and selection validation is not HTTP or GPU rendering validation.']}
    immutable(output / 'publication.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--source-work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); result = build(args.baseline, args.source_work, args.output)
    print(json.dumps({key: result[key] for key in ('status', 'map_catalog', 'file_count', 'bytes', 'reused_file_count')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
