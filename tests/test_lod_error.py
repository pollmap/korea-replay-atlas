import copy
import hashlib
import json

import numpy as np
import pytest

from pipeline import hierarchy, lod_error
from pipeline.geometry_budget import verify_geometry_budgets
from pipeline.glb_merge import merge_tiles
from test_hierarchy import fixture_tiles


def test_omission_metric_excludes_retained_source_ids_and_uses_nearest_actual_center():
    points = np.array([[100000., 100000.], [3., 4.], [9., 0.]])
    result = lod_error.omission_metric(points, ['retained', 'a', 'b'],
                                       np.array([[0., 0.], [10., 0.]]), ['retained', 'other'])
    assert result['maximum_m'] == 5
    assert result['omitted_features'] == 2
    assert result['retained_detail_features'] == 1
    assert lod_error.omission_metric(points[:1], ['retained'], points[:1], ['retained'])['maximum_m'] == 0


def test_feature_centers_read_all_merged_metadata_tables_in_world_frame(tmp_path):
    tiles, _ = fixture_tiles(tmp_path, 2)
    separate = [lod_error.feature_centers(open(t['source_path'], 'rb').read(), hierarchy.matrix(t)) for t in tiles]
    merged_path = tmp_path/'merged.glb'
    merged = merge_tiles(tiles, tmp_path/'old'/'detail', merged_path)
    points, identities = lod_error.feature_centers(merged_path.read_bytes(), hierarchy.matrix(merged))
    assert identities == ['0', '1']
    assert np.max(np.linalg.norm(points-np.concatenate([p[0] for p in separate]), axis=1)) < .001


def test_policy_refines_omissions_and_preserves_geometry_and_transforms(tmp_path):
    tiles, reps = fixture_tiles(tmp_path, 4)
    for tile in tiles[1:]:
        reps[tile['content']['uri']] = []
    root, _ = hierarchy.build_tree(tiles, reps, tmp_path/'old-root', tmp_path/'work')
    source = {'root': root, 'geometricError': root['geometricError']*2}
    before = copy.deepcopy(source)
    old_path, new_path = tmp_path/'old-root'/'tileset.json', tmp_path/'new-root'/'tileset.json'
    updated, proof, inventory = lod_error.update_tree(source, old_path, new_path, tmp_path)
    assert source == before
    assert updated['root']['geometricError'] > root['geometricError']*10
    assert proof['nodes'][0]['omitted_features'] == 3
    assert proof['nodes'][0]['representative_features'] == 1
    assert proof['detail_features'] == 4
    assert len(inventory) == 5
    reused = lod_error.verify_reuse(source, updated, old_path, new_path)
    assert reused['glb_references_unchanged'] == 5
    assert reused['additional_coordinate_transform_error_m'] == 0
    assert all(child['geometricError'] == 0 for child in updated['root']['children'])
    changed = copy.deepcopy(updated)
    changed['root']['transform'][12] += 1
    with pytest.raises(ValueError, match='Non-policy'):
        lod_error.verify_reuse(source, changed, old_path, new_path)


def test_empty_intermediate_nodes_use_actual_ancestor_and_keep_parent_monotonicity(tmp_path):
    tiles, reps = fixture_tiles(tmp_path, 8)
    root, _ = hierarchy.build_tree(tiles, reps, tmp_path/'old-root', tmp_path/'work', max_files=2)
    empty = root['children'][0]
    assert empty.get('children')
    empty.pop('content')
    source = {'root': root}
    result, proof, _ = lod_error.update_tree(source, tmp_path/'old-root'/'tileset.json',
                                            tmp_path/'new-root'/'tileset.json', tmp_path)
    fallback = [row for row in proof['nodes'] if row['fallback_from_ancestor']]
    assert len(fallback) == 1
    assert fallback[0]['representative_node'] == 'root'
    assert result['root']['geometricError'] >= max(c['geometricError'] for c in result['root']['children'])


def test_policy_refuses_changed_glb_bytes(tmp_path):
    tiles, reps = fixture_tiles(tmp_path, 2)
    root, _ = hierarchy.build_tree(tiles, reps, tmp_path/'old-root', tmp_path/'work')
    root['extras']['sha256'] = '0'*64
    with pytest.raises(ValueError, match='Immutable GLB changed'):
        lod_error.update_tree({'root': root}, tmp_path/'old-root'/'tileset.json',
                              tmp_path/'new-root'/'tileset.json', tmp_path)


