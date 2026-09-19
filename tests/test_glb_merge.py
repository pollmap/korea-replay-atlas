import numpy as np

from pipeline.glb_merge import merge_tiles, normalize_empty_metadata, encode_glb, Y_TO_Z
from pipeline.mesh_metadata_audit import parse_glb
from pipeline import retile
from test_retile import building


def world_vertices(path, tile):
    document, binary = parse_glb(path.read_bytes())
    transform = np.asarray(tile['transform']).reshape(4, 4, order='F') @ Y_TO_Z
    points = []
    for mesh in document['meshes']:
        accessor = document['accessors'][mesh['primitives'][0]['attributes']['POSITION']]
        view = document['bufferViews'][accessor['bufferView']]
        local = np.ndarray((accessor['count'], 3), dtype='<f4', buffer=binary, offset=view.get('byteOffset', 0))
        points.append(local.astype(np.float64) @ transform[:3, :3].T+transform[:3, 3])
    return np.concatenate(points)


def test_spatial_merge_preserves_properties_ids_and_all_world_vertices(tmp_path):
    features = [building('left', 127), building('right', 127.09)]
    features[0]['properties']['source_record_id'] = 'left'
    features[1]['properties']['source_record_id'] = 'right'
    source = tmp_path/'source'
    tiles = []
    for i, feature in enumerate(features):
        tiles.extend(retile.emit_glbs([feature], source, tmp_path/'scratch', f'part-{i}'))
    before = {tile['content']['uri']: (source/tile['content']['uri']).read_bytes() for tile in tiles}
    expected = np.concatenate([world_vertices(source/tile['content']['uri'], tile) for tile in tiles])
    path = tmp_path/'merged.glb'
    merged = merge_tiles(tiles, source, path)
    actual = world_vertices(path, merged)
    error = np.linalg.norm(expected-actual, axis=1).max()
    assert error <= merged['extras']['additional_coordinate_rounding_max_m']+1e-7
    assert error < .001
    assert retile.glb_strings(path, 'source_record_id') == ['left', 'right']
    for key in ('name', 'height_method', 'original_properties'):
        assert retile.glb_strings(path, key) == [value for tile in tiles for value in retile.glb_strings(source/tile['content']['uri'], key)]
    assert merged['extras']['feature_count'] == 2
    assert all((source/name).read_bytes() == raw for name, raw in before.items())


def test_empty_metadata_padding_keeps_empty_strings_and_geometry(tmp_path):
    feature = building('padding')
    feature['properties']['source_record_id'] = 'padding'
    tiles = retile.emit_glbs([feature], tmp_path, tmp_path/'scratch', 'part')
    tile = tiles[0]; path = tmp_path/tile['content']['uri']
    document, binary = parse_glb(path.read_bytes())
    assert all(view['byteLength'] > 0 for view in document['bufferViews'])
    prop = document['extensions']['EXT_structural_metadata']['propertyTables'][0]['properties']['upstream_record_id']
    # Recreate the previous generator's zero-byte string column.
    document['bufferViews'][prop['values']]['byteLength'] = 0
    path.write_bytes(encode_glb(document, bytearray(binary)))
    tile['extras']['sha256'] = retile.digest(path)
    old = path.read_bytes(); before = world_vertices(path, tile)
    replacement = normalize_empty_metadata(tile, tmp_path)
    new_path = tmp_path/replacement['content']['uri']
    repaired, _ = parse_glb(new_path.read_bytes())
    assert all(view['byteLength'] > 0 for view in repaired['bufferViews'])
    assert retile.glb_strings(new_path, 'upstream_record_id') == ['']
    assert np.array_equal(before, world_vertices(new_path, replacement))
    assert path.read_bytes() == old


def test_compaction_resume_recovers_a_missing_generated_file(tmp_path):
    output = tmp_path/'output'; work = tmp_path/'work'; tiles = []
    for index, x in enumerate((127, 127.03)):
        feature = building(str(index), x)
        feature['properties']['source_record_id'] = str(index)
        tiles.extend(retile.emit_glbs([feature], output/'detail', work/'scratch', str(index)))
    result = {'id': 'source', 'tiles': tiles, 'detail_count': 2}
    first = retile.compact_detail([result], output, work)[0]
    assert len(first['tiles']) == 1
    path = output/'detail'/first['tiles'][0]['content']['uri']
    before = path.read_bytes()
    path.unlink()
    second = retile.compact_detail([result], output, work)[0]
    assert second == first and path.read_bytes() == before
