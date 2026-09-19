import copy
import json
from pathlib import Path

import pytest

from pipeline import water_partition as water
from pipeline.retile import restore_features


def feature(identity='water-a', lon=127, lat=36):
    return {'type': 'Feature', 'id': identity,
            'properties': {'kind': 'water', 'source_record_id': identity, 'name': '원본 호수', 'quality_flags': ['source'],
                           'source_id': 'osm', 'nested': {'nil': None, 'flag': True, 'integer': 1}},
            'geometry': {'type': 'Polygon', 'coordinates': [[[lon, lat], [lon+.01, lat], [lon+.01, lat+.01], [lon, lat]]]}}


def fixture(tmp_path, features=None):
    features = features or [feature(), feature('water-b', 127.5)]
    public = tmp_path/'public/data'; public.mkdir(parents=True)
    source = public/'original.geojson'; raw = water.encoded({'type': 'FeatureCollection', 'features': features}); source.write_bytes(raw)
    rows = water.feature_rows(features)
    asset = {'id': water.SOURCE_ID, 'url': '/data/original.geojson', 'format': 'geojson', 'layer': 'terrain', 'source_id': 'osm',
             'version': 'original-version', 'bbox': [124.5, 33, 132, 38.7], 'count': len(features), 'feature_count': len(features),
             'vertex_count': sum(row['vertices'] for row in rows), 'sha256': water.sha(raw), 'byte_length': len(raw)}
    other = {**asset, 'id': 'unrelated', 'url': '/data/unrelated.geojson', 'source_id': 'different', 'count': 7, 'vertex_count': 35}
    catalog = {'schema_version': 1, 'release_id': 'previous', 'assets': [other, asset],
               'layers': [{'id': 'terrain', 'record_count': len(features)+7}], 'sources': [{'id': 'osm', 'license': 'retained'}]}
    catalog_path = tmp_path/'input.json'; catalog_path.write_bytes(water.encoded(catalog))
    return catalog_path, public, tmp_path/'candidate', catalog, source


def build(inputs, **kwargs):
    catalog, public, output, _, _ = inputs
    return water.build_candidate(catalog, public=public, output=output, disk_reserve=0, **kwargs)


def test_whole_features_preserve_holes_multipart_properties_and_foreign_fields(tmp_path):
    a = feature(); a['geometry']['coordinates'].append([[127.002, 36.002], [127.003, 36.002], [127.003, 36.003], [127.002, 36.002]])
    a['bbox'] = [127, 36, 127.01, 36.01]; a['custom_foreign_member'] = {'kept': [1, True, None]}
    b = feature('multipart', 128); b['properties']['nested']['flag'] = 1
    b['geometry'] = {'type': 'MultiPolygon', 'coordinates': [b['geometry']['coordinates'], feature('remote-part', 128.2)['geometry']['coordinates']]}
    originals = [a, b]; before = water.encoded(originals); inputs = fixture(tmp_path, originals)
    input_before = inputs[0].read_bytes(); source_before = inputs[4].read_bytes(); result = build(inputs)
    work = Path(result['work']); files = json.loads((work/'files.json').read_bytes()); restored = []
    for item in files:
        payload = (work/item['path']).read_bytes(); assert water.sha(payload) == item['sha256']; assert len(payload) == item['bytes']
        restored.extend(restore_features(json.loads(payload)))
    assert water.encoded(sorted(restored, key=lambda f: f['id'])) == water.encoded(sorted(originals, key=lambda f: f['id']))
    assert water.encoded(originals) == before
    assert inputs[0].read_bytes() == input_before and inputs[4].read_bytes() == source_before
    assert sorted(p.name for p in inputs[1].rglob('*') if p.is_file()) == ['original.geojson']
    audit = result['audit']; assert audit['coordinate_delta'] == 0 and audit['other_asset_payloads_unchanged']
    assert audit['logical_source_feature_count'] == 2 and audit['posted_representation_feature_count'] == 4
    candidate = json.loads(Path(result['catalog']).read_bytes())
    assert candidate['layers'] == inputs[3]['layers'] and candidate['sources'] == inputs[3]['sources']
    assert candidate['assets'][0] == inputs[3]['assets'][0]


def test_wide_feature_is_selected_from_its_complete_bbox_not_only_its_owning_cell():
    f = feature(); f['geometry']['coordinates'] = [[[126, 36], [128, 36], [128, 36.1], [126, 36]]]
    rows = water.feature_rows([f]); parts = water.partition_rows(rows)
    assert len(parts) == 1 and parts[0][0][0] == 508
    asset = {'bbox': water.bounds(parts[0][2]), 'max_camera_height': 60000}
    assert water.active_assets([asset], [126, 36, 126.01, 36.01], 5000) == [asset]
    assert asset['bbox'] == [126, 36, 128, 36.1]


def test_vertex_budget_splits_cell_without_cutting_or_duplicating_features():
    rows = water.feature_rows([feature(str(i), 127+i*.001) for i in range(5)])
    groups = water.partition_rows(rows, vertex_target=8)
    assert [sum(r['vertices'] for r in group) for _, _, group in groups] == [8, 8, 4]
    assert [r['id'] for _, _, group in groups for r in group] == ['0', '1', '2', '3', '4']


