"""Read-only, one-GLB-at-a-time audit of the generated building mesh profile.

Complements the Khronos validator; this is not a general extension validator.
GLB-local feature IDs are row indexes, not original building identifiers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import time
from pathlib import Path

import numpy as np

from .core import PUBLIC, LOCAL, atomic_json, now


class MeshAuditError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise MeshAuditError(message)


def integer(value, label, minimum=0):
    require(type(value) is int and value >= minimum, 'Invalid '+label)
    return value


def parse_glb(data):
    require(len(data) >= 28, 'Truncated GLB header')
    magic, version, size = struct.unpack_from('<4sII', data)
    require(magic == b'glTF' and version == 2 and size == len(data), 'Invalid GLB header or total length')
    chunks = []; offset = 12
    while offset < len(data):
        require(offset+8 <= len(data), 'Truncated chunk header')
        length, kind = struct.unpack_from('<I4s', data, offset); offset += 8
        require(length % 4 == 0 and offset+length <= len(data), 'Invalid chunk length or alignment')
        chunks.append((kind, memoryview(data)[offset:offset+length])); offset += length
    require([kind for kind, _ in chunks] == [b'JSON', b'BIN\0'], 'Expected exactly JSON then BIN chunks')
    document = json.loads(bytes(chunks[0][1]))
    require(document['asset']['version'] == '2.0', 'Invalid glTF asset version')
    buffers = document['buffers']
    require(len(buffers) == 1 and 'uri' not in buffers[0], 'Expected one embedded buffer')
    length = integer(buffers[0]['byteLength'], 'buffer length')
    require(0 <= len(chunks[1][1])-length <= 3, 'BIN length differs from declared buffer')
    return document, chunks[1][1][:length]


def audit_glb(path):
    data = Path(path).read_bytes()
    try:
        gltf, binary = parse_glb(data)
        views = gltf['bufferViews']

        def view(index):
            integer(index, 'bufferView index')
            require(index < len(views), 'bufferView index out of range')
            item = views[index]
            require(item['buffer'] == 0, 'bufferView references external buffer')
            start = integer(item.get('byteOffset', 0), 'bufferView offset')
            length = integer(item['byteLength'], 'bufferView length', 1)
            require(start+length <= len(binary), 'bufferView extends beyond BIN')
            return binary[start:start+length]

        for index in range(len(views)):
            view(index)

        component_types = {5120:'i1', 5121:'u1', 5122:'<i2', 5123:'<u2', 5125:'<u4', 5126:'<f4'}
        components = {'SCALAR':1, 'VEC2':2, 'VEC3':3, 'VEC4':4}

        def accessor(index, kind):
            integer(index, 'accessor index')
            require(index < len(gltf['accessors']), 'accessor index out of range')
            item = gltf['accessors'][index]
            require('sparse' not in item and not item.get('normalized', False), 'Unsupported sparse/normalized accessor')
            require(item['type'] == kind, 'Unexpected accessor type for '+kind)
            count = integer(item['count'], 'accessor count', 1)
            dtype = np.dtype(component_types[item['componentType']]); width = components[kind]
            raw = view(item['bufferView']); offset = integer(item.get('byteOffset', 0), 'accessor offset')
            stride = integer(views[item['bufferView']].get('byteStride', dtype.itemsize*width), 'accessor stride', 1)
            require(stride >= dtype.itemsize*width and stride % dtype.itemsize == 0, 'Invalid accessor stride')
            require(offset % dtype.itemsize == 0 and offset+(count-1)*stride+dtype.itemsize*width <= len(raw), 'Accessor extends beyond bufferView')
            result = np.ndarray((count, width), dtype=dtype, buffer=raw, offset=offset, strides=(stride, dtype.itemsize))
            return result[:, 0] if kind == 'SCALAR' else result

        metadata = gltf['extensions']['EXT_structural_metadata']
        tables = metadata['propertyTables']; classes = metadata['schema']['classes']
        require(len(tables) > 0, 'No property tables')
        seen = []; counts = []; height_min = float('inf'); height_max = 0.; string_properties = 0
        source_identity_rows = 0; identity_proof = hashlib.sha256()
        numeric = {'FLOAT32':'<f4', 'FLOAT64':'<f8'}
        offsets_types = {'UINT8':'u1', 'UINT16':'<u2', 'UINT32':'<u4', 'UINT64':'<u8'}
        for table in tables:
            count = integer(table['count'], 'propertyTable count', 1)
            counts.append(count); seen.append(np.zeros(count, dtype=bool))
            schema = classes[table['class']]['properties']; properties = table['properties']
            require('height' in properties, 'Missing height property')
            for name, item in properties.items():
                definition = schema[name]; raw = view(item['values'])
                if name == 'height':
                    require(definition['type'] == 'SCALAR', 'Height must be SCALAR')
                    dtype = np.dtype(numeric[definition['componentType']])
                    require(len(raw) == count*dtype.itemsize, 'Height array count mismatch')
                    values = np.frombuffer(raw, dtype=dtype)
                    require(np.isfinite(values).all() and (values > 0).all(), 'Height must be finite and positive')
                    height_min = min(height_min, float(values.min())); height_max = max(height_max, float(values.max()))
                elif definition['type'] == 'STRING':
                    require(not definition.get('array', False), 'String arrays outside generated profile')
                    dtype = np.dtype(offsets_types[item.get('stringOffsetType', 'UINT32')])
                    offsets_raw = view(item['stringOffsets'])
                    require(len(offsets_raw) == (count+1)*dtype.itemsize, 'String offset count mismatch: '+name)
                    offsets = np.frombuffer(offsets_raw, dtype=dtype)
                    require(offsets[0] == 0 and offsets[-1] <= len(raw), 'String offsets outside values: '+name)
                    trailing = bytes(raw[int(offsets[-1]):])
                    require(len(trailing) <= 3 and not any(trailing), 'Unexpected string values padding: '+name)
                    require((offsets[1:] >= offsets[:-1]).all() and (offsets <= len(raw)).all(), 'String offsets not monotone/in range: '+name)
                    bytes(raw).decode('utf-8')
                    starts = offsets[offsets < len(raw)].astype(np.intp)
                    require(((np.frombuffer(raw, dtype='u1')[starts] & 0xC0) != 0x80).all(), 'String offset splits UTF-8 code point: '+name)
                    string_properties += 1
                    if name == 'source_record_id':
                        require((offsets[1:] > offsets[:-1]).all(), 'Empty source record identity')
                        source_identity_rows += count
                        for start, stop in zip(offsets[:-1], offsets[1:]):
                            identity_proof.update(bytes(raw[start:stop])+b'\n')
                else:
                    raise MeshAuditError('Unsupported generated property type: '+name)

        vertices = 0; triangles = 0; primitives = 0
        for mesh in gltf['meshes']:
            for primitive in mesh['primitives']:
                require(primitive.get('mode', 4) == 4, 'Expected triangle primitive')
                attributes = primitive['attributes']
                positions = accessor(attributes['POSITION'], 'VEC3'); normals = accessor(attributes['NORMAL'], 'VEC3')
                require(len(positions) == len(normals), 'Position/normal count mismatch')
                require(np.isfinite(positions).all(), 'Non-finite positions')
                require(np.isfinite(normals).all(), 'Non-finite normals')
                require((np.sum(normals.astype(np.float64)**2, axis=1) > 0).all(), 'Zero-length normals')
                indices = accessor(primitive['indices'], 'SCALAR')
                require(indices.dtype.kind == 'u' and len(indices) % 3 == 0 and int(indices.max()) < len(positions), 'Invalid triangle indices')
                feature_sets = primitive['extensions']['EXT_mesh_features']['featureIds']
                require(len(feature_sets) > 0, 'Missing mesh feature IDs')
                for features in feature_sets:
                    table_index = integer(features['propertyTable'], 'propertyTable index')
                    require(table_index < len(tables), 'propertyTable index out of range')
                    count = counts[table_index]
                    require(features['featureCount'] == count, 'Mesh featureCount/propertyTable count mismatch')
                    ids = accessor(attributes['_FEATURE_ID_'+str(features['attribute'])], 'SCALAR')
                    require(len(ids) == len(positions), 'Vertex feature ID count mismatch')
                    require(np.isfinite(ids).all() and (ids >= 0).all() and (ids < count).all() and (ids == np.floor(ids)).all(), 'Feature IDs must be finite integers in table range')
                    # Only indexed vertices count as actual rendered feature usage.
                    seen[table_index][ids[indices].astype(np.intp)] = True
                vertices += len(positions); triangles += len(indices)//3; primitives += 1
        require(primitives > 0 and all(flags.all() for flags in seen), 'Unused propertyTable feature rows')
        return {'features':sum(counts), 'vertices':vertices, 'triangles':triangles, 'primitives':primitives,
                'string_properties':string_properties, 'height_min':height_min, 'height_max':height_max,
                'source_identity_rows':source_identity_rows, 'source_identity_sha256':identity_proof.hexdigest(),
                'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()}
    except (KeyError, IndexError, TypeError, struct.error, UnicodeError) as error:
        raise MeshAuditError('Invalid generated GLB structure: '+str(error)) from error


def audit_catalog(public=PUBLIC, output=None, catalog_path=None):
    public = Path(public).resolve(); started = time.monotonic()
    # Read once. A subsequent publication cannot change this audit's selection.
    content = Path(catalog_path or public/'catalog.json').read_bytes(); catalog = json.loads(content)
    assets = [a for a in catalog['assets'] if a['format'] == '3d-tiles']
    report = {'schema_version':1, 'release_id':catalog['release_id'], 'catalog_sha256':hashlib.sha256(content).hexdigest(),
              'started_at':now(), 'passed':True, 'assets':[], 'errors':[], 'glb_count':0, 'features':0,
              'vertices':0, 'triangles':0, 'bytes':0, 'height_min':None, 'height_max':None,
              'lod_features':0, 'lod_glb_count':0, 'source_identity_rows':0, 'external_tilesets':0,
              'limitations':['Validates the generated GLB metadata profile, not all extension variants.',
                'Feature IDs are GLB-local property row indexes. Source IDs are validated when present; source-to-output identity comparison is provided by the retile checkpoints.',
                'Positive metadata heights and geometry do not establish measured building heights or real-world positional accuracy.',
                'Unknown-height footprint GeoJSON assets are outside this mesh audit.']}
    proof = hashlib.sha256()
    for number, asset in enumerate(assets, 1):
        row = {'id':asset['id'], 'expected_features':asset['count'], 'features':0, 'glb_count':0, 'passed':True}
        def error(message, path=None):
            row['passed'] = False; report['passed'] = False
            report['errors'].append({'asset_id':asset['id'], 'file':str(path) if path else None, 'error':message})
        try:
            path = (public/asset['url'].removeprefix('/data/')).resolve()
            require(path.is_relative_to(public), 'Tileset path escapes public directory')
            tileset_bytes = path.read_bytes()
            require(hashlib.sha256(tileset_bytes).hexdigest() == asset['sha256'], 'Catalog tileset hash mismatch')
            tileset = json.loads(tileset_bytes); stack = [(tileset['root'], path)]; paths = set(); manifests = {path}
            require_identity = bool(tileset.get('extras', {}).get('height_quality_policy'))

            def validate_content(glb, tile):
                require(glb not in paths, 'Duplicate tile content reference')
                paths.add(glb)
                try:
                    result = audit_glb(glb)
                    if require_identity:
                        require(result['source_identity_rows'] == result['features'], 'Source identity rows missing from retiled mesh')
                    expected_sha = tile.get('extras', {}).get('sha256')
                    if expected_sha:
                        require(result['sha256'] == expected_sha, 'Tile content hash mismatch')
                    row['glb_count'] += 1; report['glb_count'] += 1
                    if tile.get('extras', {}).get('lod_role') == 'representative_subset':
                        report['lod_glb_count'] += 1; report['lod_features'] += result['features']
                    else:
                        row['features'] += result['features']; report['features'] += result['features']
                        report['source_identity_rows'] += result['source_identity_rows']
                    for field in ('vertices', 'triangles', 'bytes'):
                        report[field] += result[field]
                    report['height_min'] = min(report['height_min'] or result['height_min'], result['height_min'])
                    report['height_max'] = max(report['height_max'] or result['height_max'], result['height_max'])
                    proof.update(canonical_record(glb.relative_to(public).as_posix(), result['sha256']))
                except (ValueError, OSError, OverflowError) as exc:
                    error(str(exc), glb)

            while stack:
                tile, manifest = stack.pop(); stack.extend((child, manifest) for child in tile.get('children', []))
                contents = ([tile['content']] if 'content' in tile else []) + tile.get('contents', [])
                for item in contents:
                    uri = item.get('uri', item.get('url', ''))
                    require(isinstance(uri, str) and bool(uri) and not any(c in uri for c in ('?', '#', ':', '\\')), 'Unexpected/nonlocal tile content')
                    glb = (manifest.parent/uri).resolve()
                    require(glb.is_relative_to(public), 'Unexpected/nonlocal tile content')
                    if glb.suffix == '.json':
                        require(glb not in manifests, 'Duplicate or cyclic external tileset')
                        manifests.add(glb)
                        nested = json.loads(glb.read_bytes())
                        stack.append((nested['root'], glb)); report['external_tilesets'] += 1
                        proof.update(canonical_record(glb.relative_to(public).as_posix(), hashlib.sha256(glb.read_bytes()).hexdigest()))
                        continue
                    require(glb.suffix == '.glb', 'Unexpected/nonlocal tile content')
                    validate_content(glb, tile)

            if row['features'] != asset['count']:error('GLB feature sum differs from catalog count', path)
        except (ValueError, OSError, KeyError, TypeError) as exc:error(str(exc))
        report['assets'].append(row)
        if number % 100 == 0:
            print(json.dumps({'stage':'mesh-metadata-audit', 'assets':number, 'glbs':report['glb_count'], 'errors':len(report['errors'])}), flush=True)

    report.update({'asset_count':len(assets), 'expected_features':sum(a['count'] for a in assets),
                   'content_digest':proof.hexdigest(), 'finished_at':now(), 'elapsed_seconds':round(time.monotonic()-started, 3)})
    if not assets:
        report['passed'] = False; report['errors'].append({'error':'No 3D Tiles assets to audit'})
    if output is not None:atomic_json(Path(output), report)
    return report


def canonical_record(path, digest):
    return json.dumps([path, digest], ensure_ascii=False, separators=(',', ':')).encode()+b'\n'


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, default=LOCAL/'audit'/'mesh-metadata.json')
    parser.add_argument('--catalog', type=Path); args = parser.parse_args()
    report = audit_catalog(output=args.output, catalog_path=args.catalog)
    print(json.dumps({k:v for k,v in report.items() if k not in ('assets', 'errors')}, ensure_ascii=False), flush=True)
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':main()
