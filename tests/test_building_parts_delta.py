import copy
import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box, mapping

from pipeline import building_parts_delta as delta
from pipeline.glb_merge import merge_tiles
from pipeline.glb_merge import encode_glb
from pipeline.mesh import write_glb
from pipeline.mesh_metadata_audit import audit_glb, parse_glb


def feature(identity, x=127., height=20., *, parent=None):
    p = {'source_record_id': identity, 'source_id': 'overture', 'dataset_version': '2026-08-19.0',
         'height': height, 'min_height': 0., 'base_height': 30., 'name': '정확한 원문 ' + identity,
         'height_semantics': 'ground_to_top', 'raw_height': height, 'raw_min_height': None,
         'quality_flags': ['ground_not_independently_verified'], 'lod_role': 'detail',
         'render_height': height, 'render_min_height': 0., 'render_eligible': True,
         'original_properties': {'id': identity, 'height': height, 'min_height': None,
                                 'sources': [{'record_id': 'w123@4', 'license': 'ODbL-1.0'}]}}
    if parent:
        p.update(parent_source_record_id=parent, candidate_render_eligible=True, render_eligible=False)
        p['original_properties']['building_id'] = parent
    return {'type': 'Feature', 'id': identity, 'geometry': mapping(box(x, 36., x + .0001, 36.0001)), 'properties': p}


def mesh(path, features):
    tile, count = write_glb(features, path)
    raw = path.read_bytes()
    tile['extras'].update(sha256=delta.sha256(raw), bytes=len(raw), feature_count=count, lod_role='detail')
    return raw, tile, delta.matrix(tile)


def test_single_mesh_replacement_preserves_all_unrelated_geometry_metadata_and_ids(tmp_path):
    original, tile, world = mesh(tmp_path / 'original.glb', [feature('parent'), feature('neighbor', 127.001)])
    parts, _, part_world = mesh(tmp_path / 'parts.glb', [feature('p1', parent='parent'),
                                                       feature('p2', 127.0001, 26., parent='parent')])
    result, proof = delta.patch_detail(original, {'parent'}, [(parts, part_world)], world, ['p1', 'p2'])
    assert result == delta.patch_detail(original, {'parent'}, [(parts, part_world)], world, ['p1', 'p2'])[0]
    assert (tmp_path / 'original.glb').read_bytes() == original
    target = tmp_path / 'result.glb'; target.write_bytes(result)
    assert audit_glb(target)['features'] == 3
    _, old = delta.decode_profile(original); _, new = delta.decode_profile(result)
    assert delta.identities(new) == ['neighbor', 'p1', 'p2']
    assert delta.feature_fingerprints(old)['neighbor'] == delta.feature_fingerprints(new)['neighbor']
    assert proof['unaffected_features_bit_exact'] and proof['unchanged_features'] == 1
    assert proof['added_coordinate_rounding_max_m'] < .001
    added = new[-1]['props']
    assert json.loads(added['original_properties'][0])['building_id'] == 'parent'
    assert added['raw_min_height'][0] == b'' and added['height_semantics'][0] == b'ground_to_top'
    assert added['lod_role'] == [b'detail', b'detail']


def test_packed_multiple_tables_can_remove_entire_table_without_changing_other_table(tmp_path):
    _, first, _ = mesh(tmp_path / 'one.glb', [feature('parent')])
    _, second, _ = mesh(tmp_path / 'two.glb', [feature('neighbor', 127.003)])
    packed = merge_tiles([first, second], tmp_path, tmp_path / 'packed.glb')
    original = (tmp_path / 'packed.glb').read_bytes()
    parts, _, part_world = mesh(tmp_path / 'parts.glb', [feature('p1', parent='parent')])
    result, proof = delta.patch_detail(original, {'parent'}, [(parts, part_world)], delta.matrix(packed), ['p1'])
    assert proof['old_primitive_count'] == 2 and proof['new_primitive_count'] == 2
    assert delta.identities(delta.decode_profile(result)[1]) == ['neighbor', 'p1']
    assert delta.feature_fingerprints(delta.decode_profile(original)[1])['neighbor'] == \
        delta.feature_fingerprints(delta.decode_profile(result)[1])['neighbor']


