"""Add bounded z3/4 overviews from a verified, single z5 national MVT tile.

This inherits the donor's display precision; it never rereads or changes source
geometry, high-zoom archives, selection records, or public catalog pointers.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import json
import os
from pathlib import Path

import mapbox_vector_tile
import numpy as np
from pmtiles.reader import MemorySource, Reader
from pmtiles.tile import tileid_to_zxy, zxy_to_tileid
from pyproj import Transformer
import shapely
from shapely.geometry import MultiLineString, Point, box, shape

from .admin_boundaries import encoded, immutable, require
from .core import digest
from .map_tiles import EXTENT, HARD, LOW_TARGET, DETAIL_TARGET, bbox_tiles, pack_archives, reserve, sha, tile_bounds
from .map_tiles_land import load_baseline, local_path, quantized_fill

VERSION = 'national-overview-from-z5-1'
DONOR = (5, 27, 12)
ZOOMS = (3, 4)
TOPICS = ('land', 'admin-sido')


def donor_features(baseline, topic):
    """Require the national donor to be interior to one tile: no seam merging."""
    require(topic['minzoom'] == 5 and topic['id'] in TOPICS, 'Unexpected donor topic or zoom')
    tid = zxy_to_tileid(*DONOR)
    matches = [r for r in topic['chunks'] if r['first_tile_id'] <= tid <= r['last_tile_id']]
    require(len(matches) == 1, 'Missing or overlapping donor archive')
    ref = matches[0]
    data = local_path(baseline, ref['url'].lstrip('/')).read_bytes()
    require(len(data) == ref['byte_length'] and sha(data) == ref['sha256'], 'Donor archive hash differs')
    body = Reader(MemorySource(data)).get(*DONOR)
    require(body is not None, 'Missing national donor tile')
    raw = gzip.decompress(body)
    require(len(raw) <= HARD, 'Donor decoded tile exceeds budget')
    layer = mapbox_vector_tile.decode(raw)[topic['source_layer']]
    require(layer['extent'] == EXTENT, 'Unexpected donor integer extent')
    b = tile_bounds(*DONOR); unit = (b[2] - b[0]) / EXTENT; origin = np.asarray(b[:2])
    features = []; points = Counter(); grouped = defaultdict(list)
    exact_duplicates = 0; signatures = set(); vertices = 0
    for feature in layer['features']:
        props = feature['properties']; sid = props['stable_id']; geometry = shape(feature['geometry'])
        coordinates = shapely.get_coordinates(geometry)
        require(len(sid) == 16 and all(c in '0123456789abcdef' for c in sid), 'Invalid donor display ID')
        require(geometry.is_valid and not geometry.is_empty and np.isfinite(coordinates).all(), 'Invalid donor geometry')
        # No buffer, source clipping edge, or missing neighboring donor is allowed.
        require(coordinates.min() > 0 and coordinates.max() < EXTENT, 'Donor touches a tile seam or buffer')
        signature = sha(encoded(props) + geometry.wkb)
        if signature in signatures:
            exact_duplicates += 1
            continue
        signatures.add(signature); vertices += len(coordinates)
        projected = shapely.transform(geometry, lambda c: c * unit + origin)
        if topic['id'] == 'admin-sido' and geometry.geom_type != 'Point':
            require(geometry.geom_type in ('LineString', 'MultiLineString'), 'Unexpected administrative geometry')
            grouped[encoded(props)].extend(list(projected.geoms) if hasattr(projected, 'geoms') else [projected])
        else:
            features.append((projected, deepcopy(props)))
            if geometry.geom_type == 'Point':
                points[sid] += 1
    for key, parts in sorted(grouped.items()):
        features.append((MultiLineString(parts), json.loads(key)))
    ids = {p['stable_id'] for _, p in features}
    require(len(ids) == topic['feature_count'], 'Donor does not contain every source ID')
    selection_ids = []
    for detail in topic['details']:
        raw_detail = local_path(baseline, detail['url'].lstrip('/')).read_bytes()
        require(len(raw_detail) == detail['byte_length'] and sha(raw_detail) == detail['sha256'], 'Selection shard hash differs')
        selection_ids.extend(r['stable_id'] for r in json.loads(gzip.decompress(raw_detail))['records'])
    require(len(selection_ids) == len(ids) and {s[:16] for s in selection_ids} == ids,
            'Donor IDs differ from full original selection records')
    if topic['id'] == 'admin-sido':
        require(set(points) == ids and all(n == 1 for n in points.values()), 'Duplicate or missing administrative label')
    else:
        require(len(features) == len(ids), 'Duplicate land ID in donor')
    return features, {'archive_sha256': ref['sha256'], 'tile_gzip_sha256': sha(body),
        'tile': list(DONOR), 'decoded_bytes': len(raw), 'input_feature_count': len(layer['features']),
        'grouped_feature_count': len(features), 'original_ids': len(ids), 'input_vertices': vertices,
        'exact_duplicate_fragments_removed': exact_duplicates, 'donor_tiles': 1,
        'full_selection_ids_sha256': sha(encoded(sorted(selection_ids))),
        'all_geometry_strictly_inside_donor': True, 'source_clip_seams': 0,
        'label_points': sum(points.values()) if topic['id'] == 'admin-sido' else None}


def quantized_overview(geometry, bounds):
    unit = (bounds[2] - bounds[0]) / EXTENT; origin = np.asarray(bounds[:2])
    if geometry.geom_type in ('Polygon', 'MultiPolygon'):
        display, notice, _ = quantized_fill(geometry, bounds)
    else:
        scaled = shapely.transform(geometry, lambda c: (c - origin) / unit)
        if geometry.geom_type == 'Point':
            display = shapely.transform(scaled, lambda c: np.rint(c)); notice = 'overview_point'
        else:
            # Preserve the existing one-label-per-province Points separately.
            # A collapsed short line must not create another label Point.
            display = shapely.set_precision(scaled, 1, mode='valid_output')
            notice = 'overview_line' if not display.is_empty else 'subpixel_line_collapsed'
    if display.is_empty:
        return display, notice
    coordinates = shapely.get_coordinates(display)
    require(display.is_valid and np.isfinite(coordinates).all() and
            np.equal(coordinates, np.rint(coordinates)).all() and
            coordinates.min() >= 0 and coordinates.max() <= EXTENT, 'Invalid overview integer geometry')
    return display, notice


def encode_overview(topic, features, z, x, y):
    bounds = tile_bounds(z, x, y); clip = box(*bounds); output = []; expected = []
    notices = Counter(); ids = set(); labels = Counter(); displacement = 0.0
    for geometry, props in features:
        clipped = geometry.intersection(clip)
        if clipped.is_empty:
            continue
        result, notice = quantized_overview(clipped, bounds); notices[notice] += 1
        if result.is_empty:
            continue
        p = {**props, 'representation': notice, 'display_donor_zoom': DONOR[0]}
        output.append({'geometry': result, 'properties': p}); expected.append((result, p))
        ids.add(p['stable_id'])
        if topic == 'admin-sido' and result.geom_type == 'Point':
            labels[p['stable_id']] += 1
        unit = (bounds[2] - bounds[0]) / EXTENT; origin = np.asarray(bounds[:2])
        coordinates = (shapely.get_coordinates(clipped) - origin) / unit
        rounded = np.rint(coordinates) if clipped.geom_type == 'Point' else np.floor(coordinates + 0.5)
        displacement = max(displacement, float(np.linalg.norm(rounded - coordinates, axis=1).max()) * unit)
    require(output, 'Empty overview tile')
    raw = mapbox_vector_tile.encode({'name': topic, 'features': output}, default_options={
        'extents': EXTENT, 'on_invalid_geometry': mapbox_vector_tile.encoder.on_invalid_geometry_raise})
    require(len(raw) <= HARD, 'Decoded overview tile exceeds 1 MiB')
    decoded = mapbox_vector_tile.decode(raw)[topic]['features']
    require(len(decoded) == len(expected), 'Encoder omitted overview feature')
    for feature, (geometry, props) in zip(decoded, expected):
        require(feature['properties'] == props and shape(feature['geometry']).equals(geometry), 'Overview roundtrip changed geometry or properties')
    body = gzip.compress(raw, mtime=0)
    require(len(body) + 512 <= HARD, 'Overview cannot fit 1 MiB archive')
    return body, {'tile': [z, x, y], 'decoded_bytes': len(raw), 'gzip_bytes': len(body),
        'feature_count': len(output), 'id_count': len(ids), 'representation_counts': dict(notices),
        'label_count': sum(labels.values()), 'unique_label_count': len(labels),
        'max_input_vertex_snap_from_z5_web_mercator_m': displacement,
        'input_vertex_snap_is_continuous_curve_bound': False,
        'quantization_grid_web_mercator_m': unit, 'all_geometry_roundtrip_valid': True}, ids


def build_topic(topic, features):
    bounds = shapely.union_all([box(*g.bounds) for g, _ in features]).bounds
    expected = {p['stable_id'] for _, p in features}; tiles = []; audits = {}
    for zoom in ZOOMS:
        ids = set(); reports = []
        for x, y in bbox_tiles(bounds, zoom):
            body, report, current = encode_overview(topic, features, zoom, x, y)
            tiles.append((zxy_to_tileid(zoom, x, y), body)); ids.update(current); reports.append(report)
        require(ids == expected, 'Overview omitted an original display ID')
        if topic == 'admin-sido':
            require(sum(r['label_count'] for r in reports) == len(expected), 'Overview duplicated or omitted label')
        audits[str(zoom)] = {'tiles': reports, 'all_source_ids_represented': True, 'source_id_count': len(ids)}
    return sorted(tiles), audits


def reuse_all(baseline, catalog, output, release):
    topics = deepcopy(catalog['topics']); reused = []
    old_prefix = '/data/map-tiles/' + catalog['release_id'] + '/'
    new_prefix = '/data/map-tiles/' + release + '/'
    for topic in topics:
        for ref in topic['chunks'] + topic['details']:
            old_url = ref['url']; require(old_url.startswith(old_prefix), 'External reuse URL')
            ref['url'] = new_prefix + old_url[len(old_prefix):]
            source = local_path(baseline, old_url.lstrip('/')); target = local_path(output, ref['url'].lstrip('/'))
            require(not target.exists(), 'Reuse target already exists')
            target.parent.mkdir(parents=True, exist_ok=True); os.link(source, target)
            require(target.stat().st_size == ref['byte_length'] and digest(target) == ref['sha256'], 'Reused bytes changed')
            reused.append({'original_url': old_url, 'new_url': ref['url'], 'sha256': ref['sha256'], 'byte_length': ref['byte_length']})
    return topics, reused


def island_proof(land_tiles, features):
    """Presence and representation checks; subpixel anchors are not coastline fill."""
    records = {p['stable_id']: (g, p) for g, p in features}
    cases = {'mainland-seoul': (126.978, 37.566), 'jeju': (126.53, 33.38),
        'ulleung': (130.88, 37.49), 'dokdo': (131.867, 37.242)}
    reports = {}
    forward = Transformer.from_crs(4326, 3857, always_xy=True)
    for zoom in ZOOMS:
        decoded = []
        for tid, body in land_tiles:
            z, x, y = tileid_to_zxy(tid)
            if z != zoom:
                continue
            b = tile_bounds(z, x, y); unit = (b[2] - b[0]) / EXTENT; origin = np.asarray(b[:2])
            for f in mapbox_vector_tile.decode(gzip.decompress(body))['land']['features']:
                g = shapely.transform(shape(f['geometry']), lambda c: c * unit + origin)
                decoded.append((g, f['properties']))
        current = {}
        for name, lonlat in cases.items():
            point = Point(*forward.transform(*lonlat))
            # Choose source display closest to a fixed point, not an invented address.
            sid = min(records, key=lambda s: records[s][0].distance(point))
            if name == 'dokdo':
                candidates = [s for s in records if records[s][1]['source_id'] == 'osm']
                sid = min(candidates, key=lambda s: records[s][0].distance(point))
            matches = [(g, p) for g, p in decoded if p['stable_id'] == sid]
            require(matches, 'Required island/mainland ID missing')
            distance = min(g.distance(point) for g, _ in matches)
            require(distance <= records[sid][0].distance(point) + unit * 2, 'Required island displaced outside overview grid band')
            current[name] = {'stable_id': sid, 'source_id': records[sid][1]['source_id'],
                'geometry_types': sorted({g.geom_type for g, _ in matches}),
                'distance_to_check_point_web_mercator_m': distance, 'id_preserved': True}
        reports[str(zoom)] = current
    return reports


def build(baseline, output):
    baseline = Path(baseline).resolve(); output = Path(output).resolve()
    require(not output.exists(), 'Overview output must be a new directory')
    reserve(output, 16 * 1024**2)
    old_publication, old_catalog = load_baseline(baseline)
    donors = {}; donor_audits = {}; new_tiles = {}; new_audits = {}
    for topic in old_catalog['topics']:
        if topic['id'] in TOPICS:
            donors[topic['id']], donor_audits[topic['id']] = donor_features(baseline, topic)
            new_tiles[topic['id']], new_audits[topic['id']] = build_topic(topic['id'], donors[topic['id']])
    require(set(donors) == set(TOPICS), 'Required overview topics missing')
    identity = {'version': VERSION, 'baseline_catalog_sha256': old_publication['map_catalog']['sha256'],
        'baseline_publication_sha256': digest(baseline / 'publication.json'), 'transform_sha256': digest(Path(__file__)),
        'map_tiles_sha256': digest(Path(__file__).with_name('map_tiles.py')),
        'valid_fill_sha256': digest(Path(__file__).with_name('map_tiles_land.py')),
        'donor': list(DONOR), 'new_zooms': list(ZOOMS), 'extent': EXTENT, 'donors': donor_audits,
        'tile_hashes': {topic: [[tid, sha(body)] for tid, body in rows] for topic, rows in sorted(new_tiles.items())}}
    release = 'map2d-' + sha(encoded(identity))[:20]
    output.mkdir(parents=True); immutable(output / 'inputs.json', identity)
    topics, reused = reuse_all(baseline, old_catalog, output, release)
    base = output / 'data/map-tiles' / release; prefix = '/data/map-tiles/' + release
    for topic in topics:
        if topic['id'] not in TOPICS:
            continue
        chunks = pack_archives(new_tiles[topic['id']], topic['id'], topic['bounds'],
            base / topic['id'] / 'tiles', prefix + '/' + topic['id'] + '/tiles')
        require(chunks[-1]['last_tile_id'] < topic['chunks'][0]['first_tile_id'], 'Overview overlaps original tile interval')
        topic['chunks'] = chunks + topic['chunks']; topic['minzoom'] = min(ZOOMS)
        topic['geometry_precision'] += ' z3–4는 검증된 z5 표시 형상의 재양자화이며 원본 정밀도 향상을 뜻하지 않습니다.'
    catalog = {**deepcopy(old_catalog), 'release_id': release, 'topics': topics,
        'overview_display': {'donor_zoom': 5, 'minzoom': 3, 'topics': list(TOPICS),
            'basis': 'verified_z5_display_requantization', 'original_selection_shards_unchanged': True}}
    ref = immutable(base / 'catalog.json', catalog)
    files = [{'path': p.relative_to(output).as_posix(), 'sha256': digest(p), 'byte_length': p.stat().st_size}
        for p in sorted(base.rglob('*')) if p.is_file()]
    require(len(files) < 3870 and all(f['byte_length'] <= HARD for f in files), 'Overview file count/size budget exceeded')
    entry = {'path': (base / 'catalog.json').relative_to(output).as_posix(), 'sha256': ref['sha256'], 'release_id': release}
    report = {**deepcopy(old_publication), 'version': VERSION, 'profile': 'national-sgis-basemap-z3-overview',
        'map_catalog': entry, 'files': files, 'file_count': len(files), 'bytes': sum(f['byte_length'] for f in files),
        'sources': identity, 'reused_files': reused, 'reused_file_count': len(reused),
        'reused_bytes': sum(f['byte_length'] for f in reused), 'overview_audits': new_audits,
        'overview_island_validation': island_proof(new_tiles['land'], donors['land']),
        'baseline_audit_inherited_from': old_publication['map_catalog'],
        'limits': {'archive_hard_bytes': HARD, 'low_zoom_target_bytes': LOW_TARGET, 'detail_target_bytes': DETAIL_TARGET},
        'limitations': old_publication['limitations'] + [
            'Only z3/4 land and province boundaries are new; all original z5+ archives and full selection metadata are byte-identical.',
            'Low zoom inherits z5 display precision. Tiny islands remain selectable source IDs/anchors and are not claimed to be filled polygons.',
            'One interior donor tile per topic means no source-tile clip or buffer seams are merged.',
            'Measured vertex snap is between display grids, not survey accuracy or a continuous source-curve guarantee.']}
    immutable(output / 'publication.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); report = build(args.baseline, args.output)
    print(json.dumps({k: report[k] for k in ('status', 'map_catalog', 'file_count', 'bytes', 'reused_file_count')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
