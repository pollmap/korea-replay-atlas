"""Non-destructive, resumable nationwide spatial retiling for Static Assets.

Source geometry is never simplified. Medium LOD is an explicitly labelled
representative subset; only detail counts contribute to catalog coverage.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
import time

import numpy as np
from shapely.geometry import shape

from .core import PUBLIC, LOCAL, atomic_json, digest
from .height_quality import VERSION as QUALITY_VERSION, evaluate_feature
from .mesh import write_glb
from .mesh_metadata_audit import audit_glb, parse_glb
from .glb_merge import merge_tiles, normalize_empty_metadata

VERSION = 'spatial-retile-2'
MAX_GLB = 4 * 1024 * 1024
MAX_JSON = 3 * 1024 * 1024
MINIMAL = ('source_record_id', 'kind', 'name', 'highway', 'railway', 'building', 'height', 'height_method', 'quality_state', 'render_eligible')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def compact_features(features):
    """Losslessly factor common properties; never quantize geometry coordinates."""
    if not features:
        return {'type': 'FeatureCollection', 'features': [], 'metadata': {'schema_version': 1, 'shared': {}, 'rows': []}}
    shared = dict(features[0].get('properties') or {})
    for feature in features[1:]:
        props = feature.get('properties') or {}
        shared = {k: v for k, v in shared.items() if k in props and encoded(props[k]) == encoded(v)}
    rows, result = [], []
    for feature in features:
        props = feature.get('properties') or {}
        minimal = {k: props[k] for k in MINIMAL if k in props}
        rest = {k: v for k, v in props.items() if k not in shared and k not in minimal}
        # Empty rows can be shared; all other rows remain deterministic.
        index = len(rows)
        rows.append(rest)
        result.append({**feature, 'properties': {**minimal, 'metadata_index': index}})
    return {'type': 'FeatureCollection', 'features': result, 'metadata': {'schema_version': 1, 'shared': shared, 'rows': rows}}


def restore_features(document):
    metadata = document['metadata']
    return [{**f, 'properties': {**metadata['shared'], **metadata['rows'][f['properties']['metadata_index']],
                               **{k: v for k, v in f['properties'].items() if k != 'metadata_index'}}}
            for f in document['features']]


def bounds_of(features):
    bounds = [shape(f['geometry']).bounds for f in features]
    return [min(b[0] for b in bounds), min(b[1] for b in bounds), max(b[2] for b in bounds), max(b[3] for b in bounds)]


def vertex_count(geometry):
    if geometry['type'] == 'GeometryCollection':
        return sum(vertex_count(part) for part in geometry['geometries'])
    def walk(values):
        if not values:
            return 0
        if isinstance(values[0], (int, float)):
            return 1
        return sum(walk(value) for value in values)
    return walk(geometry['coordinates'])


def box_union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes), min(b[4] for b in boxes), max(b[5] for b in boxes)]


def feature_position(feature):
    b = shape(feature['geometry']).bounds
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def cell_key(feature, dx=.05, dy=None):
    x, y = feature_position(feature)
    return math.floor(x / dx + 1e-8), math.floor(y / (dy or dx) + 1e-8)


def glb_strings(path, key):
    document, binary = parse_glb(path.read_bytes())
    def raw(index):
        view = document['bufferViews'][index]
        start = view.get('byteOffset', 0)
        return binary[start:start + view['byteLength']]
    values = []
    for table in document['extensions']['EXT_structural_metadata']['propertyTables']:
        prop = table['properties'][key]
        offsets = np.frombuffer(raw(prop['stringOffsets']), dtype='<u4')
        data = raw(prop['values'])
        values.extend(bytes(data[a:b]).decode() for a, b in zip(offsets[:-1], offsets[1:]))
    return values


def emit_glbs(features, target, scratch, stem, maximum=MAX_GLB, role='detail'):
    """Split on actual serialized size, keeping every feature intact."""
    candidate = scratch / f'{stem}.glb'
    candidate.parent.mkdir(parents=True, exist_ok=True)
    result = write_glb(features, candidate)
    if result is None:
        raise ValueError('Nonempty eligible group produced no geometry')
    if candidate.stat().st_size > maximum and len(features) > 1:
        ordered = sorted(features, key=lambda f: (*feature_position(f), str(f.get('id', ''))))
        middle = len(ordered) // 2
        return (emit_glbs(ordered[:middle], target, scratch, stem+'a', maximum, role)
                + emit_glbs(ordered[middle:], target, scratch, stem+'b', maximum, role))
    if candidate.stat().st_size >= 24 * 1024 * 1024:
        raise ValueError(f'Individual geometry exceeds Static Assets safety limit: {features[0].get("id")}')
    audit = audit_glb(candidate)
    expected = [str(f['properties']['source_record_id']) for f in features]
    if glb_strings(candidate, 'source_record_id') != expected:
        raise ValueError('GLB source identity round-trip failed')
    target.mkdir(parents=True, exist_ok=True)
    filename = f'{stem}-{audit["sha256"][:16]}.glb'
    final = target / filename
    candidate.replace(final)
    tile = result[0]
    tile['content']['uri'] = filename
    tile['extras'].update({'lod_role': role, 'feature_count': result[1], 'vertex_count': audit['vertices'], 'sha256': audit['sha256'], 'bytes': final.stat().st_size})
    return [tile]


def emit_compact(features, folder, stem, template, maximum=MAX_JSON):
    document = compact_features(features)
    payload = encoded(document)
    if len(payload) > maximum and len(features) > 1:
        middle = len(features) // 2
        return emit_compact(features[:middle], folder, stem+'a', template, maximum) + emit_compact(features[middle:], folder, stem+'b', template, maximum)
    if len(payload) >= 24 * 1024 * 1024:
        raise ValueError('Individual GeoJSON feature exceeds asset limit')
    if restore_features(document) != features:
        raise ValueError('Compact GeoJSON lossless round-trip failed')
    sha = hashlib.sha256(payload).hexdigest()
    path = folder / f'{stem}-{sha[:16]}.geojson'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(payload)
    return [{**template, 'version': template.get('version', template.get('dataset_version', 'source-records')), 'id': stem, 'url': '/data/' + path.relative_to(PUBLIC).as_posix(), 'bytes': len(payload), 'byte_length': len(payload),
             'sha256': sha, 'count': len(features), 'bbox': bounds_of(features), 'format': 'geojson',
             'feature_count': len(features), 'vertex_count': sum(vertex_count(f['geometry']) for f in features),
             'geometry_encoding': 'geojson-metadata-table-v1'}]


def process_building(job):
    source, output, work = job
    output, work = Path(output), Path(work)
    checkpoint = work / 'checkpoints' / (source['id'] + '.json')
    if checkpoint.exists():
        result = json.loads(checkpoint.read_text(encoding='utf-8'))
        # Resume only complete, unchanged outputs, not a partially written cell.
        def valid_file(path, expected):
            return path.is_file() and digest(path) == expected
        valid = all(valid_file(output / 'detail' / t['content']['uri'], t['extras']['sha256']) for t in result['tiles'])
        valid = valid and all(valid_file(PUBLIC / a['url'].removeprefix('/data/'), a['sha256']) for a in result['footprints'])
        valid = valid and Path(result['lod_path']).is_file() and result['source_sha256'] == source['sha256']
        if valid:
            return result
    path = Path(source['path'])
    if digest(path) != source['sha256']:
        raise ValueError('Normalized source changed after snapshot: ' + source['id'])
    features = json.loads(path.read_text(encoding='utf-8'))['features']
    if len(features) != source['count']:
        raise ValueError('Source count mismatch')
    groups, unknown, lod = defaultdict(list), [], {}
    identity_hash, geometry_hash = hashlib.sha256(), hashlib.sha256()
    for feature in features:
        identity_hash.update(encoded(feature.get('id')))
        geometry_hash.update(encoded(feature['geometry']))
        quality = evaluate_feature(feature)
        props = {**feature['properties'], **quality, 'original_properties': feature['properties']}
        render = {**feature, 'properties': props}
        if not quality['render_eligible']:
            unknown.append(render)
            continue
        props.update({'height': quality['render_height'], 'min_height': quality['render_min_height'], 'lod_role': 'detail'})
        groups[cell_key(render)].append(render)
        key = cell_key(render, .025)
        prior = lod.get(key)
        if prior is None or (props['height'], str(feature.get('id'))) > (prior['properties']['height'], str(prior.get('id'))):
            lod[key] = render
    tiles = []
    for index, (key, items) in enumerate(sorted(groups.items())):
        tiles.extend(emit_glbs(items, output/'detail', work/'scratch'/source['id'], f'{source["id"]}-{key[0]}-{key[1]}'))
    footprint_assets = []
    if unknown:
        footprint_assets = emit_compact(unknown, output/'footprints', source['id']+'-footprints',
            {'layer': 'buildings', 'source_id': 'overture', 'dataset_version': source.get('dataset_version', '2026-08-19.0'),
             'detail_level': 'detail', 'max_camera_height': 60000, 'evidence_type': 'unverified'})
    lod_path = work/'lod-source'/(source['id']+'.json')
    atomic_json(lod_path, list(lod.values()))
    result = {'id': source['id'], 'source_sha256': source['sha256'], 'source_count': len(features),
              'detail_count': sum(t['extras']['feature_count'] for t in tiles), 'footprint_count': len(unknown),
              'identity_sha256': identity_hash.hexdigest(), 'source_geometry_sha256': geometry_hash.hexdigest(),
              'geojson_geometry_error': 0, 'tiles': tiles, 'footprints': footprint_assets, 'lod_path': str(lod_path)}
    if result['detail_count'] + result['footprint_count'] != len(features):
        raise ValueError('Building coverage mismatch')
    atomic_json(checkpoint, result)
    return result


def compact_detail(results, output, work):
    """Merge adjacent small tiles inside each original 0.1-degree source cell."""
    packed = []
    for number, result in enumerate(results):
        signature = hashlib.sha256(encoded({'merge_version': 3, 'tiles': result['tiles'], 'maximum': MAX_GLB})).hexdigest()
        receipt = work/'packed'/(result['id']+'.json')
        if receipt.exists():
            previous = json.loads(receipt.read_bytes())
            if previous['input_signature'] == signature and all(
                    (output/'detail'/tile['content']['uri']).is_file() and
                    digest(output/'detail'/tile['content']['uri']) == tile['extras']['sha256'] for tile in previous['tiles']):
                packed.append({**result, 'tiles': previous['tiles']})
                continue
        ordered = sorted(result['tiles'], key=lambda tile: tuple(tile['boundingVolume']['region'][:2]))
        batches, current, size = [], [], 0
        for tile in ordered:
            length = tile['extras']['bytes']
            if current and size+length > MAX_GLB-65536:
                batches.append(current); current, size = [], 0
            current.append(tile); size += length
        if current:
            batches.append(current)
        replacement = []
        for index, batch in enumerate(batches):
            if len(batch) == 1:
                replacement.append(normalize_empty_metadata(batch[0], output/'detail'))
                continue
            temp = work/'scratch'/'packed'/f'{result["id"]}-{index}.glb'
            tile = merge_tiles(batch, output/'detail', temp)
            if tile['extras']['bytes'] > MAX_GLB:
                raise ValueError('Merged tile exceeded 4MiB budget')
            expected_ids = [identity for original in batch for identity in glb_strings(output/'detail'/original['content']['uri'], 'source_record_id')]
            if glb_strings(temp, 'source_record_id') != expected_ids:
                raise ValueError('Merged source identities changed')
            name = f'packed-{tile["extras"]["sha256"][:32]}.glb'
            final = output/'detail'/name
            temp.replace(final); tile['content']['uri'] = name; replacement.append(tile)
        if sum(tile['extras']['feature_count'] for tile in replacement) != result['detail_count']:
            raise ValueError('Compaction changed detail feature count')
        atomic_json(receipt, {'input_signature': signature, 'tiles': replacement})
        packed.append({**result, 'tiles': replacement})
        if number % 100 == 0:
            print(json.dumps({'stage': 'retile-compact', 'source_cells': number+1, 'total': len(results)}), flush=True)
    return packed


def build_buildings(sources, output, work, workers=2, compact=False):
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        iterator = iter(sources)
        pending = {pool.submit(process_building, (s, str(output), str(work))) for s in [next(iterator, None) for _ in range(workers)] if s}
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                result = future.result()
                results.append(result)
                print(json.dumps({'stage': 'retile-building', 'done': len(results), 'total': len(sources), 'id': result['id'],
                                  'detail': result['detail_count'], 'tiles': len(result['tiles'])}), flush=True)
                source = next(iterator, None)
                if source:
                    pending.add(pool.submit(process_building, (source, str(output), str(work))))
    results.sort(key=lambda r: r['id'])
    return build_hierarchy(results, output, work, compact)


def build_hierarchy(results, output, work, compact=False):
    # Import lazily because hierarchy reuses the GLB/property helpers above.
    from .hierarchy import (VERSION as HIERARCHY_VERSION, build_tree, verify_tree,
                            prepare_retile_results, hierarchy_plan, replacement_budget, MAX_NEW_FILES)
    if compact:
        results = compact_detail(results, output, work)
    tiles, representatives = prepare_retile_results(results, output)
    plan = hierarchy_plan(tiles)
    if plan['nodes'] + 1 > MAX_NEW_FILES:
        raise ValueError('Bounded hierarchy exceeds its additional file budget')
    root, stats = build_tree(tiles, representatives, output, work)
    transform_proof = verify_tree(root, tiles, output)
    count = sum(r['detail_count'] for r in results)
    country = {'asset': {'version': '1.1'}, 'geometricError': root['geometricError']*2, 'root': root,
               'extras': {'height_quality_policy': QUALITY_VERSION, 'detail_count': count,
                          'hierarchy_version': HIERARCHY_VERSION,
                          'lod_notice': 'Intermediate geometry is a representative source-building subset; full detail preserves every eligible source building.'}}
    country_path = output/'tileset.json'
    atomic_json(country_path, country)
    bbox = country['root']['boundingVolume']['region'][:4]
    asset = {'id': 'buildings-korea-retiled', 'layer': 'buildings', 'format': '3d-tiles', 'source_id': 'overture', 'detail_level': 'overview',
             'url': '/data/'+country_path.relative_to(PUBLIC).as_posix(), 'bbox': [math.degrees(v) for v in bbox],
             'sha256': digest(country_path), 'bytes': country_path.stat().st_size, 'count': count,
             'version': '2026-08-19.0', 'dataset_version': '2026-08-19.0', 'hierarchy_version': HIERARCHY_VERSION}
    audit = {'source_count': sum(r['source_count'] for r in results), 'detail_count': count, 'footprint_count': sum(r['footprint_count'] for r in results),
             'detail_files': len(tiles), 'region_files': 0, 'lod_files': stats['lod_files'], 'lod_representative_count': stats['lod_features'],
             'hierarchy': {'version': HIERARCHY_VERSION, 'plan': plan, 'generated': stats,
                           'transform_proof': transform_proof, 'replacement_budget': replacement_budget(root)},
             'coordinate_rounding_max_m': max(t['extras']['coordinate_rounding_max_m'] for t in tiles),
             'additional_coordinate_rounding_max_m': max(t['extras'].get('additional_coordinate_rounding_max_m', 0) for t in tiles),
             'source_cells': [{k: v for k, v in r.items() if k not in ('tiles', 'footprints', 'lod_path')} for r in results]}
    footprints = [a for r in results for a in r['footprints']]
    enrich_geometry_budgets(footprints)
    return [asset] + footprints, audit


def enrich_geometry_budgets(assets):
    """Complete work budgets for resumed descriptors from older checkpoints."""
    for asset in assets:
        if not asset.get('version'):
            version = asset.get('dataset_version')
            if not isinstance(version, str) or not version:
                raise ValueError('Asset has no explicit source version: '+asset['id'])
            asset['version'] = version
        if asset['format'] != 'geojson' or 'vertex_count' in asset:
            continue
        path = PUBLIC/asset['url'].removeprefix('/data/')
        if digest(path) != asset['sha256']:
            raise ValueError('Geometry changed before recording work budget')
        document = json.loads(path.read_bytes())
        asset.update(feature_count=len(document['features']), vertex_count=sum(vertex_count(f['geometry']) for f in document['features']),
                     byte_length=path.stat().st_size)


def build_geometry(catalog, output, work):
    """Spool spatial groups on disk; bound RAM to one source/tile group."""
    database = work/'geometry.sqlite'
    db = sqlite3.connect(database)
    db.execute('CREATE TABLE IF NOT EXISTS records (source TEXT, ordinal INTEGER, grp TEXT, payload TEXT, PRIMARY KEY(source,ordinal))')
    db.execute('CREATE TABLE IF NOT EXISTS complete (source TEXT PRIMARY KEY, sha TEXT)')
    # Preserve the station-only role so the UI can suppress duplicate station
    # points while the infrastructure layer is active.
    db.execute("UPDATE records SET grp=replace(grp,'rail-detail-osm|','rail-stations-detail-osm|') WHERE source='osm-stations-korea' AND grp LIKE 'rail-detail-osm|%'")
    db.commit()
    templates, inputs = {}, []
    for asset in catalog['assets']:
        if asset['layer'] not in ('infrastructure', 'rail') or asset['format'] != 'geojson':
            continue
        level = asset.get('detail_level', 'detail')
        source = asset.get('source_id', 'osm')
        role = '-stations' if asset['id'] == 'osm-stations-korea' else ''
        prefix = f'{asset["layer"]}{role}-{level}-{source}'
        templates[prefix] = {k: asset[k] for k in ('layer', 'source_id', 'dataset_version', 'detail_level', 'min_camera_height', 'max_camera_height', 'evidence_type') if k in asset}
        templates[prefix]['version'] = 'source-records'
        if role:
            templates[prefix]['geometry_role'] = 'stations'
        inputs.append(asset)
        prior = db.execute('SELECT sha FROM complete WHERE source=?', (asset['id'],)).fetchone()
        if prior and prior[0] == asset['sha256']:
            continue
        path = PUBLIC/asset['url'].removeprefix('/data/')
        if digest(path) != asset['sha256']:
            raise ValueError('Geometry source hash mismatch: '+asset['id'])
        document = json.loads(path.read_text(encoding='utf-8'))
        features = document['features']
        if len(features) != asset['count']:
            raise ValueError('Geometry source count mismatch')
        db.execute('DELETE FROM records WHERE source=?', (asset['id'],))
        for index, feature in enumerate(features):
            grid = .5 if level == 'overview' else .25
            key = cell_key(feature, grid)
            group = f'{prefix}|{key[0]}|{key[1]}'
            db.execute('INSERT INTO records VALUES (?,?,?,?)', (asset['id'], index, group, encoded(feature).decode()))
        db.execute('INSERT OR REPLACE INTO complete VALUES (?,?)', (asset['id'], asset['sha256']))
        db.commit()
    db.execute('CREATE INDEX IF NOT EXISTS spatial_group ON records(grp)')
    groups = [row[0] for row in db.execute('SELECT DISTINCT grp FROM records ORDER BY grp')]
    result, count = [], 0
    for index, group in enumerate(groups):
        prefix, x, y = group.split('|')
        template = templates[prefix]
        # Cap uncompressed source metadata in memory; compact output gets a
        # separate hard size check. Every original ID and vertex is retained.
        batch, size, page = [], 0, 0
        for (payload,) in db.execute('SELECT payload FROM records WHERE grp=? ORDER BY source,ordinal', (group,)):
            batch.append(json.loads(payload)); size += len(payload)
            if size >= 8*1024*1024:
                result.extend(emit_compact(batch, output/'geometry', f'{prefix}-{x}-{y}-{page}', template))
                count += len(batch); batch, size, page = [], 0, page+1
        if batch:
            result.extend(emit_compact(batch, output/'geometry', f'{prefix}-{x}-{y}-{page}', template)); count += len(batch)
        if index % 50 == 0:
            print(json.dumps({'stage': 'retile-geometry', 'groups': index+1, 'total': len(groups), 'files': len(result)}), flush=True)
    db.close()
    expected = sum(a['count'] for a in inputs)
    if count != expected:
        raise ValueError('Geometry coverage mismatch')
    for asset in result:
        if asset['layer'] == 'rail':
            asset['detail_level'] = 'detail'
            asset['max_camera_height'] = min(asset.get('max_camera_height', 200000), 200000)
    overview = build_rail_overview(catalog, output)
    result.extend(overview)
    return result, {'source_files': len(inputs), 'output_files': len(result), 'feature_count': count, 'source_geometry_error': 0,
                    'geometry_error_scope': 'unchanged detail geometry; overview is explicitly generalized',
                    'overview_files': len(overview), 'overview_feature_count': sum(a['count'] for a in overview), 'overview_tolerance_m': 100,
                    'metadata_roundtrip': 'all output features compared before serialization', 'source_sha256': {a['id']: a['sha256'] for a in inputs}}


def build_rail_overview(catalog, output):
    from pyproj import Transformer
    from shapely.geometry import mapping
    from shapely.ops import transform
    source = next((asset for asset in catalog['assets'] if asset['id'] == 'osm-rail-korea'), None)
    if source is None:
        return []
    path = PUBLIC/source['url'].removeprefix('/data/')
    if digest(path) != source['sha256']:
        raise ValueError('Rail overview source hash mismatch')
    document = json.loads(path.read_bytes())
    metric = Transformer.from_crs(4326, 5179, always_xy=True).transform
    geographic = Transformer.from_crs(5179, 4326, always_xy=True).transform
    features = []
    for original in document['features']:
        generalized = transform(geographic, transform(metric, shape(original['geometry'])).simplify(100, preserve_topology=True))
        if generalized.is_empty:
            raise ValueError('Rail overview simplification lost a source feature')
        feature = {**original, 'geometry': mapping(generalized), 'properties': {**original.get('properties', {}),
                   'geometry_precision': 'generalized_for_distant_view', 'simplification_tolerance_m': 100,
                   'lod_role': 'overview_generalized'}}
        # Round-trip tuples into standard JSON arrays before metadata comparison.
        features.append(json.loads(encoded(feature)))
    assets = emit_compact(features, output/'geometry', 'rail-overview-generalized-osm', {
        'layer': 'rail', 'source_id': source['source_id'], 'version': source['version'], 'detail_level': 'overview',
        'min_camera_height': 200000, 'geometry_role': 'generalized', 'generalization_m': 100})
    if sum(asset['count'] for asset in assets) != source['count']:
        raise ValueError('Rail overview source identity count changed')
    return assets


def referenced_files(assets, output):
    """Inventory published dependencies; preserved intermediates stay outside it."""
    root = output.resolve(); files = set(); manifests = []
    for asset in assets:
        path = (PUBLIC/asset['url'].removeprefix('/data/')).resolve()
        if not path.is_relative_to(root):
            continue
        files.add(path)
        if asset['format'] == '3d-tiles':
            manifests.append(path)
    visited = set()
    while manifests:
        manifest = manifests.pop()
        if manifest in visited:
            continue
        visited.add(manifest)
        document = json.loads(manifest.read_bytes()); stack = [document['root']]
        while stack:
            tile = stack.pop(); stack.extend(tile.get('children', []))
            for content in ([tile['content']] if 'content' in tile else [])+tile.get('contents', []):
                uri = content.get('uri', content.get('url'))
                if not isinstance(uri, str) or any(char in uri for char in (':', '\\', '?', '#')):
                    raise ValueError('Invalid retile content URI')
                path = (manifest.parent/uri).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise ValueError('Missing or nonlocal retile content')
                files.add(path)
                if path.suffix == '.json':
                    manifests.append(path)
    return sorted(files)


def run(workers=2, limit=None, phase='all', compact=False):
    catalog = json.loads((PUBLIC/'catalog.json').read_text(encoding='utf-8'))
    with sqlite3.connect(LOCAL/'catalog.sqlite') as db:
        sources = sorted([json.loads(r[0]) for r in db.execute("SELECT payload FROM assets WHERE id LIKE 'normalized-buildings-%'")], key=lambda a: a['id'])
    if limit:
        sources = sources[:limit]
    fingerprint = hashlib.sha256(encoded({'version': VERSION, 'quality': QUALITY_VERSION, 'sources': [(a['id'], a['sha256']) for a in sources],
                                         'catalog_sha': digest(PUBLIC/'catalog.json'), 'max_glb': MAX_GLB})).hexdigest()[:16]
    output = PUBLIC/'retiled'/fingerprint
    work = LOCAL/'retile'/fingerprint
    work.mkdir(parents=True, exist_ok=True)
    atomic_json(work/'input-catalog.json', catalog)
    atomic_json(work/'input-buildings.json', sources)
    start = time.monotonic()
    if phase in ('all', 'buildings'):
        assets, audit = build_buildings(sources, output, work, workers, compact)
        atomic_json(work/'building-assets.json', assets); atomic_json(work/'building-audit.json', audit)
    if phase in ('all', 'geometry'):
        assets, audit = build_geometry(catalog, output, work)
        atomic_json(work/'geometry-assets.json', assets); atomic_json(work/'geometry-audit.json', audit)
    if all((work/f'{kind}-assets.json').exists() for kind in ('building', 'geometry')):
        unchanged = [a for a in catalog['assets'] if not (a['layer'] == 'buildings' or (a['layer'] in ('infrastructure', 'rail') and a['format'] == 'geojson'))]
        additions = [a for kind in ('building', 'geometry') for a in json.loads((work/f'{kind}-assets.json').read_text(encoding='utf-8'))]
        for asset in additions:
            asset.setdefault('version', asset.get('dataset_version', 'source-records'))
            if asset['id'] == 'buildings-korea-retiled':
                asset['detail_level'] = 'overview'
        staged = {**catalog, 'assets': unchanged + additions, 'retile_version': VERSION, 'retile_fingerprint': fingerprint}
        depth_override = LOCAL/'audit'/'depth-quality-asset.json'
        if depth_override.exists():
            replacement = json.loads(depth_override.read_text(encoding='utf-8'))
            staged['assets'] = [replacement if a['id'] == replacement['id'] else a for a in staged['assets']]
        search_override = LOCAL/'search-v2'/'asset.json'
        if search_override.exists():
            replacement = json.loads(search_override.read_bytes())
            staged['assets'] = [replacement if a['format'] == 'search-index' else a for a in staged['assets']]
        atomic_json(LOCAL/'retile'/'catalog.json', staged)
        files = referenced_files(additions, output)
        manifest = [{'path': p.relative_to(PUBLIC).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in files]
        atomic_json(work/'output-manifest.json', manifest)
        report = {'version': VERSION, 'fingerprint': fingerprint, 'output': str(output), 'files': len(files), 'bytes': sum(p['bytes'] for p in manifest),
                  'maximum_file_bytes': max(p['bytes'] for p in manifest), 'manifest_sha256': digest(work/'output-manifest.json'),
                  'building': json.loads((work/'building-audit.json').read_text(encoding='utf-8')), 'geometry': json.loads((work/'geometry-audit.json').read_text(encoding='utf-8')),
                  'elapsed_seconds': round(time.monotonic()-start, 3), 'limitations': ['LOD is a representative subset, not complete distant geometry.', 'Height quality labels are source assessments, not survey validation.', 'Coordinate rounding metrics do not include source terrain/height error or the local tile-up extrusion approximation.']}
        atomic_json(LOCAL/'retile'/'audit.json', report)
    print(json.dumps({'stage': 'retile-complete', 'fingerprint': fingerprint, 'phase': phase, 'elapsed': time.monotonic()-start}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=2, choices=(1, 2, 3))
    parser.add_argument('--limit', type=int)
    parser.add_argument('--phase', choices=('all', 'buildings', 'geometry', 'assemble'), default='all')
    parser.add_argument('--compact', action='store_true', help='Merge adjacent small GLBs before creating the LOD hierarchy')
    args = parser.parse_args()
    run(args.workers, args.limit, args.phase, args.compact)
