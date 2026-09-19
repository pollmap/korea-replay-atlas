import copy
import json
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box, mapping

from pipeline import building_parts_delta as delta
from pipeline import building_parts_publication as publication
from pipeline.hierarchy import replacement_budget
from pipeline.mesh import write_glb
from pipeline.mesh_metadata_audit import audit_glb


def feature(identity, lon, height=20):
    return {'type': 'Feature', 'id': identity, 'geometry': mapping(box(lon, 36., lon + .0001, 36.0001)),
        'properties': {'source_record_id': identity, 'source_id': 'overture', 'dataset_version': '2026-08-19.0',
            'height': height, 'min_height': 0, 'base_height': 30, 'name': '원문 ' + identity,
            'height_semantics': 'ground_to_top', 'quality_flags': ['ground_not_independently_verified'],
            'original_properties': {'id': identity, 'height': height, 'building_id': 'parent'}}}


def mesh(path, features, role='detail'):
    tile, count = write_glb(features, path); raw = path.read_bytes()
    tile['extras'].update(sha256=delta.sha256(raw), bytes=len(raw), feature_count=count, lod_role=role)
    return raw, tile


def json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(delta.encoded(value))


def fixture(tmp_path):
    repo = tmp_path / 'repo'; public = repo / 'public/data'; public.mkdir(parents=True)
    candidate = repo / '.local/delta'; (candidate / 'glb').mkdir(parents=True)
    parent = feature('parent', 127.); neighbor = feature('neighbor', 127.001)
    old_raw, leaf = mesh(public / 'old-detail.glb', [parent, neighbor])
    _, root = mesh(public / 'old-lod.glb', [neighbor], 'representative_subset')
    detail_world = delta.matrix(leaf)
    leaf['transform'] = (np.linalg.inv(delta.matrix(root)) @ detail_world).flatten(order='F').tolist()
    root['geometricError'] = 300.; root['children'] = [leaf]
    root['boundingVolume']['region'] = delta.union_region(root['boundingVolume']['region'], leaf['boundingVolume']['region'])
    original = {'asset': {'version': '1.1'}, 'root': root, 'geometricError': 600.}
    source_path = public / 'old-tree.json'; json_write(source_path, original)
    asset = {'id': 'buildings-korea-retiled', 'layer': 'buildings', 'format': '3d-tiles', 'source_id': 'overture',
        'detail_level': 'overview', 'version': '2026-08-19.0', 'dataset_version': '2026-08-19.0',
        'bbox': [126.9, 35.9, 127.1, 36.1], 'url': '/data/old-tree.json', 'count': 2,
        'sha256': delta.sha256(source_path.read_bytes()), 'byte_length': source_path.stat().st_size,
        'bytes': source_path.stat().st_size, 'hierarchy_version': 'bounded-spatial-lod-1'}
    other = {'id': 'untouched', 'url': '/data/unrelated.geojson', 'count': 200, 'version': 'fixture'}
    catalog = {'schema_version': 1, 'release_id': 'source-release', 'generated_at': '2026-09-19T00:00:00Z',
               'assets': [asset, other], 'layers': [{'id': 'buildings', 'record_count': 2}],
               'live_transit_routes': {'url': '/data/live-transit/routes/0123456789abcdef/manifest.json', 'sha256': 'f' * 64, 'byte_length': 100}}
    input_catalog = repo / '.local/input/catalog.json'; json_write(input_catalog, catalog)
    json_write(public / 'catalog.json', catalog); json_write(repo / 'public/catalog.json', {'legacy_pointer': True})
    part_raw, part_tile = mesh(repo / '.local/source-parts.glb', [feature('part-a', 127.), feature('part-b', 127.0001)])
    changed, patch_proof = delta.patch_detail(old_raw, {'parent'}, [(part_raw, delta.matrix(part_tile))], detail_world, ['part-a', 'part-b'])
    new_sha = delta.sha256(changed); part = candidate / 'glb' / (new_sha[:32] + '.glb'); part.write_bytes(changed)
    metadata = audit_glb(part); private = copy.deepcopy(original)
    private['root']['content']['uri'] = '../../public/data/old-lod.glb'
    private['root']['children'][0]['content']['uri'] = part.relative_to(candidate).as_posix()
    private['root']['children'][0]['extras'].update(sha256=new_sha, bytes=len(changed), feature_count=3)
    private['extras'] = {'detail_count': 3, 'logical_source_building_count': 2, 'private_candidate': True,
                        'public_assets_changed': False, 'geometric_error_policy': delta.ERROR_POLICY}
    json_write(candidate / 'tileset.json', private)
    reuse = [{'path': 'public/data/old-lod.glb', 'sha256': root['extras']['sha256'],
              'bytes': root['extras']['bytes'], 'role': 'representative_subset'}]
    patch = {'path': part.relative_to(candidate).as_posix(), 'sha256': new_sha, 'bytes': len(changed),
             'source_path': 'public/data/old-detail.glb', 'source_sha256': delta.sha256(old_raw),
             'source_bytes': len(old_raw), 'metadata_audit': metadata, **patch_proof}
    structure = delta.verify_tree_delta(original, private, source_path, candidate / 'tileset.json', {(public / 'old-detail.glb'): part})
    audit = {'version': delta.VERSION, 'passed': True, 'private_candidate': True, 'public_assets_changed': False,
        'unaffected_features_bit_exact': True, 'tileset_sha256': delta.sha256((candidate / 'tileset.json').read_bytes()),
        'source_root_sha256': asset['sha256'], 'logical_source_building_count': 2, 'patches': [patch],
        'structure': structure, 'replacement_frontier': replacement_budget(private['root']),
        'detailed_render_features': 3, 'removed_parents': 1, 'added_parts': 2,
        'changed_detail_glbs': 1, 'unchanged_glb_references': 1, 'unchanged_lod_glbs': 1}
    json_write(candidate / 'audit.json', audit); json_write(candidate / 'reuse.json', reuse)
    json_write(candidate / 'inputs.json', {'root_sha256': asset['sha256'], 'version': delta.VERSION})
    # Unit fixture models a previously successful validator receipt. Production
    # pins the actual approved audit and exact eight-file Khronos hash set.
    json_write(candidate / 'gltf-core-validation.json', {'checked': 1, 'errors': 0, 'warnings': 0,
        'reports': [{'path': patch['path'], 'sha256': new_sha, 'errors': 0, 'warnings': 0}]})
    return {'repo': repo, 'public': public, 'candidate': candidate, 'input_catalog': input_catalog,
            'expected_tree_sha256': audit['tileset_sha256'],
            'expected_audit_sha256': delta.sha256((candidate / 'audit.json').read_bytes())}


