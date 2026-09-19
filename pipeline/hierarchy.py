"""Build bounded spatial LOD over immutable, already validated detail GLBs.

The old regional REPLACE nodes could require hundreds of invisible siblings.
This hierarchy limits each replacement group, and reuses every detail byte.
Representative LOD is explicitly approximate; it is not coverage or accuracy.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import time

import numpy as np

from .core import LOCAL, PUBLIC, digest
from .height_quality import VERSION as QUALITY_VERSION
from .mesh import write_glb
from .mesh_metadata_audit import audit_glb
from .retile import box_union, encoded, feature_position, glb_strings

VERSION = 'bounded-spatial-lod-1'
MAX_DIRECT_FILES = 8
MAX_DIRECT_BYTES = 24 * 1024 * 1024
MAX_LOD_BYTES = 384 * 1024
MAX_LOD_FEATURES = 96
MAX_NEW_FILES = 1200


def immutable_json(path, document):
    payload = encoded(document)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f'Immutable hierarchy output already differs: {path}')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def matrix(tile):
    return np.asarray(tile.get('transform', np.eye(4).flatten(order='F')), dtype=np.float64).reshape(4, 4, order='F')


def center(tile):
    b = tile['boundingVolume']['region']
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def compact_group(tiles, max_files=MAX_DIRECT_FILES, max_bytes=MAX_DIRECT_BYTES):
    return len(tiles) <= max_files and sum(t['extras']['bytes'] for t in tiles) <= max_bytes


def spatial_halves(tiles):
    """Median split along the longer metric axis; deterministic on ties."""
    points = [center(tile) for tile in tiles]
    spans = [max(p[i] for p in points) - min(p[i] for p in points) for i in range(2)]
    spans[0] *= math.cos(sum(p[1] for p in points) / len(points))
    axis = int(spans[1] > spans[0])
    ordered = sorted(tiles, key=lambda t: (center(t)[axis], center(t)[1-axis], t['content']['uri']))
    middle = len(ordered) // 2
    return ordered[:middle], ordered[middle:]


def spatial_groups(tiles, max_files=MAX_DIRECT_FILES, max_bytes=MAX_DIRECT_BYTES):
    """Use up to four children without the extra nodes of a binary tree."""
    result = []
    for half in spatial_halves(tiles):
        result.extend([half] if compact_group(half, max_files, max_bytes) else spatial_halves(half))
    return result


def hierarchy_plan(tiles, max_files=MAX_DIRECT_FILES, max_bytes=MAX_DIRECT_BYTES, depth=0):
    if not tiles:
        raise ValueError('Cannot plan an empty hierarchy')
    if len(tiles) == 1:
        return {'nodes': 0, 'depth': depth, 'detail_groups': 1,
                'direct_files': 1, 'direct_bytes': tiles[0]['extras']['bytes']}
    if compact_group(tiles, max_files, max_bytes):
        return {'nodes': 1, 'depth': depth, 'detail_groups': 1,
                'direct_files': len(tiles), 'direct_bytes': sum(t['extras']['bytes'] for t in tiles)}
    children = [hierarchy_plan(group, max_files, max_bytes, depth+1)
                for group in spatial_groups(tiles, max_files, max_bytes)]
    return {'nodes': 1 + sum(c['nodes'] for c in children), 'depth': max(c['depth'] for c in children),
            'detail_groups': sum(c['detail_groups'] for c in children),
            'direct_files': max(c['direct_files'] for c in children),
            'direct_bytes': max(c['direct_bytes'] for c in children)}


def read_detail_tiles(tileset_path):
    """Read published hierarchy, preserving the effective world transform."""
    result, seen_json, seen_glb = [], set(), set()
    public = PUBLIC.resolve()

    def visit(tile, context, parent):
        absolute = parent @ matrix(tile)
        uri = tile.get('content', {}).get('uri')
        if uri:
            path = (context.parent / uri).resolve()
            if not path.is_relative_to(public):
                raise ValueError('Tileset dependency escapes public data')
            if path.suffix == '.json':
                if path in seen_json:
                    raise ValueError('Duplicate/cyclic external tileset')
                seen_json.add(path)
                visit(json.loads(path.read_bytes())['root'], path, absolute)
            elif tile.get('extras', {}).get('lod_role') == 'detail':
                if path in seen_glb:
                    raise ValueError('Duplicate detailed geometry')
                seen_glb.add(path)
                entry = copy.deepcopy(tile)
                entry.pop('children', None)
                entry['transform'] = absolute.flatten(order='F').tolist()
                entry['source_path'] = str(path)
                result.append(entry)
        for child in tile.get('children', []):
            visit(child, context, absolute)

    visit(json.loads(tileset_path.read_bytes())['root'], tileset_path, np.eye(4))
    return result


def load_inputs(catalog_path, retile_work, output_work):
    """Link representative IDs to exact final GLBs, not nearest rectangles."""
    catalog = json.loads(catalog_path.read_bytes())
    roots = [a for a in catalog['assets'] if a['id'] == 'buildings-korea-retiled']
    if len(roots) != 1:
        raise ValueError('Expected exactly one retiled building asset')
    source_asset = roots[0]
    source_root = PUBLIC / source_asset['url'].removeprefix('/data/')
    if digest(source_root) != source_asset['sha256']:
        raise ValueError('Source tileset differs from catalog hash')
    visible = {Path(t['source_path']).name: t for t in read_detail_tiles(source_root)}
    tiles, representatives, seen = [], {}, set()
    source_matrix_error = 0.0
    for i, receipt in enumerate(sorted((retile_work/'packed').glob('*.json'))):
        packed = json.loads(receipt.read_bytes())
        features = json.loads((retile_work/'lod-source'/receipt.name).read_bytes())
        wanted = {str(f['properties']['source_record_id']): f for f in features}
        matched = set()
        for entry in packed['tiles']:
            tile = copy.deepcopy(entry)
            key = tile['content']['uri']
            if key not in visible or key in seen:
                raise ValueError('Packed details do not match the source hierarchy')
            seen.add(key)
            old = visible[key]
            path = Path(old['source_path'])
            if path.stat().st_size != tile['extras']['bytes'] or digest(path) != tile['extras']['sha256']:
                raise ValueError(f'Immutable detailed GLB changed: {key}')
            if old['extras']['sha256'] != tile['extras']['sha256']:
                raise ValueError('Published and packed detail hashes differ')
            source_matrix_error = max(source_matrix_error, float(np.max(np.abs(matrix(old)-matrix(tile)))))
            if source_matrix_error > 1e-7:
                raise ValueError('Packed world frame differs from the published frame')
            values = glb_strings(path, 'source_record_id')
            if len(values) != tile['extras']['feature_count']:
                raise ValueError('Detailed metadata count differs from descriptor')
            selected = []
            for identity in values:
                if identity in wanted:
                    if identity in matched:
                        raise ValueError('Representative identity is duplicated in details')
                    matched.add(identity)
                    selected.append(wanted[identity])
            representatives[key] = selected
            tile['source_path'] = str(path)
            tiles.append(tile)
        if matched != set(wanted):
            raise ValueError('A representative has no exact detailed source identity')
        if i % 100 == 0:
            print(json.dumps({'stage': 'hierarchy-input', 'source_cells': i+1, 'detail_glbs': len(tiles)}), flush=True)
    if seen != set(visible):
        raise ValueError('Hierarchy would omit detailed files')
    if sum(t['extras']['feature_count'] for t in tiles) != source_asset['count']:
        raise ValueError('Hierarchy would change detailed coverage')
    immutable_json(output_work/'source-proof.json', {
        'source_catalog_sha256': digest(catalog_path), 'source_root_sha256': source_asset['sha256'],
        'detail_files': len(tiles), 'detail_count': source_asset['count'],
        'detail_sha256': sorted(t['extras']['sha256'] for t in tiles),
        'representatives_with_exact_detail_id': sum(map(len, representatives.values())),
        'source_world_transform_max_element_difference': source_matrix_error})
    return catalog, source_asset, tiles, representatives


def select_representatives(features, bounds, maximum=MAX_LOD_FEATURES):
    if len(features) <= maximum:
        return sorted(features, key=lambda f: str(f['properties']['source_record_id']))
    west, south, east, north = [math.degrees(x) for x in bounds[:4]]
    grid = max(1, int(math.sqrt(maximum)))
    cells = {}
    for feature in features:
        x, y = feature_position(feature)
        key = (min(grid-1, max(0, int((x-west)/max(east-west, 1e-9)*grid))),
               min(grid-1, max(0, int((y-south)/max(north-south, 1e-9)*grid))))
        cells.setdefault(key, []).append(feature)
    for group in cells.values():
        group.sort(key=lambda f: (-f['properties']['height'], str(f['properties']['source_record_id'])))
    selected = [group[0] for key, group in sorted(cells.items())]
    remaining = [f for group in cells.values() for f in group[1:]]
    remaining.sort(key=lambda f: (-f['properties']['height'], str(f['properties']['source_record_id'])))
    return selected + remaining[:maximum-len(selected)]


def prepare_retile_results(results, output):
    """Use the same bounded hierarchy for future complete retile batches."""
    tiles, wanted = [], {}
    for result in results:
        for feature in json.loads(Path(result['lod_path']).read_bytes()):
            identity = str(feature['properties']['source_record_id'])
            if identity in wanted:
                raise ValueError('Duplicate representative source identity')
            wanted[identity] = feature
        for entry in result['tiles']:
            tile = copy.deepcopy(entry)
            tile['source_path'] = str(output/'detail'/tile['content']['uri'])
            tiles.append(tile)
    matched, representatives = set(), {}
    for tile in tiles:
        path = Path(tile['source_path'])
        if digest(path) != tile['extras']['sha256']:
            raise ValueError('Detailed GLB changed before hierarchy generation')
        selected = []
        for identity in glb_strings(path, 'source_record_id'):
            if identity in wanted:
                if identity in matched:
                    raise ValueError('Representative ID occurs in several detailed GLBs')
                matched.add(identity)
                selected.append(wanted[identity])
        representatives[tile['content']['uri']] = selected
    if matched != set(wanted):
        raise ValueError('Representative source identity is missing from detailed GLBs')
    return tiles, representatives


def emit_lod(features, output, work, name):
    if not features:
        return None
    features = copy.deepcopy(features)
    for feature in features:
        feature['properties']['lod_role'] = 'representative_subset'
    scratch = work/'scratch'/f'{name}.glb'
    scratch.parent.mkdir(parents=True, exist_ok=True)
    while features:
        value = write_glb(features, scratch)
        if value is None:
            raise ValueError('Representative subset generated no geometry')
        if scratch.stat().st_size <= MAX_LOD_BYTES:
            break
        # Approximate LOD may omit complex representatives; source detail stays intact.
        features = features[:len(features)//2]
    if not features:
        return None
    report = audit_glb(scratch)
    expected = [str(f['properties']['source_record_id']) for f in features]
    if glb_strings(scratch, 'source_record_id') != expected:
        raise ValueError('Representative source identity round trip failed')
    target = output/'lod'/f'{name}-{report["sha256"][:16]}.glb'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if digest(target) != report['sha256']:
            raise ValueError('Immutable LOD already differs')
    else:
        scratch.replace(target)
    tile = value[0]
    tile['content']['uri'] = 'lod/'+target.name
    tile['extras'].update(lod_role='representative_subset', feature_count=value[1],
                          sha256=report['sha256'], bytes=target.stat().st_size, vertex_count=report['vertices'])
    return tile


def build_tree(tiles, representatives, output, work, max_files=MAX_DIRECT_FILES, max_bytes=MAX_DIRECT_BYTES):
    stats = {'lod_files': 0, 'lod_features': 0, 'lod_bytes': 0, 'nodes_without_content': 0,
             'max_direct_detail_files': 0, 'max_direct_detail_bytes': 0, 'max_immediate_content_bytes': 0,
             'max_depth': 0, 'node_count': 0}

    def visit(items, name, depth):
        if len(items) == 1:
            result = copy.deepcopy(items[0])
            path = Path(result.pop('source_path'))
            result['content']['uri'] = Path(os.path.relpath(path, output)).as_posix()
            return result
        stats['node_count'] += 1
        stats['max_depth'] = max(stats['max_depth'], depth)
        bounds = box_union([t['boundingVolume']['region'] for t in items])
        if compact_group(items, max_files, max_bytes):
            children = [visit([tile], name+f'-d{i}', depth+1) for i, tile in enumerate(items)]
            stats['max_direct_detail_files'] = max(stats['max_direct_detail_files'], len(items))
            stats['max_direct_detail_bytes'] = max(stats['max_direct_detail_bytes'], sum(t['extras']['bytes'] for t in items))
        else:
            children = [visit(group, name+f'-{i}', depth+1)
                        for i, group in enumerate(spatial_groups(items, max_files, max_bytes))]
        features = [f for tile in items for f in representatives[tile['content']['uri']]]
        selected = select_representatives(features, bounds)
        parent = emit_lod(selected, output, work, name)
        if parent:
            stats['lod_files'] += 1
            stats['lod_features'] += parent['extras']['feature_count']
            stats['lod_bytes'] += parent['extras']['bytes']
            bounds = box_union([bounds, parent['boundingVolume']['region']] + [c['boundingVolume']['region'] for c in children])
            inverse = np.linalg.inv(matrix(parent))
            for child in children:
                child['transform'] = (inverse @ matrix(child)).flatten(order='F').tolist()
        else:
            stats['nodes_without_content'] += 1
            parent = {}
            bounds = box_union([bounds] + [c['boundingVolume']['region'] for c in children])
        radius = 6371000.0
        width = (bounds[2]-bounds[0])*radius*math.cos((bounds[1]+bounds[3])/2)
        height = (bounds[3]-bounds[1])*radius
        # Conservative transition schedule, not a measured geometric accuracy.
        # Nested errors stay monotonic even for overlapping compacted detail bounds.
        error = max(24.0, math.hypot(width, height)*.0075,
                    max(c['geometricError'] for c in children)*1.05)
        parent.update(boundingVolume={'region': bounds}, geometricError=error, refine='REPLACE', children=children)
        parent.setdefault('extras', {}).update(hierarchy_level=depth, detailed_descendants=len(items),
                                               geometric_error_policy='spatial-transition-v1')
        stats['max_immediate_content_bytes'] = max(stats['max_immediate_content_bytes'],
                                                  sum(c.get('extras', {}).get('bytes', 0) for c in children))
        if stats['node_count'] % 100 == 0:
            print(json.dumps({'stage': 'hierarchy-lod', 'nodes': stats['node_count'], 'lod_files': stats['lod_files']}), flush=True)
        return parent

    root = visit(tiles, 'korea', 0)
    return root, stats


def verify_tree(root, source_tiles, output):
    sources = {Path(t['source_path']).resolve(): t for t in source_tiles}
    seen, transform_error, nodes, lods = set(), 0.0, 0, 0

    def visit(tile, parent):
        nonlocal transform_error, nodes, lods
        nodes += 1
        absolute = parent @ matrix(tile)
        if tile.get('extras', {}).get('lod_role') == 'detail':
            path = (output / tile['content']['uri']).resolve()
            if path not in sources or path in seen:
                raise ValueError('Detail identity changed or duplicated')
            seen.add(path)
            source = sources[path]
            if tile['extras']['sha256'] != source['extras']['sha256']:
                raise ValueError('Detail hash changed')
            delta = absolute - matrix(source)
            # Translation plus the rotation bound for any point in a 100 km local cube.
            bound = float(np.linalg.norm(delta[:3, 3]) + np.linalg.norm(delta[:3, :3], 2)*math.sqrt(3)*100000)
            transform_error = max(transform_error, bound)
            if bound > 1e-6:
                raise ValueError('Hierarchical transform introduces more than 1 micrometre of error')
        elif tile.get('extras', {}).get('lod_role') == 'representative_subset':
            lods += 1
        bounds = tile['boundingVolume']['region']
        for child in tile.get('children', []):
            b = child['boundingVolume']['region']
            if any(b[i] < bounds[i]-1e-10 for i in (0, 1, 4)) or any(b[i] > bounds[i]+1e-10 for i in (2, 3, 5)):
                raise ValueError('Child is outside parent bounding region')
            if child['geometricError'] > tile['geometricError']:
                raise ValueError('Geometric errors are not monotonic')
            visit(child, absolute)

    visit(root, np.eye(4))
    if seen != set(sources):
        raise ValueError('Hierarchy omitted detail files')
    return {'passed': True, 'nodes': nodes, 'detail_glbs_unchanged': len(seen), 'lod_glbs': lods,
            'detail_count_unchanged': sum(t['extras']['feature_count'] for t in source_tiles),
            'additional_transform_error_bound_m': transform_error,
            'detail_bytes_reencoded': 0, 'detail_coordinate_quantization_added': False,
            'accuracy_notice': 'This measures transform arithmetic only; it does not verify source geometry or estimated heights.'}


def replacement_budget(root, maximum=MAX_DIRECT_BYTES):
    """Include descendants of empty nodes in the traditional REPLACE frontier."""
    maximum_bytes, maximum_files = 0, 0

    def visit(tile):
        nonlocal maximum_bytes, maximum_files
        children = [visit(child) for child in tile.get('children', [])]
        load_bytes = sum(child[0] for child in children)
        load_files = sum(child[1] for child in children)
        maximum_bytes = max(maximum_bytes, load_bytes)
        maximum_files = max(maximum_files, load_files)
        if load_bytes > maximum:
            raise ValueError('REPLACE frontier exceeds the content byte budget')
        if tile.get('content'):
            return tile['extras']['bytes'], 1
        return load_bytes, load_files

    visit(root)
    return {'passed': True, 'maximum_replacement_content_bytes': maximum_bytes,
            'maximum_replacement_content_files': maximum_files, 'byte_budget': maximum,
            'empty_node_descendants_included': True,
            'scope': 'Serialized content bytes in one replacement frontier, not total browser or decoded GPU memory.'}


def run(catalog_path, retile_work):
    started = time.monotonic()
    signature = {'version': VERSION, 'source_catalog_sha256': digest(catalog_path),
                 'max_direct_files': MAX_DIRECT_FILES, 'max_direct_bytes': MAX_DIRECT_BYTES,
                 'max_lod_bytes': MAX_LOD_BYTES, 'max_lod_features': MAX_LOD_FEATURES}
    fingerprint = hashlib.sha256(encoded(signature)).hexdigest()[:16]
    output = PUBLIC/'hierarchy'/fingerprint
    work = LOCAL/'hierarchy'/fingerprint
    immutable_json(work/'signature.json', signature)
    catalog, old_asset, tiles, representatives = load_inputs(catalog_path, retile_work, work)
    plan = hierarchy_plan(tiles)
    if plan['nodes']+1 > MAX_NEW_FILES:
        raise ValueError(f'New hierarchy exceeds file budget: {plan}')
    root, stats = build_tree(tiles, representatives, output, work)
    proof = verify_tree(root, tiles, output)
    immutable_json(work/'replacement-budget-audit.json', replacement_budget(root))
    document = {'asset': {'version': '1.1'}, 'geometricError': root['geometricError']*2, 'root': root,
                'extras': {'height_quality_policy': QUALITY_VERSION, 'detail_count': old_asset['count'],
                           'hierarchy_version': VERSION,
                           'lod_notice': 'Intermediate geometry is a representative source-building subset. Detail retains every eligible source building.'}}
    path = output/'tileset.json'
    immutable_json(path, document)
    if path.stat().st_size >= 24*1024*1024:
        raise ValueError('Hierarchy JSON exceeds static asset safety limit')
    asset = {**old_asset, 'url': '/data/'+path.relative_to(PUBLIC).as_posix(), 'sha256': digest(path),
             'bytes': path.stat().st_size, 'byte_length': path.stat().st_size, 'hierarchy_version': VERSION}
    result = {**catalog, 'assets': [asset if a['id'] == old_asset['id'] else a for a in catalog['assets']]}
    candidate = work/'catalog.json'
    immutable_json(candidate, result)
    proof.update(signature=signature, plan=plan, generated=stats, candidate=str(candidate),
                 candidate_sha256=digest(candidate), new_file_count=stats['lod_files']+1,
                 elapsed_seconds=round(time.monotonic()-started, 2))
    # Timing is run-specific; save a separate report instead of rewriting immutable inputs.
    report = work/f'audit-{proof["candidate_sha256"][:16]}.json'
    if report.exists():
        prior = json.loads(report.read_bytes())
        proof['elapsed_seconds'] = prior['elapsed_seconds']
    immutable_json(report, proof)
    print(json.dumps(proof, ensure_ascii=False), flush=True)
    return candidate, report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=LOCAL/'retile'/'catalog.json')
    parser.add_argument('--retile-work', type=Path, default=LOCAL/'retile'/'b908f45777c95a08')
    args = parser.parse_args()
    run(args.catalog.resolve(), args.retile_work.resolve())
