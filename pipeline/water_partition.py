"""Build a private, lossless spatial water candidate; never publish or deploy.

Whole source features belong to one center-point cell. Asset bounds enclose
every vertex, including geometry crossing cell borders. Below 60 km use the
partitioned representation; at/above 60 km keep the exact original asset.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import copy
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil

from .core import LOCAL, PUBLIC, ROOT, digest
from .retile import compact_features, restore_features, vertex_count

VERSION = 'whole-feature-water-partition-1'
SOURCE_ID = 'osm-water-korea'
DEFAULT_INPUT = LOCAL / 'national-upgrade-20260919/catalog-streamed.json'
DEFAULT_OUTPUT = LOCAL / 'water-partition-20260920'
GRID_DEGREES = .25
VERTEX_TARGET = 12288
SWITCH_HEIGHT = 60000
DISK_RESERVE = 30 * 1024**3
STORAGE_LIMIT = 64 * 1024**2
MEMORY_LIMIT = 512 * 1024**2
HASH = re.compile(r'^[a-f0-9]{64}$')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def memory_bytes():
    if os.name != 'nt':
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value if __import__('sys').platform == 'darwin' else value * 1024
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ('peak', 'rss', 'peak_pool', 'pool', 'peak_nonpool', 'nonpool', 'pagefile', 'peak_pagefile')]
    counters = Counters(); counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True); kernel.GetCurrentProcess.restype = ctypes.c_void_p
    api = ctypes.WinDLL('psapi', use_last_error=True)
    api.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    require(api.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb), 'Cannot inspect process memory')
    return counters.peak


def no_links(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        require(not item.is_symlink(), 'Symlink path is not permitted')


def source_path(url, public):
    require(isinstance(url, str) and url.startswith('/data/') and re.fullmatch(r'/data/[A-Za-z0-9_/.-]+', url), 'Invalid source URL')
    require(all(part and not part.startswith('.') for part in url[6:].split('/')), 'Ambiguous source URL')
    path = public / url[6:]
    no_links(path); require(path.resolve().is_relative_to(public.resolve()), 'Source URL escaped public data')
    return path


def bounded_read(path, maximum=16 * 1024**2):
    no_links(path); require(path.is_file() and path.stat().st_size <= maximum, 'Missing or oversized input')
    data = path.read_bytes(); require(len(data) <= maximum, 'Input grew beyond budget')
    return data


def geometry_positions(geometry):
    require(isinstance(geometry, dict) and geometry.get('type') in ('Polygon', 'MultiPolygon'), 'Expected complete Polygon/MultiPolygon')
    polygons = [geometry.get('coordinates')] if geometry['type'] == 'Polygon' else geometry.get('coordinates')
    require(isinstance(polygons, list) and polygons, 'Empty polygon geometry')
    for polygon in polygons:
        require(isinstance(polygon, list) and polygon, 'Missing polygon rings')
        for ring in polygon:
            require(isinstance(ring, list) and len(ring) >= 4, 'Incomplete polygon ring')
            for point in ring:
                require(isinstance(point, list) and len(point) >= 2 and all(type(v) in (int, float) and math.isfinite(v) for v in point), 'Invalid coordinate')
                require(abs(point[0]) <= 180 and abs(point[1]) <= 90, 'Coordinate outside longitude/latitude domain')
                yield point
            require(ring[0] == ring[-1], 'Open source ring must be reviewed, not repaired silently')


def feature_rows(features):
    require(isinstance(features, list) and 0 < len(features) <= 100000, 'Feature count outside bounded range')
    rows, ids = [], set()
    for ordinal, feature in enumerate(features):
        require(isinstance(feature, dict) and feature.get('type') == 'Feature' and isinstance(feature.get('properties'), dict), 'Invalid source feature')
        identity = feature.get('id', feature['properties'].get('source_record_id'))
        require(type(identity) in (str, int) and str(identity) and str(identity) not in ids, 'Missing or duplicate source identity')
        ids.add(str(identity)); count = 0; west = south = math.inf; east = north = -math.inf
        for point in geometry_positions(feature.get('geometry')):
            west, east = min(west, point[0]), max(east, point[0]); south, north = min(south, point[1]), max(north, point[1]); count += 1
        require(vertex_count(feature['geometry']) == count, 'Vertex counting contract mismatch')
        rows.append({'ordinal': ordinal, 'id': identity, 'vertices': count, 'bbox': [west, south, east, north],
                     'feature_sha256': sha(encoded(feature)), 'geometry_sha256': sha(encoded(feature['geometry'])),
                     'properties_sha256': sha(encoded(feature['properties'])), 'feature': feature})
    return rows


def bounds(rows):
    return [min(r['bbox'][0] for r in rows), min(r['bbox'][1] for r in rows), max(r['bbox'][2] for r in rows), max(r['bbox'][3] for r in rows)]


def partition_rows(rows, *, grid_degrees=GRID_DEGREES, vertex_target=VERTEX_TARGET):
    require(type(grid_degrees) in (int, float) and math.isfinite(grid_degrees) and .01 <= grid_degrees <= 5, 'Invalid spatial grid')
    require(type(vertex_target) is int and vertex_target > 0, 'Invalid vertex target')
    cells = defaultdict(list)
    for row in rows:
        w, s, e, n = row['bbox']
        # Same centroid-cell convention as retile.cell_key; only grouping changes.
        cell = (math.floor((w+e)/2/grid_degrees + 1e-8), math.floor((s+n)/2/grid_degrees + 1e-8))
        cells[cell].append(row)
    result = []
    for cell, items in sorted(cells.items()):
        batch, total, page = [], 0, 0
        for row in sorted(items, key=lambda item: item['ordinal']):
            if batch and total + row['vertices'] > vertex_target:
                result.append((cell, page, batch)); page += 1; batch, total = [], 0
            batch.append(row); total += row['vertices']
            if row['vertices'] > vertex_target:
                result.append((cell, page, batch)); page += 1; batch, total = [], 0
        if batch:
            result.append((cell, page, batch))
    return result


def overlaps(a, b):
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def active_assets(assets, bbox, height):
    # Match selectViewAssets' spatial/height predicates; no GPU/FPS claim.
    return [a for a in assets if overlaps(a['bbox'], bbox) and height >= a.get('min_camera_height', 0) and height < a.get('max_camera_height', math.inf)]


def representation_assets(source, details):
    require('detail_level' not in source and 'min_camera_height' not in source and 'max_camera_height' not in source, 'Source scale contract changed')
    # Do not call the original an overview: the client's overload fallback may
    # otherwise select the nationwide payload again below its minimum height.
    nationwide = {**source, 'min_camera_height': SWITCH_HEIGHT}
    return [nationwide, *[{**a, 'detail_level': 'detail', 'max_camera_height': SWITCH_HEIGHT} for a in details]]


VIEWS = [
    ('korea', [124.5, 33, 132, 38.7], 1500000),
    ('capital-region', [126.2, 36.8, 128, 38.3], 100000),
    ('capital-region-detail-stress', [126.2, 36.8, 128, 38.3], 59999),
    ('daejeon', [127.373, 36.282, 127.493, 36.382], 4800),
    ('sejong', [127.205, 36.454, 127.325, 36.554], 6200),
    ('cheongju', [127.429, 36.593, 127.549, 36.693], 5200),
    ('seoul', [126.918, 37.516, 127.038, 37.616], 6200),
    ('busan', [128.981, 35.065, 129.101, 35.165], 7500),
    ('incheon', [126.577, 37.340, 126.697, 37.440], 7500),
    ('gangwon-lakes', [127.55, 37.6, 128.4, 38.3], 22000),
    ('jeju-island', [126.1, 33.1, 127.0, 33.65], 28000),
    ('jeju-city', [126.467, 33.449, 126.587, 33.549], 11000),
    ('ulleung', [130.79, 37.43, 131.0, 37.57], 18000),
    ('dokdo', [131.85, 37.22, 131.89, 37.26], 3500),
]


def selection_costs(source, assets, rows, tile_rows, views=VIEWS):
    result = []
    for name, bbox, height in views:
        active = active_assets(assets, bbox, height)
        ordinals = [r['ordinal'] for a in active for r in (rows if a['id'] == source['id'] else tile_rows[a['id']])]
        needed = {r['ordinal'] for r in rows if overlaps(r['bbox'], bbox)}
        require(len(ordinals) == len(set(ordinals)) and needed.issubset(ordinals), 'Active selection lost or duplicated source geometry')
        result.append({'view': name, 'bbox': bbox, 'camera_height_m': height, 'original_files': int(overlaps(source['bbox'], bbox)),
                       'original_bytes': source['byte_length'] if overlaps(source['bbox'], bbox) else 0,
                       'original_vertices': source['vertex_count'] if overlaps(source['bbox'], bbox) else 0,
                       'candidate_files': len(active), 'candidate_bytes': sum(a['byte_length'] for a in active),
                       'candidate_vertices': sum(a['vertex_count'] for a in active), 'candidate_features': len(ordinals),
                       'source_feature_bboxes_intersecting': len(needed), 'source_bbox_features_preserved': True,
                       'exceeds_low_water_only_file_budget_36': len(active) > 36,
                       'vertex_reduction_percent': round(100*(1-sum(a['vertex_count'] for a in active)/source['vertex_count']), 3)})
    return result


def index_hashes(catalog):
    """Dry comparison of static_release's 512-entry index grouping; no writes."""
    groups = defaultdict(list)
    for a in sorted(catalog['assets'], key=lambda a: a['id']):
        if a['format'] != 'geojson':
            continue
        w, s, e, n = a['bbox']; detail = a.get('detail_level', 'detail')
        cell = () if detail == 'overview' else (math.floor(w+e), math.floor(s+n))
        groups[(a['layer'], detail, a.get('min_camera_height'), a.get('max_camera_height'), *cell)].append(a)
    return {sha(encoded({'schema_version': 1, 'assets': group[at:at+512]})) for group in groups.values() for at in range(0, len(group), 512)}