def run(f):
    return publication.publish(f['candidate'], f['input_catalog'], repo=f['repo'],
        expected_tree_sha256=f['expected_tree_sha256'], expected_audit_sha256=f['expected_audit_sha256'], disk_reserve=0)


def test_materializes_only_changed_meshes_preserves_geometry_counts_pointers_and_catalog(tmp_path):
    f = fixture(tmp_path)
    originals = {p: p.read_bytes() for p in f['repo'].rglob('*') if p.is_file()}
    path, report = run(f); result = json.loads(path.read_bytes())
    assert report['passed'] and not report['remote_published'] and not report['catalog_pointers_changed']
    assert report['new_public_files'] == 2 and report['new_glbs'] == 1 and report['reused_glbs'] == 1
    assert report['detailed_render_features'] == 3 and report['logical_source_building_count'] == 2
    old = json.loads(f['input_catalog'].read_bytes()); asset = result['assets'][0]
    assert {k: result[k] for k in result if k != 'assets'} == {k: old[k] for k in old if k != 'assets'}
    assert result['assets'][1:] == old['assets'][1:]
    assert asset['count'] == asset['feature_count'] == 3 and asset['logical_source_building_count'] == 2
    assert asset['building_parts_parent_count'] == 1 and asset['building_parts_feature_count'] == 2
    assert asset['id'] == old['assets'][0]['id'] and asset['version'] == old['assets'][0]['version']
    tree_path = publication.public_asset_path(asset['url'], f['public']); tree = json.loads(tree_path.read_bytes())
    assert 'private_candidate' not in tree['extras'] and 'public_assets_changed' not in tree['extras']
    assert publication.local_reference(tree_path, tree['root']['content']['uri'], (f['public'],)) == f['public'] / 'old-lod.glb'
    new_glb = publication.local_reference(tree_path, tree['root']['children'][0]['content']['uri'], (f['public'],))
    old_private_glb = next((f['candidate'] / 'glb').glob('*.glb'))
    assert new_glb.read_bytes() == old_private_glb.read_bytes()
    assert delta.identities(delta.decode_profile(new_glb.read_bytes())[1]) == ['neighbor', 'part-a', 'part-b']
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert len(list(tree_path.parent.rglob('*.glb'))) == 1
    assert json.loads((path.parent / 'validation-reuse.json').read_bytes())['delta_glbs_byte_identical']
    nodes = json.loads((path.parent / 'hierarchy-nodes.json').read_bytes())['nodes']
    assert [n['node_id'] for n in nodes] == ['root', 'root/0']
    assert nodes[1]['parent_id'] == 'root' and nodes[0]['child_ids'] == ['root/0']
    assert nodes[1]['source_content_url'] == '/data/old-detail.glb'
    assert nodes[1]['content_url'].startswith('/data/building-parts/')
    assert all(n['transform_matches_source'] for n in nodes)
    assert nodes[0]['local_transform'] == tree['root']['transform']


