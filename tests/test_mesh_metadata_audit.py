import hashlib
import json
import struct

import numpy as np
import pytest

from pipeline.mesh_metadata_audit import audit_glb, audit_catalog, MeshAuditError


def encode(document, binary):
    header = json.dumps(document).encode(); header += b' '*(-len(header) % 4)
    binary = bytes(binary); binary += b'\0'*(-len(binary) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 28+len(header)+len(binary))
            + struct.pack('<I4s', len(header), b'JSON')+header
            + struct.pack('<I4s', len(binary), b'BIN\0')+binary)


@pytest.fixture
def model(tmp_path):
    blob = bytearray(); views = []
    arrays = [np.arange(18, dtype='<f4').reshape(6, 3),
              np.tile(np.array([0, 1, 0], dtype='<f4'), (6, 1)),
              np.array([0, 0, 0, 1, 1, 1], dtype='<f4'), np.arange(6, dtype='<u4'),
              np.array([10, 20], dtype='<f4'), b'AB', np.array([0, 1, 2], dtype='<u4')]
    for array in arrays:
        blob.extend(b'\0'*(-len(blob) % 4))
        raw = array if isinstance(array, bytes) else array.tobytes()
        views.append({'buffer':0, 'byteOffset':len(blob), 'byteLength':len(raw)}); blob.extend(raw)
    document = {'asset':{'version':'2.0'}, 'buffers':[{'byteLength':len(blob)}], 'bufferViews':views,
                'accessors':[{'bufferView':i, 'componentType':5125 if i == 3 else 5126,
                              'count':6, 'type':'VEC3' if i < 2 else 'SCALAR'} for i in range(4)],
                'meshes':[{'primitives':[{'attributes':{'POSITION':0, 'NORMAL':1, '_FEATURE_ID_0':2}, 'indices':3,
                    'extensions':{'EXT_mesh_features':{'featureIds':[{'featureCount':2, 'attribute':0, 'propertyTable':0}]}}}]}],
                'extensions':{'EXT_structural_metadata':{'schema':{'classes':{'building':{'properties':{
                    'height':{'type':'SCALAR', 'componentType':'FLOAT32'}, 'name':{'type':'STRING'}}}}},
                    'propertyTables':[{'class':'building', 'count':2, 'properties':{
                        'height':{'values':4}, 'name':{'values':5, 'stringOffsets':6}}}]}}}
    return tmp_path/'mesh.glb', document, blob


def test_valid_two_feature_mesh_and_finite_geometry(model):
    path, document, blob = model; path.write_bytes(encode(document, blob))
    result = audit_glb(path)
    assert (result['features'], result['vertices'], result['triangles']) == (2, 6, 2)
    assert (result['height_min'], result['height_max']) == (10, 20)


@pytest.mark.parametrize('fault,expected', [
    ('header', 'header'), ('chunk', 'chunk'), ('view', 'bufferView'),
    ('count', 'featureCount'), ('height_nan', 'finite and positive'), ('height_zero', 'finite and positive'),
    ('height_count', 'Height array count'), ('offset_count', 'offset count'),
    ('offset_range', 'offsets outside'), ('offset_order', 'monotone'),
    ('fractional_id', 'finite integers'), ('range_id', 'finite integers'), ('unused_id', 'Unused'),
    ('position_nan', 'Non-finite positions'), ('normal_inf', 'Non-finite normals'),
    ('index_range', 'triangle indices'), ('vertex_count', 'Vertex feature ID count')])
def test_malformed_metadata_and_mesh_fail_closed(model, fault, expected):
    path, document, blob = model
    def write(view, offset, fmt, value):
        struct.pack_into(fmt, blob, document['bufferViews'][view]['byteOffset']+offset, value)
    if fault == 'count':document['meshes'][0]['primitives'][0]['extensions']['EXT_mesh_features']['featureIds'][0]['featureCount'] = 3
    elif fault == 'view':document['bufferViews'][0]['byteLength'] = len(blob)*2
    elif fault == 'height_nan':write(4, 0, '<f', float('nan'))
    elif fault == 'height_zero':write(4, 0, '<f', 0)
    elif fault == 'height_count':document['bufferViews'][4]['byteLength'] -= 4
    elif fault == 'offset_count':document['bufferViews'][6]['byteLength'] -= 4
    elif fault == 'offset_range':write(6, 8, '<I', 20)
    elif fault == 'offset_order':write(6, 4, '<I', 3)
    elif fault == 'fractional_id':write(2, 0, '<f', .5)
    elif fault == 'range_id':write(2, 0, '<f', 2)
    elif fault == 'unused_id':
        for i in range(6):write(2, i*4, '<f', 0)
    elif fault == 'position_nan':write(0, 0, '<f', float('nan'))
    elif fault == 'normal_inf':write(1, 0, '<f', float('inf'))
    elif fault == 'index_range':write(3, 0, '<I', 6)
    elif fault == 'vertex_count':document['accessors'][2]['count'] = 5
    data = bytearray(encode(document, blob))
    if fault == 'header':struct.pack_into('<I', data, 8, len(data)+4)
    elif fault == 'chunk':struct.pack_into('<I', data, 12, len(data))
    path.write_bytes(data)
    with pytest.raises(MeshAuditError, match=expected):audit_glb(path)


