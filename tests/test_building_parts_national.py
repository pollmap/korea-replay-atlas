import copy
import io
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from shapely.geometry import box, mapping

from pipeline import building_parts_national as national


def budget(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(national, 'memory_usage', lambda: (10, 20))
    return national.Budget(tmp_path / 'candidate', disk_reserve=0, **kwargs)


class Response:
    def __init__(self, body=b'1234', status=206, headers=None):
        self.body, self.status_code = body, status
        self.headers = {'Content-Range': 'bytes 8-11/20', 'Content-Length': '4', **(headers or {})}
        self.iterated = False

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def iter_content(self, _):
        self.iterated = True
        yield self.body


class Session:
    def __init__(self, response): self.response = response; self.kwargs = None
    def get(self, url, **kwargs): self.kwargs = kwargs; return self.response


URL = 'https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/release/source.parquet'


def test_range_requires_exact_206_and_disables_redirects(tmp_path, monkeypatch):
    b = budget(tmp_path, monkeypatch); session = Session(Response())
    reader = national.PublicReader(b, session)
    assert reader.get(URL, 4, span=(8, 11), total=20) == b'1234'
    assert session.kwargs['headers']['Range'] == 'bytes=8-11'
    assert session.kwargs['allow_redirects'] is False
    assert reader.actual_bytes == b.network_bytes == 4


def test_range_pins_source_etag_and_rejects_changed_source(tmp_path, monkeypatch):
    b = budget(tmp_path, monkeypatch); session = Session(Response(headers={'ETag': 'original'}))
    reader = national.PublicReader(b, session)
    assert reader.get(URL, 4, span=(8, 11), total=20, etag='original') == b'1234'
    assert session.kwargs['headers']['If-Match'] == 'original'
    session.response.headers['ETag'] = 'changed'
    with pytest.raises(ValueError, match='Source object changed'):
        reader.get(URL, 4, span=(8, 11), total=20, etag='original')


@pytest.mark.parametrize('status', [200, 301, 302, 403, 500])
def test_range_refuses_full_object_redirects_and_error_bodies(tmp_path, monkeypatch, status):
    b = budget(tmp_path, monkeypatch); response = Response(status=status)
    with pytest.raises(RuntimeError):
        national.PublicReader(b, Session(response)).get(URL, 4, span=(8, 11), total=20)
    assert not response.iterated


@pytest.mark.parametrize('headers,body', [
    ({'Content-Range': 'bytes 8-11/21'}, b'1234'),
    ({'Content-Encoding': 'gzip'}, b'1234'),
    ({'Content-Length': '5'}, b'12345'),
    ({}, b'123'),
])
def test_range_rejects_wrong_interval_compression_oversize_and_truncation(tmp_path, monkeypatch, headers, body):
    b = budget(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        national.PublicReader(b, Session(Response(body=body, headers=headers))).get(URL, 4, span=(8, 11), total=20)


def test_failed_request_reservation_survives_restart(tmp_path, monkeypatch):
    b = budget(tmp_path, monkeypatch, network_limit=7)
    with pytest.raises(RuntimeError):
        national.PublicReader(b, Session(Response(status=503))).get(URL, 4, span=(8, 11), total=20)
    resumed = national.Budget(b.folder, disk_reserve=0, network_limit=7)
    with pytest.raises(RuntimeError, match='network budget'):
        national.PublicReader(resumed, Session(Response())).get(URL, 4, span=(8, 11), total=20)
    assert resumed.network_bytes == 4


def test_new_files_never_overwrite_existing_source_or_escape_folder(tmp_path, monkeypatch):
    b = budget(tmp_path, monkeypatch)
    p = b.write_once('raw/example.bin', b'original')
    assert b.write_once('raw/example.bin', b'original') == p
    with pytest.raises(ValueError, match='overwrite'): b.write_once('raw/example.bin', b'changed')
    with pytest.raises(ValueError, match='escaped'): b.write_once('../outside.bin', b'bad')
    assert p.read_bytes() == b'original'


def test_memory_storage_and_disk_limits_are_checked_before_write(tmp_path, monkeypatch):
    b = budget(tmp_path, monkeypatch, storage_limit=3)
    with pytest.raises(RuntimeError, match='artifact budget'): b.write_once('too-big', b'1234')
    monkeypatch.setattr(national, 'memory_usage', lambda: (national.MEMORY_LIMIT + 1, national.MEMORY_LIMIT + 1))
    with pytest.raises(RuntimeError, match='memory budget'): b.check()
    monkeypatch.setattr(national, 'memory_usage', lambda: (10, 20))
    b.disk_reserve = 10**30
    with pytest.raises(RuntimeError, match='disk reserve'): b.check()


def test_sparse_reader_cannot_fetch_a_hole():
    source = national.SparseFile(50, [(0, b'PAR1'), (40, b'footerdata')])
    assert source.read(4) == b'PAR1'
    with pytest.raises(ValueError, match='fallback prohibited'): source.read(1)
    source.seek(-10, 2)
    assert source.read(6) == b'footer'


def parquet_fixture():
    table = pa.Table.from_pylist([
        {'id': 'outside', 'bbox': {'xmin': 10., 'ymin': 10., 'xmax': 11., 'ymax': 11.}},
        {'id': 'inside', 'bbox': {'xmin': 127., 'ymin': 36., 'xmax': 127.1, 'ymax': 36.1}},
    ])
    buf = io.BytesIO(); pq.write_table(table, buf, row_group_size=1)
    return buf.getvalue()


def test_row_group_statistics_select_only_intersecting_groups_and_decodes_exact_spans():
    payload = parquet_fixture(); parquet = pq.ParquetFile(io.BytesIO(payload))
    plan = national.row_group_plan(parquet.metadata, national.BBOX)
    assert [g['index'] for g in plan] == [1]
    g = plan[0]
    sparse = national.SparseFile(len(payload), [(g['start'], payload[g['start']:g['end'] + 1])])
    selected = pq.ParquetFile(pa.PythonFile(sparse), metadata=parquet.metadata, pre_buffer=False).read_row_group(1)
    assert selected['id'].to_pylist() == ['inside']


def test_row_group_plan_refuses_missing_stats_and_excess_records(monkeypatch):
    buf = io.BytesIO(); pq.write_table(pa.table({'id': ['a']}), buf)
    with pytest.raises(ValueError, match='statistics'):
        national.row_group_plan(pq.ParquetFile(io.BytesIO(buf.getvalue())).metadata, national.BBOX)
    monkeypatch.setattr(national, 'ROW_LIMIT', 0)
    with pytest.raises(ValueError, match='100,000'):
        national.row_group_plan(pq.ParquetFile(io.BytesIO(parquet_fixture())).metadata, national.BBOX)


def family():
    geom = box(127.002, 36.002, 127.006, 36.006)
    parent = {'id': 'parent', 'geometry': geom.wkb, 'has_parts': True}
    parts = [
        {'id': 'a', 'building_id': 'parent', 'geometry': box(127.002, 36.002, 127.004, 36.006).wkb, 'height': 15.},
        {'id': 'b', 'building_id': 'parent', 'geometry': box(127.004, 36.002, 127.006, 36.006).wkb, 'height': 30.},
    ]
    normalized = [{'feature': {'type': 'Feature', 'id': 'parent', 'geometry': mapping(geom), 'properties': {
        'height': 30., 'height_method': 'source', 'base_height': 46.5, 'min_height': 0,
        'dataset_version': national.RELEASE, 'provenance': {'source_record_id': 'parent'},
    }}, 'asset_id': 'normalized-buildings-test', 'source_sha256': 'a' * 64}]
    return parent, parts, normalized


def test_complete_group_preserves_ids_shape_height_and_shared_parent_ground():
    parent, parts, normalized = family(); original = copy.deepcopy((parent, parts, normalized))
    report, candidates = national.group_decision(parent, parts, normalized)
    assert report['reasons'] == []
    assert not report['candidate'] and not report['published_parent_verified']
    assert [f['id'] for f in candidates] == ['a', 'b']
    for source, result in zip(parts, candidates):
        assert result['geometry'] == mapping(national.shapely.from_wkb(source['geometry']))
        p = result['properties']
        assert p['source_record_id'] == source['id'] and p['parent_source_record_id'] == 'parent'
        assert p['height'] == source['height'] and p['base_height'] == 46.5
        assert p['render_height'] == source['height'] and p['render_min_height'] == 0
        assert not p['render_eligible'] and p['candidate_render_eligible'] and p['proposal_only']
        assert not p['ground_accuracy_verified']
    assert (parent, parts, normalized) == original


@pytest.mark.parametrize('change,reason', [
    ('missing-height', 'not_all_parts_have_eligible_height_and_supported_roof'),
    ('floating', 'not_all_parts_have_eligible_height_and_supported_roof'),
    ('roof', 'not_all_parts_have_eligible_height_and_supported_roof'),
    ('gap', 'parts_do_not_cover_parent'),
    ('overlap', 'part_volumes_overlap'),
    ('parent-shape', 'normalized_parent_geometry_differs'),
    ('version', 'normalized_source_identity_or_release_mismatch'),
])
def test_partial_unresolved_overlapping_or_changed_groups_never_replace_parent(change, reason):
    parent, parts, normalized = family()
    if change == 'missing-height': parts[1]['height'] = None
    if change == 'floating': parts[1]['min_height'] = 10
    if change == 'roof': parts[1]['roof_shape'] = 'gabled'
    if change == 'gap': parts.pop()
    if change == 'overlap': parts[1]['geometry'] = parent['geometry']
    if change == 'parent-shape': normalized[0]['feature']['geometry'] = mapping(box(127.003, 36.003, 127.004, 36.004))
    if change == 'version': normalized[0]['feature']['properties']['dataset_version'] = 'other-release'
    report, candidates = national.group_decision(parent, parts, normalized)
    assert reason in report['reasons']
    assert not report['candidate'] and not candidates


def test_duplicates_or_missing_published_reference_cannot_promote_group():
    parent, parts, normalized = family()
    report, candidates = national.group_decision(parent, parts, normalized, duplicate_ids={'a'})
    assert report['reasons'] == ['duplicate_part_id'] and not candidates
    report, _ = national.group_decision(parent, parts, normalized)
    national.verify_published_parents([report], {'parent': normalized}, None, {'verified': False}, None)
    assert report['reasons'] == ['published_root_reference_not_verified'] and not report['candidate']


@pytest.mark.parametrize('invalid_target', ['parent', 'part'])
def test_invalid_source_wkb_is_quarantined_instead_of_terminating_other_groups(invalid_target):
    parent, parts, normalized = family()
    (parent if invalid_target == 'parent' else parts[0])['geometry'] = b'invalid'
    report, candidates = national.group_decision(parent, parts, normalized)
    assert 'invalid_' + invalid_target + '_geometry' in report['reasons']
    assert not candidates and not report['candidate']


def test_public_reference_requires_current_release_and_equal_root_hash(tmp_path, monkeypatch):
    b = budget(tmp_path, monkeypatch)
    (tmp_path / 'public/data').mkdir(parents=True)
    catalog = {'release_id': national.PUBLIC_RELEASE, 'assets': [{'id': 'buildings-korea-retiled', 'format': '3d-tiles',
        'url': '/data/tileset.json', 'sha256': 'a' * 64, 'version': national.RELEASE}]}
    (tmp_path / 'public/data/catalog.json').write_text(json.dumps(catalog))
    remote = copy.deepcopy(catalog); remote['assets'][0]['sha256'] = 'b' * 64
    response = Response(json.dumps(remote).encode(), status=200, headers={'Content-Length': str(len(json.dumps(remote).encode()))})
    evidence, tree = national.public_proof(b, national.PublicReader(b, Session(response)), tmp_path)
    assert not evidence['verified'] and tree is None
    assert Path(b.folder / 'references/published-parent-root.json').is_file()


def test_candidate_remains_compatible_with_existing_mesh_identity_metadata(tmp_path):
    from pipeline.mesh import write_glb
    from pipeline.mesh_metadata_audit import audit_glb
    parent, parts, normalized = family()
    report, candidates = national.group_decision(parent, parts, normalized)
    report.update({'candidate': True, 'published_parent_verified': True})
    replacement = {'replace_parent_ids': ['parent'], 'groups': [report]}
    original = copy.deepcopy(candidates)
    inputs = national.prepare_candidate_mesh(candidates, replacement)
    path = tmp_path / 'private-candidate.glb'
    result = write_glb(inputs, path)
    assert result[1] == 2
    assert audit_glb(path)['features'] == 2
    assert national.glb_strings(path, 'source_record_id') == ['a', 'b']
    originals = [json.loads(p) for p in national.glb_strings(path, 'original_properties')]
    assert [p['building_id'] for p in originals] == ['parent', 'parent']
    assert [p['height'] for p in originals] == [15., 30.]
    assert 'parent' not in national.glb_strings(path, 'source_record_id')
    assert candidates == original
    assert inputs[0]['properties']['min_height'] == 0
    assert inputs[0]['properties']['raw_min_height'] is None
    with pytest.raises(ValueError, match='Partial or duplicated'):
        national.prepare_candidate_mesh(candidates[:-1], replacement)
    report['published_parent_verified'] = False
    with pytest.raises(ValueError, match='verified complete'):
        national.prepare_candidate_mesh(candidates, replacement)


def test_published_parent_must_occur_once_in_verified_current_detail(tmp_path, monkeypatch):
    from pipeline.mesh import write_glb
    b = budget(tmp_path, monkeypatch)
    parent, parts, normalized = family()
    folder = tmp_path / 'public/data/hierarchy'
    folder.mkdir(parents=True)
    feature = normalized[0]['feature']
    feature['properties']['source_record_id'] = 'parent'
    tile = write_glb([feature], folder / 'detail.glb')[0]
    tile['content']['uri'] = 'detail.glb'
    tile['extras']['lod_role'] = 'detail'
    tile['extras']['sha256'] = national.digest(folder / 'detail.glb')
    report, _ = national.group_decision(parent, parts, normalized)
    national.verify_published_parents([report], {'parent': normalized}, {'root': tile},
        {'verified': True, 'building_root': {'url': '/data/hierarchy/tileset.json'}}, b, tmp_path)
    assert report['candidate'] and report['published_parent_verified']
    assert report['published_tiles'][0]['occurrences'] == 1
    duplicate = copy.deepcopy(feature)
    duplicate['properties'] = dict(feature['properties'])
    tile = write_glb([feature, duplicate], folder / 'duplicate.glb')[0]
    tile['content']['uri'] = 'duplicate.glb'; tile['extras']['lod_role'] = 'detail'
    tile['extras']['sha256'] = national.digest(folder / 'duplicate.glb')
    report, _ = national.group_decision(parent, parts, normalized)
    national.verify_published_parents([report], {'parent': normalized}, {'root': tile},
        {'verified': True, 'building_root': {'url': '/data/hierarchy/tileset.json'}}, b, tmp_path)
    assert not report['candidate'] and not report['published_parent_verified']
    assert report['reasons'] == ['published_parent_missing_or_duplicate']


@pytest.mark.parametrize('global_count,complete,accepted', [(2, True, True), (3, True, False), (1, True, False), (2, False, False)])
def test_global_sibling_count_or_incomplete_scan_blocks_replacement(global_count, complete, accepted):
    parent, parts, normalized = family()
    report, _ = national.group_decision(parent, parts, normalized)
    report.update({'candidate': True, 'published_parent_verified': True})
    original = {'groups': [report], 'replace_parent_ids': ['parent'], 'render_feature_count_delta': 1}
    before = copy.deepcopy(original)
    result = national.apply_sibling_gate(original, {'parent': global_count}, complete=complete)
    assert len(result['groups']) == int(accepted)
    assert result['replace_parent_ids'] == (['parent'] if accepted else [])
    if accepted:
        assert result['groups'][0]['global_sibling_count_verified']
    else:
        assert not result['blocked_groups'][0]['candidate']
        assert not result['blocked_groups'][0]['global_sibling_count_verified']
    assert original == before


def review_files(tmp_path, monkeypatch):
    b = budget(tmp_path, monkeypatch)
    parent, parts, normalized = family()
    report, candidates = national.group_decision(parent, parts, normalized)
    report.update({'candidate': True, 'published_parent_verified': True, 'global_sibling_count_verified': True})
    plan = {'groups': [report], 'replace_parent_ids': ['parent'], 'global_sibling_scan_complete': True,
            'parent_height_review_complete': True}
    b.json('final/replacement-plan.json', plan)
    b.json('final/candidate-parts.geojson', {'type': 'FeatureCollection', 'features': candidates})
    buf = io.BytesIO(); pq.write_table(pa.Table.from_pylist(parts), buf)
    b.write_once('raw/korea-group-0.parquet', buf.getvalue())
    return b.folder


def test_private_review_mesh_roundtrips_actual_original_input_and_never_overwrites(tmp_path, monkeypatch):
    folder = review_files(tmp_path, monkeypatch)
    result = national.verify_candidate_meshes(folder)
    assert result['private_meshes'] == 1 and result['features'] == 2
    assert result['raw_id_geometry_attributes_equal'] and result['glb_id_original_properties_equal']
    assert not result['public_assets_changed'] and result['coordinate_rounding_max_m'] <= .005
    with pytest.raises(ValueError, match='already exists'):
        national.verify_candidate_meshes(folder)


def test_private_review_mesh_rejects_changed_coordinates_before_writing(tmp_path, monkeypatch):
    folder = review_files(tmp_path, monkeypatch)
    path = folder / 'final/candidate-parts.geojson'
    document = json.loads(path.read_bytes())
    document['features'][0]['geometry']['coordinates'][0][0][0] += .001
    path.write_bytes(national.encoded(document))
    with pytest.raises(ValueError, match='changed source coordinates'):
        national.verify_candidate_meshes(folder)
    assert not (folder / 'final/meshes').exists()


def test_private_review_mesh_requires_completed_global_scalar_scan(tmp_path, monkeypatch):
    folder = review_files(tmp_path, monkeypatch)
    path = folder / 'final/replacement-plan.json'
    plan = json.loads(path.read_bytes()); plan['global_sibling_scan_complete'] = False
    path.write_bytes(national.encoded(plan))
    with pytest.raises(ValueError, match='Global sibling completeness'):
        national.verify_candidate_meshes(folder)
    assert not (folder / 'final/meshes').exists()


@pytest.mark.parametrize('parent_height,state,accepted', [
    (30., 'source_values_equal', True), (None, 'source_parent_absent', True),
    (40., 'source_values_conflict', False), (20., 'source_values_conflict', False),
])
def test_source_parent_and_part_height_conflicts_require_review(parent_height, state, accepted):
    parent, parts, normalized = family()
    group, features = national.group_decision(parent, parts, normalized)
    group.update({'candidate': True, 'published_parent_verified': True})
    plan = {'groups': [group], 'replace_parent_ids': ['parent']}
    before = copy.deepcopy((plan, features))
    final, retained = national.apply_parent_height_gate(plan, features,
        {'parent': {'id': 'parent', 'height': parent_height, 'names': {'primary': '원천 부모 이름'}}})
    assert len(final['groups']) == int(accepted)
    result_group = (final['groups'] if accepted else final['blocked_height_groups'])[0]
    assert result_group['parent_height_consistency'] == state
    if accepted:
        assert retained[0]['properties']['name'] == '원천 부모 이름'
        assert retained[0]['properties']['display_name_source'] == 'parent_source'
        assert retained[0]['properties']['original_properties'] == features[0]['properties']['original_properties']
    else:
        assert not retained and not final['replace_parent_ids']
    assert (plan, features) == before
