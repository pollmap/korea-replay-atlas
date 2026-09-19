"""Private, bounded parent-to-parts delta for the generated building GLB profile.

This module never publishes or edits source files. Unaffected feature vertices,
triangles and property values survive byte-for-byte. Only added part geometry is
reframed into the existing detail tile frame. An affected LOD representative is
an explicit stop condition; the current accepted groups need no LOD remeshing.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import shape

from .building_parts_national import Budget, encoded, prepare_candidate_mesh
from .glb_merge import Y_TO_Z, encode_glb
from .hierarchy import matrix, replacement_budget
from .lod_error import feature_centers
from .mesh import frame
from .mesh_metadata_audit import audit_glb, parse_glb

VERSION = 'private-building-parts-delta-1'
ERROR_POLICY = 'incremental-omission-distance-bound-1'
MAX_DETAIL_BYTES = 4 * 1024 ** 2
MAX_ROUNDING_M = .01


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def read_checked(path, expected, expected_bytes=None, cap=8 * 1024 ** 2):
    require(path.stat().st_size <= cap, 'Input exceeds bounded per-file size')
    payload = path.read_bytes()
    require(sha256(payload) == expected, 'Pinned input hash mismatch: ' + str(path))
    require(expected_bytes is None or len(payload) == expected_bytes, 'Pinned input size mismatch')
    return payload


def decode_profile(payload):
    """Strict generated profile; no silent loss of unknown attributes/extensions."""
    doc, binary = parse_glb(payload)
    require(set(doc['extensions']) == {'EXT_structural_metadata'}, 'Unsupported document extension')
    require(doc['scene'] == 0 and doc['scenes'] == [{'nodes': list(range(len(doc['meshes'])))}],
            'Unsupported scene instancing')
    require(doc['nodes'] == [{'mesh': i} for i in range(len(doc['meshes']))],
            'Unsupported internal node transforms')
    metadata = doc['extensions']['EXT_structural_metadata']
    tables = metadata['propertyTables']; used_tables = set(); blocks = []

    def view(index):
        v = doc['bufferViews'][index]
        require(v['buffer'] == 0 and 'byteStride' not in v, 'Unsupported interleaved buffer')
        start = v.get('byteOffset', 0)
        require(start >= 0 and start + v['byteLength'] <= len(binary), 'Buffer outside GLB')
        return binary[start:start + v['byteLength']]

    def array(index, component, kind):
        a = doc['accessors'][index]
        require(a['componentType'] == component and a['type'] == kind and
                not a.get('normalized') and 'sparse' not in a, 'Unsupported generated accessor')
        width = 3 if kind == 'VEC3' else 1
        result = np.frombuffer(view(a['bufferView']), dtype='<f4' if component == 5126 else '<u4',
                               count=a['count'] * width, offset=a.get('byteOffset', 0)).copy()
        return result.reshape(-1, 3) if width == 3 else result

    for mesh in doc['meshes']:
        require(set(mesh) == {'primitives'} and len(mesh['primitives']) == 1,
                'Unsupported mesh profile')
        p = mesh['primitives'][0]
        require(set(p) <= {'attributes', 'indices', 'material', 'extensions', 'mode'} and
                p.get('mode', 4) == 4 and set(p['extensions']) == {'EXT_mesh_features'},
                'Unsupported triangle primitive profile')
        require(set(p['attributes']) == {'POSITION', 'NORMAL', '_FEATURE_ID_0'},
                'Unsupported vertex attributes')
        sets = p['extensions']['EXT_mesh_features']['featureIds']
        require(len(sets) == 1 and sets[0]['attribute'] == 0 and
                set(sets[0]) == {'attribute', 'propertyTable', 'featureCount'}, 'Unsupported feature binding')
        ti = sets[0]['propertyTable']
        require(ti not in used_tables, 'Shared metadata table is outside delta profile')
        used_tables.add(ti); table = tables[ti]; count = table['count']
        require(count > 0 and sets[0]['featureCount'] == count, 'Feature count differs from table')
        schema = metadata['schema']['classes'][table['class']]['properties']
        props = {}
        for key, prop in table['properties'].items():
            definition = schema[key]
            require(not definition.get('array'), 'Array metadata is outside delta profile')
            if definition['type'] == 'STRING':
                require(prop.get('stringOffsetType', 'UINT32') == 'UINT32', 'Unexpected string offsets')
                offsets = np.frombuffer(view(prop['stringOffsets']), dtype='<u4')
                raw = view(prop['values'])
                require(len(offsets) == count + 1 and offsets[0] == 0 and offsets[-1] <= len(raw)
                        and np.all(offsets[1:] >= offsets[:-1]), 'Invalid metadata string offsets')
                props[key] = [bytes(raw[a:b]) for a, b in zip(offsets[:-1], offsets[1:])]
            else:
                require(key == 'height' and definition == {'type': 'SCALAR', 'componentType': 'FLOAT32'},
                        'Unknown numeric metadata cannot be silently dropped')
                props[key] = np.frombuffer(view(prop['values']), dtype='<f4').copy()
                require(len(props[key]) == count, 'Numeric metadata length mismatch')
        a = p['attributes']
        positions, normals = array(a['POSITION'], 5126, 'VEC3'), array(a['NORMAL'], 5126, 'VEC3')
        ids = array(a['_FEATURE_ID_0'], 5126, 'SCALAR')
        indices = array(p['indices'], 5125, 'SCALAR').reshape(-1, 3)
        require(len(ids) == len(positions) == len(normals) and np.isfinite(positions).all()
                and np.isfinite(normals).all(), 'Invalid vertex data')
        require(np.isfinite(ids).all() and np.all(ids == np.floor(ids)) and ids.min() >= 0
                and ids.max() < count and np.all(ids[1:] >= ids[:-1]), 'Invalid or ungrouped feature IDs')
        require(indices.size > 0 and indices.max() < len(ids), 'Invalid triangle indices')
        triangle_ids = ids[indices]
        require(np.all(triangle_ids == triangle_ids[:, :1]) and
                np.all(triangle_ids[1:, 0] >= triangle_ids[:-1, 0]), 'Triangles cross feature boundaries')
        require(set(triangle_ids[:, 0]) == set(range(count)), 'Unused metadata rows')
        blocks.append({'positions': positions, 'normals': normals, 'ids': ids, 'indices': indices,
                       'props': props, 'class': table['class'], 'material': p['material']})
    require(used_tables == set(range(len(tables))), 'Unused metadata table')
    base = {k: copy.deepcopy(v) for k, v in doc.items() if k not in
            {'buffers', 'bufferViews', 'accessors', 'meshes', 'nodes', 'scenes', 'extensions'}}
    base['schema'] = copy.deepcopy(metadata['schema'])
    return base, blocks


def identities(blocks):
    return [value.decode('utf-8') for block in blocks for value in block['props']['source_record_id']]


def encode_profile(base, blocks):
    require(bool(blocks), 'Cannot produce an empty GLB')
    doc = copy.deepcopy(base); schema = doc.pop('schema')
    doc.update(bufferViews=[], accessors=[], meshes=[], nodes=[], scenes=[{'nodes': []}])
    tables = []; binary = bytearray()
    doc['extensions'] = {'EXT_structural_metadata': {'schema': schema, 'propertyTables': tables}}

    def view(payload, target=None):
        binary.extend(b'\0' * (-len(binary) % 4)); payload = payload or b'\0'
        v = {'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(payload)}
        if target is not None: v['target'] = target
        doc['bufferViews'].append(v); binary.extend(payload)
        return len(doc['bufferViews']) - 1

    def accessor(values, component, kind, target):
        a = {'bufferView': view(values.tobytes(), target), 'componentType': component,
             'count': len(values), 'type': kind}
        if kind == 'VEC3': a.update(min=values.min(axis=0).tolist(), max=values.max(axis=0).tolist())
        doc['accessors'].append(a)
        return len(doc['accessors']) - 1

    for block in blocks:
        n = len(block['props']['source_record_id']); table = {}
        attrs = {name: accessor(block[key], 5126, kind, 34962) for name, key, kind in
                 [('POSITION', 'positions', 'VEC3'), ('NORMAL', 'normals', 'VEC3'), ('_FEATURE_ID_0', 'ids', 'SCALAR')]}
        index = accessor(block['indices'].ravel(), 5125, 'SCALAR', 34963)
        for key, values in block['props'].items():
            if isinstance(values, list):
                offsets = np.asarray([0] + list(np.cumsum([len(v) for v in values])), dtype='<u4')
                table[key] = {'values': view(b''.join(values)), 'stringOffsets': view(offsets.tobytes()),
                              'stringOffsetType': 'UINT32'}
            else:
                table[key] = {'values': view(values.tobytes())}
        ti = len(tables); tables.append({'class': block['class'], 'count': n, 'properties': table})
        doc['meshes'].append({'primitives': [{'attributes': attrs, 'indices': index, 'material': block['material'],
            'extensions': {'EXT_mesh_features': {'featureIds': [{'featureCount': n, 'attribute': 0, 'propertyTable': ti}]}}}]})
        doc['nodes'].append({'mesh': ti}); doc['scenes'][0]['nodes'].append(ti)
    return encode_glb(doc, binary)


def feature_fingerprints(blocks):
    """Per source ID: unchanged local geometry, topology and exact property bytes."""
    result = {}
    for b in blocks:
        ids = b['ids']; tri_ids = ids[b['indices'][:, 0]]
        for row, raw_id in enumerate(b['props']['source_record_id']):
            key = raw_id.decode(); require(key not in result, 'Duplicate source record ID')
            start, stop = np.searchsorted(ids, row), np.searchsorted(ids, row, side='right')
            first, last = np.searchsorted(tri_ids, row), np.searchsorted(tri_ids, row, side='right')
            h = hashlib.sha256()
            for payload in (b['positions'][start:stop].tobytes(), b['normals'][start:stop].tobytes(),
                            (b['indices'][first:last] - start).astype('<u4').tobytes()):
                h.update(len(payload).to_bytes(8, 'little')); h.update(payload)
            for name, values in sorted(b['props'].items()):
                raw = values[row] if isinstance(values, list) else values[row:row + 1].tobytes()
                h.update(name.encode() + b'\0' + len(raw).to_bytes(8, 'little') + raw)
            result[key] = h.hexdigest()
    return result


def remove_features(blocks, remove_ids):
    result = []
    for b in blocks:
        keep = np.asarray([v.decode() not in remove_ids for v in b['props']['source_record_id']])
        if not keep.any(): continue
        rows = np.flatnonzero(keep); row_map = np.cumsum(keep) - 1
        vertices = keep[b['ids'].astype(np.intp)]; vertex_map = np.cumsum(vertices) - 1
        triangles = vertices[b['indices'][:, 0]]
        result.append({**b, 'positions': b['positions'][vertices], 'normals': b['normals'][vertices],
            'ids': row_map[b['ids'][vertices].astype(np.intp)].astype('<f4'),
            'indices': vertex_map[b['indices'][triangles]].astype('<u4'),
            'props': {key: [values[i] for i in rows] if isinstance(values, list) else values[rows]
                      for key, values in b['props'].items()}})
    return result


def reframe_blocks(blocks, source_world, target_world):
    change = Y_TO_Z.T @ np.linalg.inv(target_world) @ source_world @ Y_TO_Z
    require(np.allclose(change[:3, :3].T @ change[:3, :3], np.eye(3), atol=1e-9),
            'Only rigid tile frames are supported')
    result = []; error = 0.
    for b in blocks:
        positions = b['positions'].astype(np.float64) @ change[:3, :3].T + change[:3, 3]
        rounded = positions.astype('<f4')
        error = max(error, float(np.linalg.norm(positions - rounded.astype(np.float64), axis=1).max()))
        result.append({**b, 'positions': rounded,
                       'normals': (b['normals'].astype(np.float64) @ change[:3, :3].T).astype('<f4'),
                       'props': {**b['props'], 'lod_role': [b'detail'] * len(b['props']['source_record_id'])}})
    require(error <= MAX_ROUNDING_M, 'Part reframing exceeds 1 cm additional rounding')
    return result, error


def combine_blocks(blocks):
    """One added primitive per changed tile, rather than one per parent."""
    require(bool(blocks), 'No parts to append')
    first = blocks[0]; vertices = 0; rows = 0; ids = []; indices = []
    for b in blocks:
        require(b['class'] == first['class'] and b['material'] == first['material']
                and b['props'].keys() == first['props'].keys(), 'Incompatible part blocks')
        ids.append(b['ids'] + rows); indices.append(b['indices'] + vertices)
        vertices += len(b['ids']); rows += len(b['props']['source_record_id'])
    return {**first, 'positions': np.concatenate([b['positions'] for b in blocks]),
        'normals': np.concatenate([b['normals'] for b in blocks]), 'ids': np.concatenate(ids),
        'indices': np.concatenate(indices), 'props': {
            key: [v for b in blocks for v in b['props'][key]] if isinstance(values, list)
            else np.concatenate([b['props'][key] for b in blocks]) for key, values in first['props'].items()}}


def patch_detail(payload, remove_ids, additions, target_world, expected_part_ids):
    """Pure deterministic conversion. Each addition is (GLB bytes, world frame)."""
    base, old = decode_profile(payload); before = feature_fingerprints(old)
    require(set(remove_ids) <= before.keys(), 'Replacement parent is absent from detail GLB')
    require(len(expected_part_ids) == len(set(expected_part_ids)) and
            not (set(expected_part_ids) & before.keys()), 'Added part identity already exists or is duplicated')
    parts = []; error = 0.
    for part_payload, part_world in additions:
        part_base, blocks = decode_profile(part_payload)
        require(part_base == base, 'Part and detail schemas/materials are incompatible')
        moved, rounding = reframe_blocks(blocks, part_world, target_world)
        parts.extend(moved); error = max(error, rounding)
    require(Counter(identities(parts)) == Counter(expected_part_ids), 'Part mesh identity set differs from plan')
    new = remove_features(old, set(remove_ids)) + [combine_blocks(parts)]
    result = encode_profile(base, new)
    require(len(result) <= MAX_DETAIL_BYTES, 'Changed detailed GLB exceeds 4 MiB; explicit splitting is required')
    _, decoded = decode_profile(result); after = feature_fingerprints(decoded)
    remaining = before.keys() - set(remove_ids)
    require(after.keys() == remaining | set(expected_part_ids), 'Replacement feature set is not exact')
    require(all(before[k] == after[k] for k in remaining), 'Unrelated feature geometry/metadata changed')
    return result, {'removed_parent_ids': sorted(remove_ids), 'added_part_ids': sorted(expected_part_ids),
        'unchanged_features': len(remaining), 'unaffected_features_bit_exact': True,
        'unaffected_coordinate_conversion_error_m': 0., 'added_coordinate_rounding_max_m': error,
        'before_features': len(before), 'after_features': len(after),
        'new_primitive_count': len(decoded), 'old_primitive_count': len(old),
        'unaffected_feature_digest': sha256(encoded({k: before[k] for k in sorted(remaining)}))}


def validate_plan(plan, features):
    require(plan.get('global_sibling_scan_complete') is True and plan.get('parent_height_review_complete') is True,
            'Complete global sibling and source height reviews are required')
    require(bool(plan.get('groups')), 'No verified replacement groups')
    prepared = prepare_candidate_mesh(features, plan)
    ids = [f['id'] for f in prepared]
    require(len(ids) == len(set(ids)), 'Duplicated part source IDs')
    for g in plan['groups']:
        require(g.get('global_sibling_count_verified') is True and
                g.get('global_sibling_count') == g.get('observed_sibling_count') == len(g['part_source_ids']) and
                len(g['part_source_ids']) == len(set(g['part_source_ids'])), 'Incomplete global part group')
        require(g.get('parent_height_consistency') in {'source_values_equal', 'source_parent_absent'},
                'Conflicting parent and part source heights')
        require(len(g.get('published_tiles', [])) == 1 and g['published_tiles'][0]['occurrences'] == 1,
                'Parent must occur exactly once in the pinned published details')
    require(not (set(ids) & set(plan['replace_parent_ids'])), 'Parent and part source identity collision')
    return prepared


def union_region(*regions):
    a = np.asarray(regions)
    return [float(a[:, i].min() if i in (0, 1, 4) else a[:, i].max()) for i in range(6)]


def incremental_error(previous, baseline_distance, points, representatives, child_errors):
    require(len(representatives) > 0, 'Changed descendants have no retained LOD representatives')
    maximum = float(cKDTree(representatives).query(points, workers=1)[0].max()) if len(points) else 0.
    bound = max(baseline_distance, maximum)
    return max(previous, bound, *child_errors), bound, maximum


def verify_tree_delta(before, after, source_path, output_path, changed):
    """Independent structural check: topology, placement and unrelated references."""
    stats = {'nodes': 0, 'changed_contents': 0, 'reused_contents': 0, 'expanded_regions': 0}
    def visit(old, new):
        stats['nodes'] += 1
        require(old.get('transform') == new.get('transform'), 'Tile transform changed')
        require(old.get('refine') == new.get('refine'), 'Refinement mode changed')
        require(len(old.get('children', [])) == len(new.get('children', [])), 'Hierarchy topology changed')
        require(new['geometricError'] >= old['geometricError'], 'Geometric error decreased')
        a, b = old['boundingVolume']['region'], new['boundingVolume']['region']
        require(union_region(a, b) == b, 'New bounds shrink the prior content region')
        stats['expanded_regions'] += a != b
        if old.get('content'):
            old_path = (source_path.parent / old['content']['uri']).resolve()
            new_path = (output_path.parent / new['content']['uri']).resolve()
            if old_path in changed:
                require(new_path == changed[old_path], 'Changed detail points to unexpected file')
                stats['changed_contents'] += 1
            else:
                require(old_path == new_path and old['extras'] == new['extras'] or
                        (old_path == new_path and old['extras']['sha256'] == new['extras']['sha256']
                         and old['extras']['bytes'] == new['extras']['bytes']
                         and old['extras']['feature_count'] == new['extras']['feature_count']),
                        'Reused GLB reference, metadata count or hash changed')
                stats['reused_contents'] += 1
        else:
            require(not new.get('content'), 'Unexpected new content')
        for a_child, b_child in zip(old.get('children', []), new.get('children', [])):
            visit(a_child, b_child)
    visit(before['root'], after['root'])
    require(stats['changed_contents'] == len(changed), 'Not every patch occurs once in the hierarchy')
    return {'passed': True, 'transforms_bit_exact': True, **stats}


def index_tree(document, source_path, public):
    nodes = {}; paths = {}
    def visit(node, parent, name, ancestors):
        world = parent @ matrix(node)
        row = {'node': node, 'world': world, 'ancestors': ancestors, 'name': name}
        nodes[name] = row
        if node.get('content'):
            uri = node['content']['uri']
            require(not any(c in uri for c in ('?', '#', ':', '\\')), 'Unexpected content URI')
            path = (source_path.parent / uri).resolve()
            require(path.is_relative_to(public) and path.suffix == '.glb' and path not in paths,
                    'Expected unique existing public GLB references')
            row['path'] = path; paths[path] = row
        require('contents' not in node, 'Multiple contents are unsupported')
        for i, child in enumerate(node.get('children', [])):
            visit(child, world, name + '/' + str(i), ancestors + [name])
    visit(document['root'], np.eye(4), 'root', [])
    return nodes, paths


def private_delta(candidate_folder, catalog_path, output_parent, *, repo, disk_reserve=30 * 1024 ** 3):
    """Create an immutable private delta; reuse existing GLBs by relative reference."""
    repo = Path(repo).resolve(); candidate_folder = Path(candidate_folder).resolve()
    catalog_path = Path(catalog_path).resolve(); public = repo / 'public/data'
    output_parent = Path(output_parent).resolve()
    require(output_parent.is_relative_to(repo / '.local') and not output_parent.is_relative_to(candidate_folder),
            'Only a separate private .local output folder is allowed')
    names = ['final/replacement-plan.json', 'final/candidate-parts.geojson', 'final/mesh-audit.json',
             'references/published-parent-root.json']
    inputs = {name: (candidate_folder / name).read_bytes() for name in names}
    plan, collection, mesh_audit, reference = [json.loads(inputs[name]) for name in names]
    prepared = validate_plan(plan, collection['features'])
    catalog_bytes = catalog_path.read_bytes(); catalog = json.loads(catalog_bytes)
    require(reference.get('verified') and catalog['release_id'] == reference['public_release_id'] ==
            reference['local_release_id'] and sha256(catalog_bytes) == reference['local_catalog_sha256'] ==
            reference['public_catalog_sha256'], 'Current catalog differs from verified parent publication')
    assets = [a for a in catalog['assets'] if a['id'] == reference['building_root']['id']]
    require(len(assets) == 1 and assets[0] == reference['building_root'], 'Published hierarchy descriptor changed')
    asset = assets[0]; source_path = (repo / 'public' / asset['url'].lstrip('/')).resolve()
    require(source_path.is_relative_to(public), 'Root escaped public data')
    source_bytes = read_checked(source_path, asset['sha256'], asset['byte_length'])
    source = json.loads(source_bytes); result = copy.deepcopy(source)
    nodes, paths = index_tree(result, source_path, public)
    require(mesh_audit.get('private_meshes') and mesh_audit.get('glb_id_original_properties_equal') and
            mesh_audit.get('raw_id_geometry_attributes_equal') and
            mesh_audit['plan_sha256'] == sha256(inputs['final/replacement-plan.json']) and
            mesh_audit['candidate_geojson_sha256'] == sha256(inputs['final/candidate-parts.geojson']),
            'Private mesh audit does not match accepted source parts')
    signature = {'version': VERSION, 'catalog_sha256': sha256(catalog_bytes), 'root_sha256': sha256(source_bytes),
                 'inputs': {k: sha256(v) for k, v in inputs.items()}}
    output = output_parent / sha256(encoded(signature))[:20]
    budget = Budget(output, network_limit=0, disk_reserve=disk_reserve)
    budget.json('inputs.json', signature)
    meshes = {r['building_id']: r for r in mesh_audit['meshes']}
    features = defaultdict(list)
    for f in prepared: features[f['properties']['parent_source_record_id']].append(f)
    grouped = defaultdict(list)
    for g in plan['groups']:
        path = (repo / g['published_tiles'][0]['path']).resolve()
        require(path in paths and paths[path]['node']['extras']['lod_role'] == 'detail',
                'Verified parent tile is absent from current hierarchy')
        require(not paths[path]['node'].get('children'), 'Replacement target must be a detailed leaf')
        grouped[path].append(g)
    affected = {name for path in grouped for name in paths[path]['ancestors']}
    old_parent_ids = set(plan['replace_parent_ids']); representatives = {}; lod_rows = []
    for name in sorted(affected, key=lambda n: (n.count('/'), n)):
        row = nodes[name]; node = row['node']
        if row.get('path'):
            require(node['extras']['lod_role'] == 'representative_subset', 'Unknown intermediate content role')
            raw = read_checked(row['path'], node['extras']['sha256'], node['extras']['bytes'])
            centers, ids = feature_centers(raw, row['world'])
            require(not (set(ids) & old_parent_ids), 'Replaced parent is an LOD representative; separate LOD remeshing required')
            representatives[name] = (centers, name)
            lod_rows.append({'path': row['path'].relative_to(repo).as_posix(), 'sha256': sha256(raw), 'bytes': len(raw)})
        else:
            nearest = next((n for n in reversed(row['ancestors']) if n in representatives), None)
            if nearest: representatives[name] = representatives[nearest]
        budget.check()
    patches = []; new_points = defaultdict(list); source_hashed_bytes = sum(r['bytes'] for r in lod_rows)
    for path, groups in sorted(grouped.items()):
        budget.check(allocation=64 * 1024 ** 2)
        row = paths[path]; tile = row['node']; source_raw = read_checked(path, tile['extras']['sha256'], tile['extras']['bytes'])
        additions = []; part_regions = []; input_rounding = 0.; part_mesh_rows = []
        for g in sorted(groups, key=lambda v: v['building_id']):
            p = g['published_tiles'][0]
            require(p['sha256'] == sha256(source_raw) and p['bytes'] == len(source_raw), 'Plan detail hash mismatch')
            m = meshes[g['building_id']]; part_path = (candidate_folder / m['path']).resolve()
            require(part_path.is_relative_to(candidate_folder / 'final/meshes'), 'Part mesh escaped final candidate')
            raw = read_checked(part_path, m['sha256'], m['bytes'])
            fs = features[g['building_id']]; b = np.asarray([shape(f['geometry']).bounds for f in fs])
            bounds = [b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()]
            _, _, transform = frame((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
            additions.append((raw, np.asarray(transform).reshape(4, 4, order='F')))
            low = min(f['properties']['base_height'] + f['properties']['min_height'] for f in fs)
            high = max(f['properties']['base_height'] + f['properties']['height'] for f in fs)
            part_regions.append([math.radians(v) for v in bounds] + [low - 1, high + 1])
            input_rounding = max(input_rounding, m['coordinate_rounding_max_m'])
            part_mesh_rows.append({'path': part_path.relative_to(repo).as_posix(), 'sha256': sha256(raw), 'bytes': len(raw)})
        expected = [i for g in groups for i in g['part_source_ids']]
        output_raw, proof = patch_detail(source_raw, {g['building_id'] for g in groups}, additions, row['world'], expected)
        output_sha = sha256(output_raw); target = budget.write_once('glb/' + output_sha[:32] + '.glb', output_raw)
        metadata = audit_glb(target); centers, ids = feature_centers(output_raw, row['world'])
        selected = centers[np.asarray([i in set(expected) for i in ids])]
        for ancestor in row['ancestors']: new_points[ancestor].append(selected)
        tile['content']['uri'] = 'glb/' + target.name
        tile['boundingVolume']['region'] = union_region(tile['boundingVolume']['region'], *part_regions)
        tile['extras'].update(sha256=output_sha, bytes=len(output_raw), feature_count=metadata['features'],
            vertex_count=metadata['vertices'], parent_parts_delta=VERSION,
            coordinate_rounding_max_m=max(tile['extras'].get('coordinate_rounding_max_m', 0),
                                         input_rounding + proof['added_coordinate_rounding_max_m']))
        patches.append({'source_path': path.relative_to(repo).as_posix(), 'source_sha256': sha256(source_raw),
            'source_bytes': len(source_raw), 'path': target.relative_to(output).as_posix(), 'sha256': output_sha,
            'bytes': len(output_raw), 'metadata_audit': metadata, 'part_inputs': part_mesh_rows,
            'part_total_coordinate_rounding_bound_m': input_rounding + proof['added_coordinate_rounding_max_m'], **proof})
        source_hashed_bytes += len(source_raw) + sum(r['bytes'] for r in part_mesh_rows)
        budget.json('checkpoints/' + output_sha[:32] + '.json', patches[-1]); budget.check()
    transition_rows = []
    for name in sorted(affected, key=lambda n: (-n.count('/'), n)):
        row = nodes[name]; node = row['node']; extras = node.setdefault('extras', {})
        require(name in representatives, 'Missing displayed ancestor representative set')
        baseline = extras.pop('omission_center_max_distance_m', None)
        require(baseline is not None, 'Source has no measured omission policy')
        points = np.concatenate(new_points[name]); displayed, owner = representatives[name]
        previous = node['geometricError']
        error, bound, added = incremental_error(previous, baseline, points, displayed,
                                               [c['geometricError'] for c in node['children']])
        node['geometricError'] = error
        node['boundingVolume']['region'] = union_region(node['boundingVolume']['region'],
                                                       *[c['boundingVolume']['region'] for c in node['children']])
        extras.update(geometric_error_policy=ERROR_POLICY, baseline_omission_center_max_distance_m=baseline,
                      omission_center_distance_upper_bound_m=bound, added_parts_center_max_distance_m=added)
        transition_rows.append({'node': name, 'representative_node': owner, 'previous_error_m': previous,
            'geometric_error_m': error, 'baseline_omission_center_max_distance_m': baseline,
            'omission_center_distance_upper_bound_m': bound, 'added_parts_center_max_distance_m': added})
    reuse = []; changed_paths = set(grouped)
    for path, row in paths.items():
        if path in changed_paths: continue
        row['node']['content']['uri'] = Path(os.path.relpath(path, output)).as_posix()
        reuse.append({'path': path.relative_to(repo).as_posix(), 'sha256': row['node']['extras']['sha256'],
                     'bytes': row['node']['extras']['bytes'], 'role': row['node']['extras']['lod_role']})
    delta = len(prepared) - len(plan['groups']); old_count = asset['count']
    result['geometricError'] = max(result['geometricError'], result['root']['geometricError'] * 2)
    result.setdefault('extras', {}).update(detail_count=old_count + delta, logical_source_building_count=old_count,
        private_candidate=True, public_assets_changed=False, parent_parts_delta=VERSION,
        geometric_error_policy=ERROR_POLICY, parent_count_removed=len(plan['groups']), part_count_added=len(prepared),
        geometric_error_metric={'name': 'conservative_horizontal_feature_center_spacing_bound', 'units': 'm',
            'coordinate_reference_system': 'EPSG:5179', 'baseline_policy': 'omitted-feature-center-spacing-1',
            'method': 'max(previous omission distance, added part center distance); unchanged representative GLBs',
            'limitation': 'A conservative refinement bound, not an exact new maximum or surface/real-world accuracy.'})
    detail_total = sum(r['node']['extras']['feature_count'] for r in paths.values()
                       if r['node']['extras']['lod_role'] == 'detail')
    require(detail_total == old_count + delta, 'Hierarchy detail count does not match complete replacement')
    frontier = replacement_budget(result['root'])
    require(frontier['maximum_replacement_content_files'] <= 8, 'Replacement frontier exceeds eight files')
    structure = verify_tree_delta(source, result, source_path, output / 'tileset.json',
        {repo / p['source_path']: output / p['path'] for p in patches})
    tree_bytes = encoded(result); budget.write_once('tileset.json', tree_bytes)
    budget.json('reuse.json', sorted(reuse, key=lambda r: r['path']))
    report = {'version': VERSION, 'passed': True, 'private_candidate': True, 'public_assets_changed': False,
        'source_release_id': catalog['release_id'], 'source_root_sha256': sha256(source_bytes),
        'tileset_sha256': sha256(tree_bytes), 'changed_detail_glbs': len(patches), 'unchanged_glb_references': len(reuse),
        'unchanged_lod_glbs': sum(r['role'] == 'representative_subset' for r in reuse),
        'removed_parents': len(plan['groups']), 'added_parts': len(prepared), 'logical_source_building_count': old_count,
        'detailed_render_features': detail_total, 'additional_render_features': delta,
        'unchanged_tile_transforms': True, 'unaffected_features_bit_exact': True,
        'new_glb_bytes': sum(p['bytes'] for p in patches), 'source_glb_bytes_hashed': source_hashed_bytes,
        'part_total_coordinate_rounding_bound_m': max(p['part_total_coordinate_rounding_bound_m'] for p in patches),
        'replacement_frontier': frontier, 'structure': structure, 'patches': patches, 'unchanged_ancestor_lods_checked': lod_rows,
        'transition_nodes': transition_rows,
        'limitations': ['Private local candidate; no publication, browser or public integration validation performed.',
            'Unchanged nationwide GLB hashes are inherited from the pinned hierarchy; only affected detail, ancestor LOD and part GLBs were reread.',
            'Parent uniqueness relies on the pinned spatial parent audit plus exact changed-tile metadata checks.',
            'Additional coordinate rounding is not absolute building/terrain accuracy.',
            'Parts are the accepted flat-roof extrusion subset; roof shapes and unresolved groups are unchanged.']}
    budget.json('audit.json', report)
    resource = {'artifact_bytes_before_resource_report': budget.check(), 'process_peak_bytes': budget.peak_memory,
                'network_bytes': 0, 'storage_limit': budget.storage_limit, 'memory_limit': budget.memory_limit,
                'disk_reserve': disk_reserve}
    if not (output / 'resources.json').exists(): budget.json('resources.json', resource)
    return output, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, default=Path('public/data/catalog.json'))
    parser.add_argument('--output', type=Path, default=Path('.local/building-parts-delta'))
    args = parser.parse_args()
    output, report = private_delta(args.candidate, args.catalog, args.output, repo=Path.cwd())
    print(json.dumps({'output': str(output), 'passed': report['passed'], 'changed_detail_glbs': report['changed_detail_glbs'],
                      'removed_parents': report['removed_parents'], 'added_parts': report['added_parts'],
                      'new_glb_bytes': report['new_glb_bytes'], 'tileset_sha256': report['tileset_sha256']}))


if __name__ == '__main__':
    main()