def test_replaces_last_parent_and_keeps_complete_part_set(tmp_path):
    original, _, world = mesh(tmp_path / 'parent.glb', [feature('parent')])
    parts, _, pw = mesh(tmp_path / 'parts.glb', [feature('p1'), feature('p2', 127.0001)])
    result, proof = delta.patch_detail(original, {'parent'}, [(parts, pw)], world, ['p1', 'p2'])
    assert proof['unchanged_features'] == 0 and proof['after_features'] == 2
    assert delta.identities(delta.decode_profile(result)[1]) == ['p1', 'p2']


@pytest.mark.parametrize('remove,expected,error', [
    ({'missing'}, ['p1'], 'absent'), ({'parent'}, ['p1', 'p1'], 'duplicated'),
    ({'parent'}, ['parent'], 'already exists'), ({'parent'}, ['p2'], 'differs from plan'),
])
def test_missing_parent_duplicate_part_or_partial_group_rejected(tmp_path, remove, expected, error):
    original, _, world = mesh(tmp_path / 'parent.glb', [feature('parent')])
    parts, _, pw = mesh(tmp_path / 'parts.glb', [feature('p1')])
    with pytest.raises(ValueError, match=error):
        delta.patch_detail(original, remove, [(parts, pw)], world, expected)


def test_oversize_result_and_nonrigid_transform_fail_closed(tmp_path, monkeypatch):
    original, _, world = mesh(tmp_path / 'parent.glb', [feature('parent')])
    parts, _, pw = mesh(tmp_path / 'parts.glb', [feature('p1')])
    nonrigid = pw.copy(); nonrigid[:3, :3] *= 2
    with pytest.raises(ValueError, match='rigid'):
        delta.patch_detail(original, {'parent'}, [(parts, nonrigid)], world, ['p1'])
    monkeypatch.setattr(delta, 'MAX_DETAIL_BYTES', 10)
    with pytest.raises(ValueError, match='4 MiB'):
        delta.patch_detail(original, {'parent'}, [(parts, pw)], world, ['p1'])


@pytest.mark.parametrize('mutation,error', [
    ('attributes', 'vertex attributes'), ('transform', 'internal node'),
    ('triangles', 'Triangles cross'), ('numeric', 'Unknown numeric'),
])
def test_unknown_profile_or_cross_feature_triangles_cannot_silently_lose_information(tmp_path, mutation, error):
    raw, _, _ = mesh(tmp_path / 'source.glb', [feature('one'), feature('two', 127.001)])
    doc, binary = parse_glb(raw); data = bytearray(binary)
    primitive = doc['meshes'][0]['primitives'][0]
    if mutation == 'attributes': primitive['attributes']['TEXCOORD_0'] = primitive['attributes']['POSITION']
    elif mutation == 'transform': doc['nodes'][0]['translation'] = [0, 0, 0]
    elif mutation == 'numeric':
        doc['extensions']['EXT_structural_metadata']['schema']['classes']['building']['properties']['height']['componentType'] = 'FLOAT64'
    else:
        accessor = doc['accessors'][primitive['indices']]
        index_view = doc['bufferViews'][accessor['bufferView']]
        indices = np.frombuffer(data, dtype='<u4', count=accessor['count'], offset=index_view['byteOffset'])
        indices[1] = len(delta.decode_profile(raw)[1][0]['ids']) - 1
    with pytest.raises(ValueError, match=error): delta.decode_profile(encode_glb(doc, data))


def test_incremental_omission_distance_is_conservative_and_parent_monotone():
    representatives = np.asarray([[0., 0.], [100., 0.]])
    assert delta.incremental_error(80., 60., np.asarray([[80., 0.]]), representatives, [90.]) == (90., 60., 20.)
    assert delta.incremental_error(80., 60., np.asarray([[100., 120.]]), representatives, [90.]) == (120., 120., 120.)
    with pytest.raises(ValueError, match='no retained'):
        delta.incremental_error(1, 1, np.asarray([[0., 0.]]), np.empty((0, 2)), [0])


