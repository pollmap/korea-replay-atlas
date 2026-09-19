import copy
import json
from pathlib import Path

import numpy as np
import pytest

from pipeline import hierarchy
from pipeline.retile import emit_glbs


def fixture_tiles(tmp_path, count=24):
    tiles, representatives = [], {}
    for i in range(count):
        x, y = 127 + (i % 6)*.018, 36 + (i // 6)*.013
        feature = {'type': 'Feature', 'id': str(i), 'geometry': {'type': 'Polygon', 'coordinates': [
            [[x, y], [x+.001, y], [x+.001, y+.001], [x, y+.001], [x, y]]]},
            'properties': {'height': 12+i, 'base_height': 12, 'min_height': 0,
                           'source_record_id': str(i), 'source_id': 'fixture', 'height_method': 'source'}}
        tile = emit_glbs([feature], tmp_path/'old'/'detail', tmp_path/'scratch', f'detail-{i}')[0]
        tile['source_path'] = str(tmp_path/'old'/'detail'/tile['content']['uri'])
        tiles.append(tile)
        representatives[tile['content']['uri']] = [feature]
    return tiles, representatives


def test_spatial_replacement_groups_are_bounded_even_with_coincident_centers(tmp_path):
    tiles, _ = fixture_tiles(tmp_path, 24)
    for tile in tiles:
        tile['boundingVolume'] = copy.deepcopy(tiles[0]['boundingVolume'])
        tile['extras']['bytes'] = 3*1024*1024
    plan = hierarchy.hierarchy_plan(tiles, max_files=5, max_bytes=10*1024*1024)
    assert plan['direct_files'] <= 5
    assert plan['direct_bytes'] <= 10*1024*1024
    assert plan['nodes'] > 1 and plan['depth'] > 0


def test_nested_lod_preserves_all_detail_bytes_ids_world_frames_and_input(tmp_path):
    tiles, representatives = fixture_tiles(tmp_path)
    before = copy.deepcopy(tiles)
    original = {t['source_path']: Path(t['source_path']).read_bytes() for t in tiles}
    root, stats = hierarchy.build_tree(tiles, representatives, tmp_path/'new', tmp_path/'work',
                                      max_files=3, max_bytes=40000)
    proof = hierarchy.verify_tree(root, tiles, tmp_path/'new')
    assert proof['passed'] and proof['detail_glbs_unchanged'] == len(tiles)
    assert proof['detail_count_unchanged'] == len(tiles)
    assert proof['additional_transform_error_bound_m'] < 1e-6
    assert proof['detail_bytes_reencoded'] == 0
    assert stats['lod_files'] > 1
    assert stats['max_direct_detail_files'] <= 3
    assert stats['max_direct_detail_bytes'] <= 40000
    assert stats['max_immediate_content_bytes'] <= max(40000, 4*hierarchy.MAX_LOD_BYTES)
    assert tiles == before
    assert all(Path(path).read_bytes() == data for path, data in original.items())
    # The hierarchy uses external immutable detail paths, not copies or regenerated geometry.
    assert not (tmp_path/'new'/'detail').exists()


def test_verification_rejects_detail_omission_duplicate_and_bad_world_transform(tmp_path):
    tiles, representatives = fixture_tiles(tmp_path, 4)
    root, _ = hierarchy.build_tree(tiles, representatives, tmp_path/'new', tmp_path/'work')
    missing = copy.deepcopy(root)
    missing['children'].pop()
    with pytest.raises(ValueError, match='omitted'):
        hierarchy.verify_tree(missing, tiles, tmp_path/'new')
    duplicate = copy.deepcopy(root)
    duplicate['children'].append(copy.deepcopy(duplicate['children'][0]))
    with pytest.raises(ValueError, match='duplicated'):
        hierarchy.verify_tree(duplicate, tiles, tmp_path/'new')
    changed = copy.deepcopy(root)
    changed['children'][0]['transform'][12] += .01
    with pytest.raises(ValueError, match='micrometre'):
        hierarchy.verify_tree(changed, tiles, tmp_path/'new')


def test_input_hierarchy_composes_external_parent_frames(tmp_path, monkeypatch):
    tiles, representatives = fixture_tiles(tmp_path, 4)
    output = tmp_path/'new'
    root, _ = hierarchy.build_tree(tiles, representatives, output, tmp_path/'work', max_files=2)
    path = output/'tileset.json'
    hierarchy.immutable_json(path, {'root': root})
    monkeypatch.setattr(hierarchy, 'PUBLIC', tmp_path)
    read = hierarchy.read_detail_tiles(path)
    by_name = {Path(t['source_path']).name: t for t in tiles}
    assert len(read) == len(tiles)
    for tile in read:
        assert np.allclose(hierarchy.matrix(tile), hierarchy.matrix(by_name[Path(tile['source_path']).name]), atol=1e-7)


def test_hashed_candidate_does_not_overwrite_existing_different_content(tmp_path):
    target = tmp_path/'catalog.json'
    hierarchy.immutable_json(target, {'version': 'first'})
    hierarchy.immutable_json(target, {'version': 'first'})
    with pytest.raises(ValueError, match='already differs'):
        hierarchy.immutable_json(target, {'version': 'second'})
    assert json.loads(target.read_bytes()) == {'version': 'first'}


def test_replacement_budget_follows_empty_nodes_before_counting_content():
    def content(size):
        return {'content': {'uri': 'fixture.glb'}, 'extras': {'bytes': size}}
    root = {**content(5), 'children': [{'children': [content(7)]}, content(6)]}
    assert hierarchy.replacement_budget(root, maximum=13)['maximum_replacement_content_bytes'] == 13
    with pytest.raises(ValueError, match='frontier'):
        hierarchy.replacement_budget(root, maximum=12)


def test_lod_representative_budget_has_spatial_coverage_and_stable_ties():
    features = []
    for i in range(100):
        x, y = 127 + (i%10)/100, 36 + (i//10)/100
        features.append({'geometry': {'type': 'Point', 'coordinates': [x, y]},
                         'properties': {'height': 10, 'source_record_id': str(i)}})
    bounds = [np.radians(v) for v in [127, 36, 127.1, 36.1]] + [0, 100]
    selected = hierarchy.select_representatives(features, bounds, maximum=16)
    assert len(selected) == 16
    assert hierarchy.select_representatives(list(reversed(features)), bounds, maximum=16) == selected
    assert len({(int((f['geometry']['coordinates'][0]-127)*40),
                 int((f['geometry']['coordinates'][1]-36)*40)) for f in selected}) >= 9