def test_only_indexed_feature_usage_counts(model):
    path, document, blob = model
    # Feature 1 exists in vertex attributes but none of its vertices is drawn.
    start = document['bufferViews'][3]['byteOffset']
    struct.pack_into('<6I', blob, start, 0, 1, 2, 0, 1, 2)
    path.write_bytes(encode(document, blob))
    with pytest.raises(MeshAuditError, match='Unused'):audit_glb(path)


def test_string_offsets_cannot_split_a_valid_utf8_sequence(model):
    path, document, blob = model
    start = document['bufferViews'][5]['byteOffset']
    blob[start:start+2] = '\u00e9'.encode('utf-8')
    # The concatenated string is valid UTF-8, but offset 1 splits the character.
    path.write_bytes(encode(document, blob))
    with pytest.raises(MeshAuditError, match='splits UTF-8'):audit_glb(path)


def test_catalog_sum_and_tileset_hash_are_checked_without_modifying_inputs(model, tmp_path):
    path, document, blob = model; path.write_bytes(encode(document, blob))
    tileset = tmp_path/'tileset.json'
    tileset.write_text(json.dumps({'root':{'children':[{'content':{'uri':'mesh.glb'}}]}}))
    catalog = {'release_id':'fixture', 'assets':[{'id':'fixture', 'format':'3d-tiles', 'count':2,
        'url':'/data/tileset.json', 'sha256':hashlib.sha256(tileset.read_bytes()).hexdigest()}]}
    pointer = tmp_path/'catalog.json'; pointer.write_text(json.dumps(catalog))
    before = path.read_bytes(), tileset.read_bytes(), pointer.read_bytes()
    report = audit_catalog(tmp_path, tmp_path/'audit.json')
    assert report['passed'] and report['features'] == 2 and report['glb_count'] == 1
    assert (path.read_bytes(), tileset.read_bytes(), pointer.read_bytes()) == before
    catalog['assets'][0]['count'] = 3; pointer.write_text(json.dumps(catalog))
    report = audit_catalog(tmp_path)
    assert not report['passed'] and 'feature sum' in report['errors'][0]['error']
    catalog['assets'][0]['sha256'] = '0'*64; pointer.write_text(json.dumps(catalog))
    report = audit_catalog(tmp_path)
    assert not report['passed'] and 'hash mismatch' in report['errors'][0]['error']


def test_external_tilesets_count_lod_separately(model, tmp_path):
    path, document, blob = model
    path.write_bytes(encode(document, blob))
    overview = tmp_path/'overview.glb'
    overview.write_bytes(path.read_bytes())
    nested = tmp_path/'region.json'
    nested.write_text(json.dumps({'root': {
        'content': {'uri': overview.name}, 'extras': {'lod_role': 'representative_subset'},
        'children': [{'content': {'uri': path.name}}]}}))
    tileset = tmp_path/'tileset.json'
    tileset.write_text(json.dumps({'root': {'content': {'uri': nested.name}}}))
    pointer = tmp_path/'catalog.json'
    pointer.write_text(json.dumps({'release_id': 'fixture', 'assets': [{
        'id': 'fixture', 'format': '3d-tiles', 'count': 2, 'url': '/data/tileset.json',
        'sha256': hashlib.sha256(tileset.read_bytes()).hexdigest()}]}))
    report = audit_catalog(tmp_path)
    assert report['passed']
    assert report['features'] == report['lod_features'] == 2
    assert report['glb_count'] == 2 and report['lod_glb_count'] == 1
    assert report['external_tilesets'] == 1
    nested.write_text(json.dumps({'root': {'content': {'uri': tileset.name}}}))
    report = audit_catalog(tmp_path)
    assert not report['passed'] and 'cyclic external tileset' in report['errors'][0]['error']