def fixture(tmp_path):
    repo = tmp_path / 'repo'; public = repo / 'public/data'; public.mkdir(parents=True)
    folder = repo / '.local/parts'; (folder / 'final/meshes').mkdir(parents=True)
    (folder / 'references').mkdir()
    parents = [feature('parent-a'), feature('parent-b', 127.002), feature('neighbor', 127.003)]
    original, leaf, leaf_world = mesh(public / 'detail.glb', parents)
    _, root, root_world = mesh(public / 'lod.glb', [parents[-1]])
    root['extras'].update(lod_role='representative_subset', omission_center_max_distance_m=300.)
    root['geometricError'] = 300.
    leaf['transform'] = (np.linalg.inv(root_world) @ leaf_world).flatten(order='F').tolist()
    root['children'] = [leaf]; root['refine'] = 'REPLACE'
    root['boundingVolume']['region'] = delta.union_region(root['boundingVolume']['region'], leaf['boundingVolume']['region'])
    tree = {'asset': {'version': '1.1'}, 'geometricError': 600., 'root': root, 'extras': {'detail_count': 3}}
    tree_raw = delta.encoded(tree); (public / 'tileset.json').write_bytes(tree_raw)
    asset = {'id': 'buildings-korea-retiled', 'format': '3d-tiles', 'url': '/data/tileset.json',
             'sha256': delta.sha256(tree_raw), 'byte_length': len(tree_raw), 'count': 3}
    catalog = {'release_id': 'pub-fixture', 'assets': [asset]}; cat_raw = delta.encoded(catalog)
    catalog_path = public / 'catalog.json'; catalog_path.write_bytes(cat_raw)
    reference = {'verified': True, 'public_release_id': 'pub-fixture', 'local_release_id': 'pub-fixture',
                 'local_catalog_sha256': delta.sha256(cat_raw), 'public_catalog_sha256': delta.sha256(cat_raw),
                 'building_root': asset}
    plan = {'groups': [], 'replace_parent_ids': ['parent-a', 'parent-b'],
            'global_sibling_scan_complete': True, 'parent_height_review_complete': True}
    parts = []; mesh_rows = []
    for n, parent in enumerate(parents[:2]):
        fs = [feature(parent['id'] + '-' + str(i), 127. + n * .002 + i * .00005, 30. + i,
                      parent=parent['id']) for i in range(2)]
        parts.extend(fs)
        path = folder / 'final/meshes' / (parent['id'] + '.glb')
        raw, tile, _ = mesh(path, fs)
        mesh_rows.append({'building_id': parent['id'], 'path': path.relative_to(folder).as_posix(),
                          'sha256': delta.sha256(raw), 'bytes': len(raw),
                          'coordinate_rounding_max_m': tile['extras']['coordinate_rounding_max_m']})
        plan['groups'].append({'building_id': parent['id'], 'part_source_ids': [f['id'] for f in fs],
            'candidate': True, 'published_parent_verified': True, 'global_sibling_count_verified': True,
            'global_sibling_count': 2, 'observed_sibling_count': 2, 'parent_height_consistency': 'source_parent_absent',
            'published_tiles': [{'path': 'public/data/detail.glb', 'sha256': delta.sha256(original),
                                 'bytes': len(original), 'occurrences': 1}]})
    collection = {'type': 'FeatureCollection', 'features': parts}
    mesh_audit = {'private_meshes': True, 'glb_id_original_properties_equal': True,
                  'raw_id_geometry_attributes_equal': True, 'plan_sha256': delta.sha256(delta.encoded(plan)),
                  'candidate_geojson_sha256': delta.sha256(delta.encoded(collection)), 'meshes': mesh_rows}
    for name, value in [('final/replacement-plan.json', plan), ('final/candidate-parts.geojson', collection),
                        ('final/mesh-audit.json', mesh_audit), ('references/published-parent-root.json', reference)]:
        (folder / name).write_bytes(delta.encoded(value))
    return repo, folder, catalog_path, plan, collection


def test_private_delta_reuses_lods_and_topology_preserves_sources_and_restarts(tmp_path):
    repo, folder, catalog, _, _ = fixture(tmp_path)
    sources = {p: p.read_bytes() for p in repo.rglob('*') if p.is_file()}
    output, report = delta.private_delta(folder, catalog, repo / '.local/delta', repo=repo, disk_reserve=0)
    assert report['changed_detail_glbs'] == 1 and report['unchanged_lod_glbs'] == 1
    assert report['removed_parents'] == 2 and report['added_parts'] == 4
    assert report['logical_source_building_count'] == 3 and report['detailed_render_features'] == 5
    assert report['structure']['transforms_bit_exact'] and report['structure']['changed_contents'] == 1
    assert report['transition_nodes'][0]['geometric_error_m'] >= 300.
    tree = json.loads((output / 'tileset.json').read_bytes())
    assert tree['root']['children'][0]['boundingVolume']['region'][5] >= 62.
    assert (output / tree['root']['content']['uri']).resolve() == repo / 'public/data/lod.glb'
    assert all(p.read_bytes() == raw for p, raw in sources.items())
    second, again = delta.private_delta(folder, catalog, repo / '.local/delta', repo=repo, disk_reserve=0)
    assert second == output and again == report