def test_oversized_individual_feature_is_kept_whole_and_explicitly_flagged(tmp_path):
    inputs = fixture(tmp_path, [feature('a'), feature('b', 127.002)])
    result = build(inputs, vertex_target=3)
    assets = json.loads((Path(result['work'])/'assets.json').read_bytes())[1:]
    assert len(assets) == 2
    assert all(a['vertex_budget_exceeded'] and a['vertex_count'] == 4 and a['feature_count'] == 1 for a in assets)
    assert [a['oversize_feature_ids'] for a in assets] == [['a'], ['b']]
    assert result['audit']['detail_vertex_count'] == 8


@pytest.mark.parametrize('height,original_visible', [(59999, False), (60000, True), (60001, True)])
def test_scale_boundary_uses_exactly_one_representation_without_overview_fallback(tmp_path, height, original_visible):
    result = build(fixture(tmp_path)); assets = json.loads((Path(result['work'])/'assets.json').read_bytes())
    selected = water.active_assets(assets, [124.5, 33, 132, 38.7], height)
    assert sum(a['count'] for a in selected) == 2 and sum(a['vertex_count'] for a in selected) == 8
    assert (selected == [assets[0]]) is original_visible
    assert assets[0]['min_camera_height'] == 60000 and 'detail_level' not in assets[0]
    assert all(a.get('detail_level') != 'overview' for a in assets)


def test_repeated_run_verifies_existing_bytes_without_rewriting_them(tmp_path):
    inputs = fixture(tmp_path); first = build(inputs); work = Path(first['work'])
    prior = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in work.rglob('*') if p.is_file()}
    second = build(inputs)
    assert first['catalog_sha256'] == second['catalog_sha256'] and first['work'] == second['work']
    assert all(p.read_bytes() == payload and p.stat().st_mtime_ns == timestamp for p, (payload, timestamp) in prior.items())


def test_different_existing_bytes_fail_without_overwriting_any_candidate(tmp_path):
    inputs = fixture(tmp_path); result = build(inputs); work = Path(result['work'])
    manifest = json.loads((work/'files.json').read_bytes()); damaged = work/manifest[0]['path']; damaged.write_bytes(b'preserve this conflicting file')
    old_catalog = (work/'catalog.json').read_bytes()
    with pytest.raises(ValueError, match='differs'):
        build(inputs)
    assert damaged.read_bytes() == b'preserve this conflicting file' and (work/'catalog.json').read_bytes() == old_catalog


@pytest.mark.parametrize('change', ['sha', 'vertices', 'count', 'byte_length', 'scale'])
def test_source_contract_changes_are_rejected_before_output(tmp_path, change):
    inputs = fixture(tmp_path); catalog = inputs[3]; source = catalog['assets'][1]
    if change == 'sha': source['sha256'] = '0'*64
    elif change == 'scale': source['detail_level'] = 'overview'
    else: source[{'vertices': 'vertex_count', 'count': 'count', 'byte_length': 'byte_length'}[change]] += 1
    inputs[0].write_bytes(water.encoded(catalog))
    with pytest.raises(ValueError): build(inputs)
    assert not inputs[2].exists()


@pytest.mark.parametrize('url', ['https://external.invalid/water.geojson', '/data/../private.geojson', '/data/osm/a%2f.geojson', '/data/osm\\a.geojson', '/data/osm/a.geojson?key=invalid'])
def test_source_uri_cannot_escape_or_become_a_network_request(tmp_path, url):
    with pytest.raises(ValueError): water.source_path(url, tmp_path)


def test_invalid_geometry_and_duplicate_identity_are_not_silently_repaired():
    with pytest.raises(ValueError, match='duplicate'): water.feature_rows([feature(), feature()])
    f = feature(); f['geometry']['coordinates'][0][-1] = [127, 36.2]
    with pytest.raises(ValueError, match='Open'): water.feature_rows([f])
    f = feature(); f['geometry']['coordinates'][0][0][0] = float('nan')
    with pytest.raises(ValueError, match='coordinate'): water.feature_rows([f])


def test_disk_and_public_output_guards_run_before_writes(tmp_path):
    inputs = fixture(tmp_path)
    with pytest.raises(ValueError, match='reserve'):
        water.build_candidate(inputs[0], public=inputs[1], output=inputs[2], disk_reserve=10**20)
    with pytest.raises(ValueError, match='public'):
        water.build_candidate(inputs[0], public=inputs[1], output=inputs[1]/'new', disk_reserve=0)
    assert not inputs[2].exists()


def test_index_projection_is_read_only_and_uses_scale_groups(tmp_path):
    inputs = fixture(tmp_path); original = copy.deepcopy(inputs[3]); before = water.index_hashes(original)
    result = build(inputs); candidate = json.loads(Path(result['catalog']).read_bytes()); after = water.index_hashes(candidate)
    projection = result['audit']['storage_projection']
    assert projection['new_index_hashes'] == len(after-before)
    assert projection['retired_index_hashes'] == len(before-after)
    assert projection['conservative_projected_static_files'] == 17537+result['audit']['detail_files']+len(after-before)+4
    assert original == inputs[3] and not (inputs[1]/'indexes').exists()