def test_validation_reuse_requires_exact_prior_hash_set_and_metadata_digest(tmp_path):
    tiles, reps = fixture_tiles(tmp_path, 2)
    root, _ = hierarchy.build_tree(tiles, reps, tmp_path/'old-root', tmp_path/'work')
    old_path = tmp_path/'old-root'/'tileset.json'
    hierarchy.immutable_json(old_path, {'root': root})
    _, _, inventory = lod_error.update_tree({'root': root}, old_path, tmp_path/'new-root'/'tileset.json', tmp_path)
    catalog_path = tmp_path/'catalog.json'
    hierarchy.immutable_json(catalog_path, {'assets': [{'format': '3d-tiles', 'url': '/data/old-root/tileset.json'}]})
    core = {'catalog_sha256': lod_error.digest(catalog_path), 'errors': 0, 'warnings': 0,
            'checked': len(inventory), 'reports': [{'file': r['url'].removeprefix('/data/'),
                                                 'sha256': r['sha256']} for r in inventory]}
    expected = {r['url'].removeprefix('/data/'): r['sha256'] for r in inventory}
    proof = hashlib.sha256()
    for node in [root, *reversed(root['children'])]:
        path = (old_path.parent/node['content']['uri']).resolve().relative_to(tmp_path).as_posix()
        proof.update(lod_error.canonical_record(path, expected[path]))
    metadata = {'catalog_sha256': core['catalog_sha256'], 'passed': True, 'errors': [],
                'glb_count': len(inventory), 'features': 2, 'lod_features': 2,
                'content_digest': proof.hexdigest()}
    core_path, metadata_path = tmp_path/'core.json', tmp_path/'metadata.json'
    hierarchy.immutable_json(core_path, core)
    hierarchy.immutable_json(metadata_path, metadata)
    assert lod_error.inherit_validation(inventory, catalog_path, core_path, metadata_path, tmp_path)['passed']
    changed = copy.deepcopy(inventory)
    changed[0]['sha256'] = '0'*64
    with pytest.raises(ValueError, match='different GLB hash set'):
        lod_error.inherit_validation(changed, catalog_path, core_path, metadata_path, tmp_path)
    metadata['content_digest'] = '0'*64
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='content digest'):
        lod_error.inherit_validation(inventory, catalog_path, core_path, metadata_path, tmp_path)


def geo_fixture(tmp_path):
    source = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'geometry': {'type': 'GeometryCollection', 'geometries': [
            {'type': 'Point', 'coordinates': [127, 36, 3]},
            {'type': 'LineString', 'coordinates': [[127, 36], [128, 37], [129, 38]]}]}, 'properties': {}},
        {'type': 'Feature', 'geometry': None, 'properties': {}}]}
    payload = json.dumps(source).encode()
    (tmp_path/'water.geojson').write_bytes(payload)
    return {'id': 'water', 'format': 'geojson', 'url': '/data/water.geojson',
            'sha256': hashlib.sha256(payload).hexdigest()}


def test_geojson_budget_fills_every_missing_field_even_when_vertex_count_exists(tmp_path):
    asset = geo_fixture(tmp_path)
    asset['vertex_count'] = 4
    report = verify_geometry_budgets([asset], tmp_path, fill_missing=True)
    assert asset['feature_count'] == 2
    assert asset['byte_length'] == (tmp_path/'water.geojson').stat().st_size
    assert report['filled'][0]['filled'] == ['byte_length', 'feature_count']
    assert verify_geometry_budgets([asset], tmp_path)['passed']


@pytest.mark.parametrize('field,wrong', [('vertex_count', 3), ('feature_count', 1), ('byte_length', 1)])
def test_geojson_budget_rejects_underreported_or_missing_actual_work(tmp_path, field, wrong):
    asset = geo_fixture(tmp_path)
    with pytest.raises(ValueError, match='Missing GeoJSON'):
        verify_geometry_budgets([asset], tmp_path)
    verify_geometry_budgets([asset], tmp_path, fill_missing=True)
    asset[field] = wrong
    with pytest.raises(ValueError, match='Incorrect GeoJSON work budget '+field):
        verify_geometry_budgets([asset], tmp_path, fill_missing=True)