@pytest.mark.parametrize('field,value,error', [
    ('global_sibling_scan_complete', False, 'Complete global'), ('parent_height_review_complete', False, 'Complete global'),
    ('global_sibling_count', 3, 'Incomplete global'), ('parent_height_consistency', 'source_values_conflict', 'Conflicting'),
    ('published_parent_verified', False, 'verified complete'),
])
def test_unverified_incomplete_and_height_conflicting_groups_are_rejected(tmp_path, field, value, error):
    _, _, _, plan, collection = fixture(tmp_path)
    if field in plan: plan[field] = value
    else: plan['groups'][0][field] = value
    with pytest.raises(ValueError, match=error): delta.validate_plan(plan, collection['features'])


def test_changed_catalog_or_source_hash_is_rejected(tmp_path):
    repo, folder, catalog, _, _ = fixture(tmp_path)
    original = catalog.read_bytes(); catalog.write_bytes(original + b' ')
    with pytest.raises(ValueError, match='catalog differs'):
        delta.private_delta(folder, catalog, repo / '.local/delta', repo=repo, disk_reserve=0)
    catalog.write_bytes(original)
    glb = repo / 'public/data/detail.glb'; glb.write_bytes(glb.read_bytes() + b'junk')
    with pytest.raises(ValueError, match='hash mismatch'):
        delta.private_delta(folder, catalog, repo / '.local/delta', repo=repo, disk_reserve=0)


def test_parent_in_lod_requires_explicit_future_lod_conversion_and_no_output_meshes(tmp_path):
    repo, folder, catalog, _, _ = fixture(tmp_path)
    public = repo / 'public/data'
    _, tile, _ = mesh(public / 'lod.glb', [feature('parent-a')])
    tree_path = public / 'tileset.json'; tree = json.loads(tree_path.read_bytes())
    for key in ('sha256', 'bytes', 'feature_count'):
        tree['root']['extras'][key] = tile['extras'][key]
    raw = delta.encoded(tree); tree_path.write_bytes(raw)
    cat = json.loads(catalog.read_bytes()); cat['assets'][0].update(sha256=delta.sha256(raw), byte_length=len(raw))
    cat_raw = delta.encoded(cat); catalog.write_bytes(cat_raw)
    ref_path = folder / 'references/published-parent-root.json'; ref = json.loads(ref_path.read_bytes())
    ref.update(local_catalog_sha256=delta.sha256(cat_raw), public_catalog_sha256=delta.sha256(cat_raw),
               building_root=cat['assets'][0]); ref_path.write_bytes(delta.encoded(ref))
    with pytest.raises(ValueError, match='LOD representative'):
        delta.private_delta(folder, catalog, repo / '.local/delta', repo=repo, disk_reserve=0)
    assert not list((repo / '.local/delta').rglob('*.glb'))


def test_published_output_or_candidate_overwrite_is_forbidden(tmp_path):
    repo, folder, catalog, _, _ = fixture(tmp_path)
    for output in [repo / 'public/data/new', folder / 'delta']:
        with pytest.raises(ValueError, match='separate private'):
            delta.private_delta(folder, catalog, output, repo=repo, disk_reserve=0)


def test_structural_check_detects_changed_transform_even_if_world_looks_close(tmp_path):
    repo, folder, catalog, _, _ = fixture(tmp_path)
    old = json.loads((repo / 'public/data/tileset.json').read_bytes()); new = copy.deepcopy(old)
    new['root']['transform'][12] += .00001
    with pytest.raises(ValueError, match='transform changed'):
        delta.verify_tree_delta(old, new, repo / 'public/data/tileset.json', repo / 'public/data/tileset.json', {})