def _write_new(path, payload):
    no_links(path)
    if path.exists():
        require(path.is_file() and path.read_bytes() == payload, 'Immutable candidate already exists with different bytes')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(payload)


def build_candidate(catalog_path=DEFAULT_INPUT, *, public=PUBLIC, output=DEFAULT_OUTPUT,
                    grid_degrees=GRID_DEGREES, vertex_target=VERTEX_TARGET, disk_reserve=DISK_RESERVE, baseline_files=17537):
    catalog_path, public, output = Path(catalog_path).absolute(), Path(public).absolute(), Path(output).absolute()
    require(not output.resolve().is_relative_to(public.resolve()), 'Candidate output must not be inside public data')
    no_links(output)
    existing = output
    while not existing.exists():
        existing = existing.parent
    require(shutil.disk_usage(existing).free >= disk_reserve, '30 GiB disk reserve unavailable')
    input_bytes = bounded_read(catalog_path); catalog = json.loads(input_bytes)
    require(catalog.get('schema_version') == 1 and isinstance(catalog.get('assets'), list), 'Expected flat v1 catalog')
    matching = [a for a in catalog['assets'] if a.get('id') == SOURCE_ID]
    require(len(matching) == 1, 'Expected one nationwide water source')
    source = matching[0]
    require(source.get('format') == 'geojson' and source.get('layer') == 'terrain' and source.get('source_id') == 'osm', 'Water source contract changed')
    path = source_path(source['url'], public); raw = bounded_read(path)
    require(HASH.fullmatch(source.get('sha256', '')) and sha(raw) == source['sha256'], 'Water source hash mismatch')
    document = json.loads(raw)
    require(document.get('type') == 'FeatureCollection' and 'metadata' not in document, 'Expected original flat water features')
    rows = feature_rows(document.get('features')); vertices = sum(row['vertices'] for row in rows)
    require(source.get('count') == source.get('feature_count') == len(rows) and source.get('vertex_count') == vertices and source.get('byte_length') == len(raw), 'Source budget metadata mismatch')
    partition = partition_rows(rows, grid_degrees=grid_degrees, vertex_target=vertex_target)
    require(len(partition) <= 200, 'Water candidate would exceed 200 detail files')
    identity = {'version': VERSION, 'source_sha256': sha(raw), 'grid_degrees': grid_degrees, 'vertex_target': vertex_target, 'switch_height_m': SWITCH_HEIGHT}
    water_id = sha(encoded(identity))[:20]; run_id = sha(encoded({**identity, 'catalog_sha256': sha(input_bytes)}))[:20]
    work = output / run_id
    require(work.resolve() != catalog_path.parent.resolve(), 'Candidate would share the input directory')
    artifacts, details, file_manifest, tile_rows, proof = {}, [], [], {}, []
    for cell, page, items in partition:
        features = [r['feature'] for r in items]; compact = compact_features(features); payload = encoded(compact)
        restored = restore_features(json.loads(payload))
        require([sha(encoded(f)) for f in restored] == [r['feature_sha256'] for r in items], 'Compact feature identity/shape/property round-trip failed')
        require(len(payload) < 24*1024**2, 'Individual source feature exceeds static asset limit')
        token = f'{cell[0]}-{cell[1]}-{page}'; name = f'water-{token}-{sha(payload)[:16]}.geojson'
        url = f'/data/water-partitions/{water_id}/{name}'; relative_path = 'public' + url
        count = sum(r['vertices'] for r in items); oversized = [r['id'] for r in items if r['vertices'] > vertex_target]
        require(count <= vertex_target or len(items) == 1 and len(oversized) == 1, 'Tile vertex budget was exceeded by a group')
        asset = {**source, 'id': f'{SOURCE_ID}-{token}', 'url': url, 'sha256': sha(payload), 'byte_length': len(payload), 'bytes': len(payload),
                 'bbox': bounds(items), 'count': len(items), 'feature_count': len(items), 'vertex_count': count,
                 'detail_level': 'detail', 'max_camera_height': SWITCH_HEIGHT, 'geometry_encoding': 'geojson-metadata-table-v1',
                 'partition_version': VERSION, 'partition_source_asset_id': SOURCE_ID, 'partition_vertex_target': vertex_target,
                 'vertex_budget_exceeded': bool(oversized), 'oversize_feature_ids': oversized}
        artifacts[relative_path] = payload; details.append(asset); tile_rows[asset['id']] = items
        file_manifest.append({'url': url, 'path': relative_path, 'sha256': sha(payload), 'bytes': len(payload), 'feature_count': len(items), 'vertex_count': count})
        for row in items:
            proof.append({**{k: v for k, v in row.items() if k != 'feature'}, 'output_asset_id': asset['id'], 'output_feature_sha256': row['feature_sha256']})
    require(sorted(row['ordinal'] for row in proof) == list(range(len(rows))), 'Not every source feature occurs exactly once in detail')
    representations = representation_assets(source, details)
    candidate = copy.deepcopy(catalog); candidate['assets'] = [a for a in candidate['assets'] if a['id'] != SOURCE_ID] + representations
    require(len({a['id'] for a in candidate['assets']}) == len(candidate['assets']), 'Candidate asset ID collision')
    for height in (59999, 60000, 60001):
        selected = active_assets(representations, bounds(rows), height)
        require(sum(a['feature_count'] for a in selected) == len(rows) and sum(a['vertex_count'] for a in selected) == vertices, 'Scale boundary duplicates or omits source geometry')
        require(all(a.get('detail_level') != 'overview' for a in selected), 'Water must never enter overview fallback')
    counts = sorted(r['vertices'] for r in rows)
    distribution = {'features': len(rows), 'vertices': vertices, 'minimum': counts[0], 'median': counts[len(counts)//2],
                    **{f'p{q}': counts[math.ceil(len(counts)*q/100)-1] for q in (90, 95, 99)}, 'maximum': counts[-1],
                    'over_8192': sum(v > 8192 for v in counts), 'over_16384': sum(v > 16384 for v in counts),
                    'largest': [{k: r[k] for k in ('id', 'vertices', 'bbox')} for r in sorted(rows, key=lambda r: -r['vertices'])[:12]]}
    before_indexes, after_indexes = index_hashes(catalog), index_hashes(candidate)
    index_additions = len(after_indexes-before_indexes); index_retirements = len(before_indexes-after_indexes)
    costs = selection_costs(source, representations, rows, tile_rows)
    audit = {'version': VERSION, 'passed': True, 'published': False, 'source_catalog_sha256': sha(input_bytes), 'source_asset_sha256': sha(raw),
             'source_asset_url': source['url'], 'source_feature_count': len(rows), 'logical_source_feature_count': len(rows),
             'posted_representation_feature_count': len(rows)*2, 'detail_feature_count': sum(a['feature_count'] for a in details),
             'nationwide_feature_count': len(rows), 'source_vertex_count': vertices, 'detail_vertex_count': sum(a['vertex_count'] for a in details),
             'detail_files': len(details), 'detail_bytes': sum(a['byte_length'] for a in details), 'max_tile_vertices': max(a['vertex_count'] for a in details),
             'max_tile_bytes': max(a['byte_length'] for a in details), 'oversized_single_features': sum(a['vertex_budget_exceeded'] for a in details),
             'coordinate_delta': 0, 'coordinate_delta_scope': 'Exact source numeric values/ring order/holes/multipart parts; no transform, quantization, simplification or clipping',
             'feature_sha256_roundtrip': True, 'original_id_properties_geometry_preserved': True,
             'nationwide_descriptor_change': {'min_camera_height': SWITCH_HEIGHT}, 'detail_selection': {'detail_level': 'detail', 'max_camera_height': SWITCH_HEIGHT},
             'scale_boundary_heights_verified': [59999, 60000, 60001], 'layer_record_counts_unchanged': candidate['layers'] == catalog['layers'],
             'other_asset_payloads_unchanged': [a for a in candidate['assets'] if a['id'] != SOURCE_ID and a.get('partition_version') != VERSION] == [a for a in catalog['assets'] if a['id'] != SOURCE_ID],
             'storage_projection': {'detail_files_added': len(details), 'new_index_hashes': index_additions, 'retired_index_hashes': index_retirements,
                                    'conservative_new_files_including_4_catalog_coverage_metadata': len(details)+index_additions+4,
                                    'baseline_static_files': baseline_files, 'conservative_projected_static_files': baseline_files+len(details)+index_additions+4},
             'limitations': ['BBox costs precede the scene-wide bytes/vertices/file admission limit; no GPU timing was measured.',
                            'At wide near-altitude views, combined layers may defer some detail tiles; the original nationwide representation remains active from 60km.',
                            'The two mutually exclusive scale representations do not double the distinct source water count.',
                            'Public files and catalog pointers have not been written. Copy the declared new files before staging this private catalog.']}
    require(audit['other_asset_payloads_unchanged'] and audit['layer_record_counts_unchanged'], 'Unrelated catalog content changed')
    artifacts.update({'catalog.json': encoded(candidate), 'assets.json': encoded(representations), 'files.json': encoded(file_manifest),
                      'feature-proof.json': encoded(sorted(proof, key=lambda r: r['ordinal'])), 'distribution.json': encoded(distribution),
                      'selection-costs.json': encoded({'scope': 'Conservative asset bbox selection; comparison is water-only, not GPU or total scene performance', 'views': costs}),
                      'audit.json': encoded(audit)})
    checkpoint = {'complete': True, 'version': VERSION, 'source_catalog_sha256': sha(input_bytes), 'source_asset_sha256': sha(raw),
                  'catalog_sha256': sha(artifacts['catalog.json']), 'audit_sha256': sha(artifacts['audit.json']),
                  'artifacts': [{'path': name, 'bytes': len(payload), 'sha256': sha(payload)} for name, payload in sorted(artifacts.items())]}
    planned_bytes = sum(len(payload) for payload in artifacts.values()) + len(encoded(checkpoint))
    require(planned_bytes <= STORAGE_LIMIT and memory_bytes() <= MEMORY_LIMIT, 'Candidate storage or process memory budget exceeded')
    require(shutil.disk_usage(existing).free-planned_bytes >= disk_reserve, '30 GiB reserve would be crossed')
    if work.exists():
        for existing_file in work.rglob('*'):
            no_links(existing_file)
            if existing_file.is_file():
                require(existing_file.relative_to(work).as_posix() in {*artifacts, 'checkpoint.json'}, 'Unrecognized file in candidate folder')
    # Existing candidates are resumable only when every already-written byte agrees.
    for name, payload in artifacts.items():
        target = work / name
        if target.exists():
            no_links(target); require(target.is_file() and target.read_bytes() == payload, 'Existing candidate differs; no overwrite permitted')
    for name, payload in artifacts.items():
        _write_new(work/name, payload)
    for entry in file_manifest:
        require(digest(work/entry['path']) == entry['sha256'], 'Written candidate hash mismatch')
    require(digest(path) == sha(raw) and digest(catalog_path) == sha(input_bytes), 'Source changed during candidate creation')
    _write_new(work/'checkpoint.json', encoded(checkpoint))
    return {'work': str(work), 'catalog': str(work/'catalog.json'), 'catalog_sha256': checkpoint['catalog_sha256'],
            'audit': audit, 'artifact_bytes': planned_bytes, 'process_peak_bytes': memory_bytes(),
            'disk_free_bytes': shutil.disk_usage(existing).free, 'selection_costs': costs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--grid-degrees', type=float, default=GRID_DEGREES)
    parser.add_argument('--vertex-target', type=int, default=VERTEX_TARGET)
    args = parser.parse_args()
    require(args.output.resolve().is_relative_to(DEFAULT_OUTPUT.resolve()), 'CLI output is limited to the private water workspace')
    result = build_candidate(args.catalog, output=args.output, grid_degrees=args.grid_degrees, vertex_target=args.vertex_target)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