def test_deterministic_rerun_resumes_identical_outputs_without_duplicate_files(tmp_path):
    f = fixture(tmp_path); path, report = run(f)
    files = {p: p.read_bytes() for p in f['repo'].rglob('*') if p.is_file()}
    again, repeated = run(f)
    assert again == path and repeated == report
    assert files == {p: p.read_bytes() for p in f['repo'].rglob('*') if p.is_file()}


def test_preserves_partial_copies_as_resumable_checkpoints(tmp_path, monkeypatch):
    f = fixture(tmp_path); write = publication.Artifacts.write
    def interrupted(self, path, payload):
        if path.name == 'tileset.json': raise RuntimeError('fixture interrupted after mesh checkpoint')
        return write(self, path, payload)
    with monkeypatch.context() as m:
        m.setattr(publication.Artifacts, 'write', interrupted)
        with pytest.raises(RuntimeError, match='interrupted'): run(f)
    copied = list((f['public'] / 'building-parts').rglob('*.glb'))
    assert len(copied) == 1
    assert len(list((f['repo'] / '.local/building-parts-publication').rglob('checkpoints/*.json'))) == 1
    before = copied[0].read_bytes(); path, _ = run(f)
    assert path.is_file() and copied[0].read_bytes() == before


@pytest.mark.parametrize('name', ['tileset.json', 'audit.json'])
def test_pinned_candidate_input_tampering_fails_before_public_copy(tmp_path, name):
    f = fixture(tmp_path); p = f['candidate'] / name; p.write_bytes(p.read_bytes() + b' ')
    with pytest.raises(ValueError, match='SHA-256'): run(f)
    assert not (f['public'] / 'building-parts').exists()


