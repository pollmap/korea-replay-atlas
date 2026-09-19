"""Spatially merge generated GLBs without remeshing or dropping metadata.

Only the known KOREA REPLAY profile is accepted. Every source position is
transformed once into a shared local frame; measured float32 rounding error is
included in the resulting tile. Source files are always left untouched.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import struct
from pathlib import Path

import numpy as np

from .mesh import frame
from .mesh_metadata_audit import audit_glb, parse_glb

# A glTF Y-up point becomes an East/North/Up tile point by this rotation.
Y_TO_Z = np.array([[1., 0., 0., 0.], [0., 0., -1., 0.], [0., 1., 0., 0.], [0., 0., 0., 1.]])


def pad_empty_views(document, binary):
    empty = [view for view in document['bufferViews'] if view['byteLength'] == 0]
    if empty:
        start = len(binary)
        binary.extend(b'\0')
        for view in empty:
            view.update(byteOffset=start, byteLength=1)
    return bool(empty)


def encode_glb(document, binary):
    document['buffers'] = [{'byteLength': len(binary)}]
    header = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode()
    header += b' '*(-len(header) % 4); binary += b'\0'*(-len(binary) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 28+len(header)+len(binary))
            + struct.pack('<I4s', len(header), b'JSON')+header
            + struct.pack('<I4s', len(binary), b'BIN\0')+binary)


def normalize_empty_metadata(tile, source_dir):
    path = source_dir/tile['content']['uri']
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != tile['extras']['sha256']:
        raise ValueError('GLB changed before metadata normalization')
    document, source = parse_glb(raw)
    binary = bytearray(source)
    if not pad_empty_views(document, binary):
        return tile
    payload = encode_glb(document, binary)
    sha = hashlib.sha256(payload).hexdigest()
    target = source_dir/f'valid-{sha[:32]}.glb'
    target.write_bytes(payload)
    audit = audit_glb(target)
    if audit['features'] != tile['extras']['feature_count']:
        raise ValueError('Metadata padding changed feature count')
    return {**tile, 'content': {'uri': target.name}, 'extras': {**tile['extras'],
            'sha256': sha, 'bytes': len(payload), 'metadata_empty_padding': True}}


def merge_tiles(tiles, source_dir: Path, target: Path):
    regions = np.asarray([tile['boundingVolume']['region'] for tile in tiles])
    bounds = [float(regions[:, 0].min()), float(regions[:, 1].min()),
              float(regions[:, 2].max()), float(regions[:, 3].max()),
              float(regions[:, 4].min()), float(regions[:, 5].max())]
    _, _, transform = frame(math.degrees((bounds[0]+bounds[2])/2), math.degrees((bounds[1]+bounds[3])/2))
    parent = np.asarray(transform).reshape(4, 4, order='F')
    inverse = np.linalg.inv(parent)
    result = None; binary = bytearray(); metadata = None; error = 0.; feature_count = 0
    for tile in tiles:
        path = source_dir/tile['content']['uri']
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != tile['extras']['sha256']:
            raise ValueError('Source GLB changed before merge: '+str(path))
        document, original = parse_glb(raw)
        document = copy.deepcopy(document); local = bytearray(original)
        pad_empty_views(document, local)
        if len(document['nodes']) != 1 or document['nodes'][0] != {'mesh': 0} or len(document['meshes']) != 1:
            raise ValueError('Only generated single-mesh GLBs can be merged')
        table = document['extensions']['EXT_structural_metadata']
        if result is None:
            result = {k: copy.deepcopy(v) for k, v in document.items() if k not in ('buffers', 'bufferViews', 'accessors', 'meshes', 'nodes', 'scenes', 'extensions')}
            result.update({'bufferViews': [], 'accessors': [], 'meshes': [], 'nodes': [], 'scenes': [{'nodes': []}], 'scene': 0})
            metadata = {'schema': table['schema'], 'propertyTables': []}
            result['extensions'] = {'EXT_structural_metadata': metadata}
        elif metadata['schema'] != table['schema'] or result['materials'] != document['materials']:
            raise ValueError('Incompatible mesh schema/materials')
        child = np.asarray(tile['transform']).reshape(4, 4, order='F')
        change = Y_TO_Z.T @ inverse @ child @ Y_TO_Z
        primitive = document['meshes'][0]['primitives'][0]
        if len(document['meshes'][0]['primitives']) != 1:
            raise ValueError('Only the single-primitive generated profile is accepted')
        for name in ('POSITION', 'NORMAL'):
            accessor = document['accessors'][primitive['attributes'][name]]
            view = document['bufferViews'][accessor['bufferView']]
            if accessor['componentType'] != 5126 or accessor['type'] != 'VEC3' or view.get('byteStride', 12) != 12:
                raise ValueError('Unexpected generated vector accessor')
            start = view.get('byteOffset', 0)+accessor.get('byteOffset', 0)
            values = np.ndarray((accessor['count'], 3), dtype='<f4', buffer=original, offset=start).astype(np.float64)
            changed = values @ change[:3, :3].T
            if name == 'POSITION':
                changed += change[:3, 3]
            rounded = changed.astype('<f4')
            if name == 'POSITION':
                error = max(error, float(np.linalg.norm(changed-rounded.astype(np.float64), axis=1).max()))
            payload = rounded.tobytes(); local[start:start+len(payload)] = payload
            accessor.update({'min': rounded.min(axis=0).tolist(), 'max': rounded.max(axis=0).tolist()})
        binary.extend(b'\0'*(-len(binary) % 4))
        byte_offset = len(binary); view_offset = len(result['bufferViews']); accessor_offset = len(result['accessors'])
        table_offset = len(metadata['propertyTables']); mesh_offset = len(result['meshes'])
        for view in document['bufferViews']:
            view['byteOffset'] = view.get('byteOffset', 0)+byte_offset
        result['bufferViews'].extend(document['bufferViews'])
        for accessor in document['accessors']:
            accessor['bufferView'] += view_offset
        result['accessors'].extend(document['accessors'])
        for prop_table in table['propertyTables']:
            for prop in prop_table['properties'].values():
                for field in ('values', 'stringOffsets', 'arrayOffsets'):
                    if field in prop:
                        prop[field] += view_offset
        metadata['propertyTables'].extend(table['propertyTables'])
        primitive['attributes'] = {name: value+accessor_offset for name, value in primitive['attributes'].items()}
        primitive['indices'] += accessor_offset
        for features in primitive['extensions']['EXT_mesh_features']['featureIds']:
            features['propertyTable'] += table_offset
        result['meshes'].extend(document['meshes'])
        result['nodes'].append({'mesh': mesh_offset}); result['scenes'][0]['nodes'].append(mesh_offset)
        binary.extend(local); feature_count += tile['extras']['feature_count']
    if result is None:
        raise ValueError('Cannot merge empty tile list')
    if error > .01:
        raise ValueError(f'Merge exceeded 1cm additional coordinate error: {error}')
    payload = encode_glb(result, binary)
    target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(payload)
    audit = audit_glb(target)
    if audit['features'] != feature_count or audit['source_identity_rows'] != feature_count:
        raise ValueError('Merged feature metadata/identity count changed')
    return {'boundingVolume': {'region': bounds}, 'geometricError': 0, 'transform': transform,
            'content': {'uri': target.name}, 'extras': {
                'lod_role': 'detail', 'feature_count': feature_count, 'sha256': audit['sha256'], 'bytes': len(payload),
                'vertex_count': audit['vertices'], 'merged_source_tiles': len(tiles),
                'additional_coordinate_rounding_max_m': error,
                'coordinate_rounding_max_m': error+max(t['extras']['coordinate_rounding_max_m'] for t in tiles)}}
