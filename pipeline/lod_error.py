"""Republish only hierarchy JSON using measured representative omission spacing.

The metric is the horizontal EPSG:5179 distance from an omitted detail mesh's
feature AABB center to the nearest retained representative mesh AABB center.
It is a conservative display transition policy, not surface or source accuracy.
Existing geometry, metadata, bounding regions and transforms remain identical.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree

from .core import LOCAL, PUBLIC, digest
from .geometry_budget import verify_geometry_budgets
from .hierarchy import immutable_json, matrix, replacement_budget
from .mesh_metadata_audit import canonical_record, parse_glb
from .retile import encoded

VERSION = 'omitted-feature-center-spacing-1'
METRIC = {'name': 'omitted_detail_to_retained_representative_center_max_distance',
          'units': 'm', 'coordinate_reference_system': 'EPSG:5179',
          'input_frame': 'glTF per-feature local AABB center -> tile Z-up -> ECEF EPSG:4978',
          'exclusion': 'Detailed rows whose source_record_id is retained in the displayed LOD are excluded.',
          'empty_node_policy': 'Use nearest ancestor with renderable representative content.',
          'error_policy': 'max(previous error, measured horizontal omission distance, child errors)',
          'limitation': 'Feature-center spacing controls refinement; it is not a surface Hausdorff bound or real-world accuracy.'}


def _values(document, binary, index):
    accessor = document['accessors'][index]
    view = document['bufferViews'][accessor['bufferView']]
    if 'sparse' in accessor or accessor.get('normalized', False):
        raise ValueError('Unexpected sparse or normalized mesh accessor')
    dtype = np.dtype({5126: '<f4', 5125: '<u4', 5123: '<u2', 5121: 'u1'}[accessor['componentType']])
    width = {'SCALAR': 1, 'VEC3': 3}[accessor['type']]
    return np.ndarray((accessor['count'], width), dtype=dtype, buffer=binary,
                      offset=view.get('byteOffset', 0)+accessor.get('byteOffset', 0),
                      strides=(view.get('byteStride', dtype.itemsize*width), dtype.itemsize))


def feature_centers(payload, world, projection=None):
    """Read every metadata table and primitive without modifying GLB bytes."""
    document, binary = parse_glb(payload)
    for node in document.get('nodes', []):
        if any(key in node for key in ('translation', 'rotation', 'scale', 'matrix', 'children')):
            raise ValueError('Unexpected internal glTF transform; center policy needs an explicit scene traversal')
    if sorted(node['mesh'] for node in document['nodes']) != list(range(len(document['meshes']))):
        raise ValueError('Expected each GLB mesh to occur exactly once')
    tables = document['extensions']['EXT_structural_metadata']['propertyTables']
    low = [np.full((t['count'], 3), np.inf) for t in tables]
    high = [np.full((t['count'], 3), -np.inf) for t in tables]
    for mesh in document['meshes']:
        for primitive in mesh['primitives']:
            table = primitive['extensions']['EXT_mesh_features']['featureIds'][0]['propertyTable']
            attrs = primitive['attributes']
            positions = _values(document, binary, attrs['POSITION'])
            feature_ids = _values(document, binary, attrs['_FEATURE_ID_0'])[:, 0].astype(np.intp)
            np.minimum.at(low[table], feature_ids, positions)
            np.maximum.at(high[table], feature_ids, positions)
    local = np.concatenate([(a+b)/2 for a, b in zip(low, high)])
    if not np.isfinite(local).all():
        raise ValueError('GLB metadata row has no finite geometric center')
    identities = []
    for table in tables:
        prop = table['properties']['source_record_id']
        def view(index):
            v = document['bufferViews'][index]
            return binary[v.get('byteOffset', 0):v.get('byteOffset', 0)+v['byteLength']]
        offset_type = {'UINT8': 'u1', 'UINT16': '<u2', 'UINT32': '<u4', 'UINT64': '<u8'}[prop.get('stringOffsetType', 'UINT32')]
        offsets = np.frombuffer(view(prop['stringOffsets']), dtype=offset_type)
        raw = view(prop['values'])
        values = [bytes(raw[a:b]).decode() for a, b in zip(offsets[:-1], offsets[1:])]
        if len(values) != table['count'] or any(not value for value in values):
            raise ValueError('Source identity count differs from feature centers')
        identities.extend(values)
    zup = local[:, [0, 2, 1]].copy()
    zup[:, 1] *= -1
    ecef = zup @ world[:3, :3].T + world[:3, 3]
    projection = projection or Transformer.from_crs(4978, 5179, always_xy=True)
    x, y, _ = projection.transform(ecef[:, 0], ecef[:, 1], ecef[:, 2])
    points = np.column_stack([x, y])
    if not np.isfinite(points).all():
        raise ValueError('Projected feature centers are non-finite')
    return points, identities


def omission_metric(points, identities, representatives, representative_ids):
    """Measure only omitted identities against the actually displayed centers."""
    retained = set(representative_ids)
    omitted = np.fromiter((identity not in retained for identity in identities), dtype=bool, count=len(identities))
    count = int(omitted.sum())
    if not count:
        return {'omitted_features': 0, 'retained_detail_features': len(identities), 'maximum_m': 0., 'p95_m': 0.}
    if not len(representatives):
        raise ValueError('Omitted detail has no displayed representative centers')
    distances = cKDTree(representatives).query(points[omitted], workers=1)[0]
    return {'omitted_features': count, 'retained_detail_features': len(identities)-count,
            'maximum_m': float(distances.max()), 'p95_m': float(np.percentile(distances, 95))}


def update_tree(source, source_path, target_path, public=PUBLIC):
    """Return a new tree, audit and hash inventory; retain every old GLB path."""
    result = copy.deepcopy(source)
    projection = Transformer.from_crs(4978, 5179, always_xy=True)
    rows, inventory, seen = [], [], set()
    public = public.resolve()

    def visit(tile, parent, name, fallback=None):
        world = parent @ matrix(tile)
        role = tile.get('extras', {}).get('lod_role')
        own = None
        if tile.get('content'):
            path = (source_path.parent/tile['content']['uri']).resolve()
            if not path.is_relative_to(public) or path.suffix != '.glb' or path in seen:
                raise ValueError('Expected a unique, in-public GLB reference')
            seen.add(path)
            payload = path.read_bytes()
            sha = hashlib.sha256(payload).hexdigest()
            if sha != tile['extras']['sha256'] or len(payload) != tile['extras']['bytes']:
                raise ValueError('Immutable GLB changed: '+str(path))
            points, identities = feature_centers(payload, world, projection)
            if len(identities) != tile['extras']['feature_count']:
                raise ValueError('GLB feature count differs from the hierarchy')
            inventory.append({'url': '/data/'+path.relative_to(public).as_posix(), 'sha256': sha,
                              'byte_length': len(payload), 'feature_count': len(identities), 'role': role})
            tile['content']['uri'] = Path(os.path.relpath(path, target_path.parent)).as_posix()
            if role == 'detail':
                if tile.get('children') or tile['geometricError'] != 0:
                    raise ValueError('Expected a zero-error detailed leaf')
                return points, identities
            if role != 'representative_subset':
                raise ValueError('Unknown hierarchy content role')
            own = (points, identities, name)
        displayed = own or fallback
        children = [visit(child, world, name+'/'+str(i), displayed)
                    for i, child in enumerate(tile.get('children', []))]
        if not children:
            raise ValueError('Intermediate node has no detailed descendants')
        points = np.concatenate([value[0] for value in children])
        identities = [identity for value in children for identity in value[1]]
        previous = tile['geometricError']
        measured = omission_metric(points, identities, displayed[0], displayed[1]) if displayed else {
            'omitted_features': len(identities), 'retained_detail_features': 0, 'maximum_m': 0., 'p95_m': 0.}
        if own and measured['retained_detail_features'] != len(set(own[1])):
            raise ValueError('Representative identities are absent or duplicated in detailed descendants')
        child_error = max(child['geometricError'] for child in tile['children'])
        error = max(previous, measured['maximum_m'], child_error)
        tile['geometricError'] = error
        tile.setdefault('extras', {}).update(geometric_error_policy=VERSION,
            omission_center_max_distance_m=measured['maximum_m'])
        rows.append({'node': name, 'uri': tile.get('content', {}).get('uri'),
                     'bbox_radians': tile['boundingVolume']['region'], 'detail_features': len(identities),
                     'representative_features': len(displayed[1]) if displayed else 0,
                     'representative_node': displayed[2] if displayed else None,
                     'fallback_from_ancestor': bool(displayed and not own),
                     'previous_error_m': previous, 'geometric_error_m': error, **measured})
        if len(rows) % 100 == 0:
            print(json.dumps({'stage': 'lod-error', 'intermediate_nodes': len(rows), 'glbs_read': len(inventory)}), flush=True)
        return points, identities

    points, identities = visit(result['root'], np.eye(4), 'root')
    result['geometricError'] = max(source.get('geometricError', 0), result['root']['geometricError']*2)
    result.setdefault('extras', {}).update(geometric_error_policy=VERSION, geometric_error_metric=METRIC)
    proof = {'passed': True, 'metric': METRIC, 'intermediate_nodes': len(rows),
             'detail_features': len(identities), 'new_glbs': 0,
             'source_glbs_hashed': len(inventory), 'nodes': rows}
    return result, proof, sorted(inventory, key=lambda row: row['url'])


def verify_reuse(old, new, source_path, target_path):
    """Structural equality independently proves no geometry placement changes."""
    stats = {'passed': True, 'nodes_checked': 0, 'glb_references_unchanged': 0,
             'transforms_unchanged': True, 'bounds_unchanged': True,
             'additional_coordinate_transform_error_m': 0., 'new_glbs': 0}
    def visit(a, b):
        stats['nodes_checked'] += 1
        omitted = {'geometricError', 'extras', 'children', 'content'}
        if {k:v for k,v in a.items() if k not in omitted} != {k:v for k,v in b.items() if k not in omitted}:
            raise ValueError('Non-policy hierarchy fields changed')
        allowed_extras = {'geometric_error_policy', 'omission_center_max_distance_m'}
        if {k:v for k,v in a.get('extras', {}).items() if k not in allowed_extras} != {
                k:v for k,v in b.get('extras', {}).items() if k not in allowed_extras}:
            raise ValueError('Non-policy content metadata changed')
        if bool(a.get('content')) != bool(b.get('content')):
            raise ValueError('Hierarchy content presence changed')
        if a.get('content'):
            if (source_path.parent/a['content']['uri']).resolve() != (target_path.parent/b['content']['uri']).resolve():
                raise ValueError('Existing GLB path changed')
            if {k:v for k,v in a['content'].items() if k != 'uri'} != {k:v for k,v in b['content'].items() if k != 'uri'}:
                raise ValueError('Content options changed')
            stats['glb_references_unchanged'] += 1
        if b['geometricError'] < a['geometricError'] or any(c['geometricError'] > b['geometricError'] for c in b.get('children', [])):
            raise ValueError('Geometric error decreased or is not parent-monotonic')
        if len(a.get('children', [])) != len(b.get('children', [])):
            raise ValueError('Hierarchy child count changed')
        for ca, cb in zip(a.get('children', []), b.get('children', [])):
            visit(ca, cb)
    visit(old['root'], new['root'])
    return stats


def inherit_validation(inventory, catalog_path, validator_path, metadata_path, public=PUBLIC):
    """Reuse passed byte-level audits only if their complete hash set still agrees."""
    source_hash = digest(catalog_path)
    core = json.loads(validator_path.read_bytes())
    metadata = json.loads(metadata_path.read_bytes())
    if core['catalog_sha256'] != source_hash or core['errors'] or core['warnings']:
        raise ValueError('Prior Khronos audit does not pass for the source catalog')
    if metadata['catalog_sha256'] != source_hash or not metadata['passed'] or metadata['errors']:
        raise ValueError('Prior metadata audit does not pass for the source catalog')
    expected = {row['url'].removeprefix('/data/'): row['sha256'] for row in inventory}
    validated = {row['file'].replace('\\', '/'): row['sha256'] for row in core['reports']}
    if expected != validated or len(core['reports']) != len(expected) or core['checked'] != len(expected):
        raise ValueError('Prior Khronos audit covers a different GLB hash set')
    # Reconstruct the old auditor's deterministic reverse-child DFS without
    # rereading geometry. The inventory was independently SHA-256 checked.
    proof = hashlib.sha256()
    catalog = json.loads(catalog_path.read_bytes())
    for asset in catalog['assets']:
        if asset['format'] != '3d-tiles':
            continue
        path = public/asset['url'].removeprefix('/data/')
        stack = [json.loads(path.read_bytes())['root']]
        while stack:
            tile = stack.pop()
            stack.extend(tile.get('children', []))
            if tile.get('content'):
                glb = (path.parent/tile['content']['uri']).resolve()
                key = glb.relative_to(public.resolve()).as_posix()
                proof.update(canonical_record(key, expected[key]))
    if proof.hexdigest() != metadata['content_digest'] or metadata['glb_count'] != len(expected):
        raise ValueError('Prior metadata audit content digest differs from the reused geometry')
    detail = [r for r in inventory if r['role'] == 'detail']
    lod = [r for r in inventory if r['role'] == 'representative_subset']
    if (sum(r['feature_count'] for r in detail) != metadata['features'] or
            sum(r['feature_count'] for r in lod) != metadata['lod_features']):
        raise ValueError('Prior metadata feature totals differ from the reused geometry')
    return {'passed': True, 'scope': 'Byte-identical GLB validation reuse; new hierarchy policy and references are checked separately.',
            'source_catalog_sha256': source_hash, 'glb_files_unchanged': len(expected),
            'khronos_report_sha256': digest(validator_path), 'metadata_report_sha256': digest(metadata_path),
            'khronos_errors': core['errors'], 'khronos_warnings': core['warnings'],
            'metadata_content_digest': proof.hexdigest(), 'detail_features': metadata['features'],
            'lod_features': metadata['lod_features'], 'geometry_revalidation_required': False}


def run(catalog_path, additions=(), *, public=PUBLIC, local=LOCAL):
    catalog = json.loads(catalog_path.read_bytes())
    assets = [a for a in catalog['assets'] if a['id'] == 'buildings-korea-retiled']
    if len(assets) != 1:
        raise ValueError('Expected exactly one national building hierarchy')
    asset = assets[0]
    source_path = public/asset['url'].removeprefix('/data/')
    if digest(source_path) != asset['sha256']:
        raise ValueError('Source hierarchy differs from catalog hash')
    added = [json.loads(path.read_bytes()) for path in additions]
    signature = {'policy': VERSION, 'source_catalog_sha256': digest(catalog_path),
                 'additional_asset_descriptors': [hashlib.sha256(encoded(a)).hexdigest() for a in added],
                 'metric': METRIC}
    fingerprint = hashlib.sha256(encoded(signature)).hexdigest()[:16]
    work = local/'lod-error'/fingerprint
    target = public/'hierarchy'/fingerprint/'tileset.json'
    old = json.loads(source_path.read_bytes())
    updated, metric_proof, inventory = update_tree(old, source_path, target, public)
    reuse = verify_reuse(old, updated, source_path, target)
    if metric_proof['detail_features'] != asset['count']:
        raise ValueError('Policy changed detailed building coverage')
    immutable_json(target, updated)
    new_asset = {**asset, 'url': '/data/'+target.relative_to(public).as_posix(), 'sha256': digest(target),
                 'byte_length': target.stat().st_size, 'bytes': target.stat().st_size,
                 'geometric_error_policy': VERSION}
    ids = {a['id'] for a in catalog['assets']}
    if any(a['id'] in ids for a in added) or len({a['id'] for a in added}) != len(added):
        raise ValueError('Additional asset ID duplicates the source catalog')
    result = {**catalog, 'assets': [new_asset if a['id'] == asset['id'] else copy.deepcopy(a)
                                   for a in catalog['assets']] + added}
    budgets = verify_geometry_budgets(result['assets'], public, fill_missing=True)
    candidate = work/'catalog.json'
    immutable_json(candidate, result)
    immutable_json(work/'signature.json', signature)
    immutable_json(work/'glb-reuse-manifest.json', inventory)
    immutable_json(work/'geometry-budget-audit.json', budgets)
    immutable_json(work/'replacement-budget-audit.json', replacement_budget(updated['root']))
    validator = catalog_path.parent/'gltf-validator.json'
    metadata = catalog_path.parent/'mesh-metadata-audit.json'
    if validator.is_file() and metadata.is_file():
        immutable_json(work/'validation-reuse.json', inherit_validation(inventory, catalog_path, validator, metadata, public))
    metric_proof.update(source_catalog_sha256=digest(catalog_path), source_tileset_sha256=digest(source_path),
                        candidate_sha256=digest(candidate), tileset_sha256=digest(target), reuse=reuse,
                        glb_reuse_manifest_sha256=digest(work/'glb-reuse-manifest.json'),
                        detail_count_unchanged=asset['count'],
                        new_public_files=1+len(added), new_geometry_glbs=0)
    immutable_json(work/'policy-proof.json', metric_proof)
    print(json.dumps({'candidate': str(candidate), 'candidate_sha256': digest(candidate),
                      'tileset': str(target), 'proof': str(work/'policy-proof.json'),
                      'glbs_unchanged': len(inventory), 'geojson_budgets_verified': budgets['geojson_assets_checked'],
                      'budget_descriptors_completed': len(budgets['filled'])}), flush=True)
    return candidate, work/'policy-proof.json'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--add-asset', type=Path, action='append', default=[])
    args = parser.parse_args()
    run(args.catalog.resolve(), [path.resolve() for path in args.add_asset])