@pytest.mark.parametrize('mutation', ['missing', 'wrong-hash', 'warning'])
def test_validator_receipt_requires_exact_complete_passing_hash_set(tmp_path, mutation):
    f = fixture(tmp_path); p = f['candidate'] / 'gltf-core-validation.json'; report = json.loads(p.read_bytes())
    if mutation == 'missing': report['reports'] = []
    elif mutation == 'wrong-hash': report['reports'][0]['sha256'] = 'a' * 64
    else: report['reports'][0]['warnings'] = 1
    json_write(p, report)
    with pytest.raises(ValueError, match='Khronos'): run(f)


@pytest.mark.parametrize('uri', ['https://invalid.test/tile.glb', '//invalid.test/tile.glb', 'file:///secret.glb',
                               '../outside.glb', 'tile.glb?key=secret', '%2e%2e/secret.glb', 'x\\y.glb', '/tmp/file.glb'])
def test_external_or_escaping_content_uris_are_rejected(tmp_path, uri):
    with pytest.raises(ValueError, match='URI|escaped'):
        publication.local_reference(tmp_path / 'root/tree.json', uri, ((tmp_path / 'root').resolve(),))


def test_candidate_cannot_reference_unlisted_file_even_inside_allowed_public_root(tmp_path):
    f = fixture(tmp_path); private = json.loads((f['candidate'] / 'tileset.json').read_bytes())
    private['root']['content']['uri'] = '../../public/data/old-detail.glb'
    with pytest.raises(ValueError, match='closure'):
        publication.rewrite_tree(private, f['candidate'] / 'tileset.json', f['public'] / 'new/tileset.json', {},
                                 {f['public'] / 'old-lod.glb': {}}, f['public'])


def test_reused_mesh_missing_and_changed_mesh_corruption_are_detected(tmp_path):
    f = fixture(tmp_path); lod = f['public'] / 'old-lod.glb'; lod.write_bytes(lod.read_bytes() + b'junk')
    with pytest.raises(ValueError, match='size changed'): run(f)
    f2 = fixture(tmp_path / 'other'); p = next((f2['candidate'] / 'glb').glob('*.glb')); p.write_bytes(p.read_bytes() + b'junk')
    with pytest.raises(ValueError, match='SHA-256'): run(f2)


def test_existing_public_artifact_is_never_overwritten(tmp_path):
    f = fixture(tmp_path); _, proof = run(f)
    tree = publication.public_asset_path(proof['public_tree_url'], f['public']); tree.write_bytes(b'owned existing content')
    with pytest.raises(ValueError, match='overwrite'): run(f)
    assert tree.read_bytes() == b'owned existing content'


def test_artifact_budget_and_free_disk_reserve_are_enforced(tmp_path, monkeypatch):
    artifacts = publication.Artifacts(tmp_path / 'new-public', tmp_path / 'new-local', disk_reserve=0)
    monkeypatch.setattr(publication, 'STORAGE_LIMIT', 3)
    with pytest.raises(ValueError, match='artifact budget'): artifacts.write(artifacts.public / 'four', b'1234')
    monkeypatch.setattr(publication, 'STORAGE_LIMIT', 100)
    artifacts.disk_reserve = 10 ** 30
    with pytest.raises(ValueError, match='disk reserve'): artifacts.write(artifacts.work / 'one', b'1')
    artifacts.disk_reserve = 0; monkeypatch.setattr(publication, 'memory_usage', lambda: (600 * 1024 ** 2, 600 * 1024 ** 2))
    with pytest.raises(ValueError, match='memory budget'): artifacts.check()


def test_source_tree_change_or_wrong_flat_catalog_blocks_promotion(tmp_path):
    f = fixture(tmp_path); tree = f['public'] / 'old-tree.json'; tree.write_bytes(tree.read_bytes() + b' ')
    with pytest.raises(ValueError, match='SHA-256'): run(f)
    f2 = fixture(tmp_path / 'other'); catalog = json.loads(f2['input_catalog'].read_bytes()); catalog['schema_version'] = 2
    json_write(f2['input_catalog'], catalog)
    with pytest.raises(ValueError, match='flat v1'): run(f2)
