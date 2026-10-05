import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import frontend_release as frontend
from pipeline import static_release as release
from pipeline.core import atomic_json, digest


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    # Tiny frontend fixtures must not depend on production-volume free space.
    monkeypatch.setattr(release, 'DISK_RESERVE_BYTES', 0)
    root = tmp_path / 'workspace'
    monkeypatch.setattr(frontend, 'POI_SOURCE_ROOT', root / 'canonical/src/data/property-poi')
    base = root / 'public/data'
    source = base / 'roads/road.geojson'
    atomic_json(source, {'type': 'FeatureCollection', 'features': []})
    asset = {
        'id': 'roads', 'layer': 'infrastructure', 'format': 'geojson',
        'url': '/data/roads/road.geojson', 'bbox': [127, 36, 128, 37],
        'source_id': 'fixture', 'version': 'v1', 'count': 0,
        'sha256': digest(source), 'feature_count': 0, 'vertex_count': 0,
        'byte_length': source.stat().st_size,
    }
    catalog = root / 'source-catalog.json'
    atomic_json(catalog, {'schema_version': 1, 'release_id': 'fixture',
                         'generated_at': '2026-09-20T00:00:00Z', 'layers': [], 'assets': [asset]})
    client, worker = root / 'dist/client', root / 'dist/korea_replay'
    client.mkdir(parents=True)
    worker.mkdir(parents=True)
    frontend_files = {
        'index.html': '<!doctype html><script src="/assets/index-12345678.js"></script>',
        'download-gate.js': 'globalThis.gate = true;',
        '.assetsignore': 'wrangler.json\n.dev.vars\n',
        'assets/index-12345678.js': 'export const build=1;',
        'assets/index-abcdefgh.css': 'body{color:black}',
        'assets/Map2D-87654321.js': 'export const map=1;',
        'assets/search.worker--ohVzdKT.js': 'self.onmessage=()=>{};',
        'cesium/Workers/engine.js': 'export const engine=1;',
        'cesium/Assets/texture.png': 'unchanged fixture image',
    }
    for name, content in frontend_files.items():
        target = client / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    (worker / 'index.js').write_text('export default {};', encoding='utf-8')
    output = root / '.local/deploy'
    plan = release.prepare(catalog, client_dir=client, base=base, output=output)
    first = release.stage(plan, output=output, worker_dir=worker)
    bundle = Path(first['bundle'])
    before = {p.relative_to(bundle).as_posix(): p.read_bytes() for p in bundle.rglob('*') if p.is_file()}
    # Replace only a disposable Vite fixture, never a project file.
    (client / 'assets/index-12345678.js').unlink()
    (client / 'assets/index-abcdefgh.js').write_text('export const build=2;', encoding='utf-8')
    (client / 'index.html').write_text(
        '<!doctype html><script src="/assets/index-abcdefgh.js"></script>', encoding='utf-8')
    return SimpleNamespace(root=root, base=base, catalog=catalog, client=client, worker=worker,
                           output=output, bundle=bundle, first=first, before=before)


def test_navigation_asset_requires_exact_audited_bytes(fixture):
    source = Path(__file__).resolve().parents[1] / 'src/data/seoul-property-navigation.json'
    target = fixture.client / 'assets/seoul-property-navigation-JeSm217J.json'
    target.write_bytes(source.read_bytes())
    # Use the same prior metadata contract as the production restager.
    _, assets = frontend._prior_metadata(fixture.bundle)
    entries = frontend._frontend_entries(fixture.client, assets)
    assert any(row['target'] == target.relative_to(fixture.client).as_posix() for row in entries)
    target.write_bytes(source.read_bytes().replace(b'A10025850', b'A10025851'))
    with pytest.raises(ValueError, match='Copied frontend assets changed'):
        frontend._frontend_entries(fixture.client, assets)


def restage(fixture, **kwargs):
    return frontend.restage(root=fixture.root, reuse_bundle=fixture.bundle,
                            client_dir=fixture.client, worker_dir=fixture.worker,
                            output=fixture.output, **kwargs)


def test_additional_shared_module_requires_exact_audited_bytes(fixture, monkeypatch):
    target = fixture.client / 'assets/property-summary-client-abcdefgh.js'
    target.write_bytes(b'export const summary=1;')
    monkeypatch.setattr(frontend, 'AUDITED_ADDITIONAL_CHUNKS', {
        ('property-summary-client', 'js'): {'sha256': digest(target), 'bytes': target.stat().st_size},
    })
    _, assets = frontend._prior_metadata(fixture.bundle)
    entries = frontend._frontend_entries(fixture.client, assets)
    assert any(row['target'] == target.relative_to(fixture.client).as_posix() for row in entries)
    target.write_bytes(b'export const summary=2;')
    with pytest.raises(ValueError, match='differs from audited bytes'):
        frontend._frontend_entries(fixture.client, assets)
    assert_prior_unchanged(fixture)


def assert_prior_unchanged(fixture):
    assert {p.relative_to(fixture.bundle).as_posix(): p.read_bytes()
            for p in fixture.bundle.rglob('*') if p.is_file()} == fixture.before


def assert_no_new_bundle(fixture):
    assert list((fixture.output / 'bundles').iterdir()) == [fixture.bundle]
    assert json.loads((fixture.output / 'static-stage.json').read_bytes()) == fixture.first


def test_frontend_only_preserves_data_contracts_and_hardlinks_without_raw_audit(fixture, monkeypatch):
    for name in ('prepare', 'build_catalog', 'dependencies', 'verify_geometry_budgets'):
        monkeypatch.setattr(release, name, lambda *args, **kwargs: pytest.fail('Raw geometry audit must not run'))
    # Local raw/catalog edits cannot become a published data change in this path.
    (fixture.base / 'roads/road.geojson').write_bytes(b'changed unpublished original')
    fixture.catalog.write_bytes(b'changed unpublished catalog')
    result = restage(fixture)
    target = Path(result['bundle'])
    assert target != fixture.bundle
    assert result['release_id'] == fixture.first['release_id']
    assert result['catalog_hash'] == fixture.first['catalog_hash']
    assert result['config_hash'] == fixture.first['config_hash']
    assert result['deployment_contract'] == fixture.first['deployment_contract']
    assert result['frontend_only']['receipt_hash'] == digest(fixture.bundle / 'receipt.json')
    assert result['frontend_only']['source_catalog_hash'] == fixture.first['catalog_hash']
    old = json.loads((fixture.bundle / 'asset-manifest.json').read_bytes())
    new = json.loads((target / 'asset-manifest.json').read_bytes())
    old_data = [entry for entry in old if entry['target'].startswith('data/')]
    assert {row['target']: row for row in new if row['target'].startswith('data/')} == {
        row['target']: row for row in old_data}
    for row in old_data:
        assert digest(target / 'client' / row['target']) == row['sha256']
        if row['target'] != 'data/catalog.json':
            assert os.path.samefile(target / 'client' / row['target'], fixture.bundle / 'client' / row['target'])
    assert (target / 'client/assets/index-abcdefgh.js').read_bytes() == b'export const build=2;'
    assert not (target / 'client/assets/index-12345678.js').exists()
    assert [(row['target'], row['sha256']) for row in result['worker_files']] == [
        (row['target'], row['sha256']) for row in fixture.first['worker_files']]
    assert_prior_unchanged(fixture)


def test_exact_public_seoul_point_asset_can_be_added_without_mutating_prior_data(fixture):
    source = Path(__file__).resolve().parents[1] / 'src/data/seoul-kapt-points-8360eb2d88be0ab4.geojson'
    target = fixture.client / 'assets/seoul-kapt-points-8360eb2d88be0ab4-Fbg8DdT6.geojson'
    target.write_bytes(source.read_bytes())
    result = restage(fixture)
    staged = Path(result['bundle']) / 'client' / target.relative_to(fixture.client)
    assert digest(staged) == frontend.SEOUL_KAPT_GEOJSON_SHA
    assert_prior_unchanged(fixture)


def test_joined_seoul_point_asset_is_exact_and_replaces_prior_point_version(fixture):
    old_source = Path(__file__).resolve().parents[1] / 'src/data/seoul-kapt-points-8360eb2d88be0ab4.geojson'
    new_source = Path(__file__).resolve().parents[1] / 'src/data/seoul-kapt-points-33058dae0a1d86c3.geojson'
    old_target = fixture.client / 'assets/seoul-kapt-points-8360eb2d88be0ab4-Fbg8DdT6.geojson'
    new_target = fixture.client / 'assets/seoul-kapt-points-33058dae0a1d86c3-Hh1M2n3P.geojson'
    old_target.write_bytes(old_source.read_bytes())
    previous = restage(fixture)
    previous_bundle = Path(previous['bundle'])
    fixture.bundle = previous_bundle
    old_target.unlink()
    new_target.write_bytes(new_source.read_bytes())
    result = restage(fixture)
    staged = Path(result['bundle']) / 'client' / new_target.relative_to(fixture.client)
    assert digest(staged) == frontend.SEOUL_KAPT_JOINED_SHA
    assert not (Path(result['bundle']) / 'client' / old_target.relative_to(fixture.client)).exists()


def test_recent_sale_marker_asset_replaces_joined_point_version(fixture):
    old_source = Path(__file__).resolve().parents[1] / 'src/data/seoul-kapt-points-33058dae0a1d86c3.geojson'
    new_source = Path(__file__).resolve().parents[1] / 'src/data/seoul-kapt-points-b63b62af834062de.geojson'
    old_target = fixture.client / 'assets/seoul-kapt-points-33058dae0a1d86c3-Hh1M2n3P.geojson'
    new_target = fixture.client / 'assets/seoul-kapt-points-b63b62af834062de-Kk2N3p4Q.geojson'
    old_target.write_bytes(old_source.read_bytes())
    previous = restage(fixture)
    fixture.bundle = Path(previous['bundle'])
    old_target.unlink()
    new_target.write_bytes(new_source.read_bytes())
    result = restage(fixture)
    staged = Path(result['bundle']) / 'client' / new_target.relative_to(fixture.client)
    assert digest(staged) == frontend.SEOUL_KAPT_RECENT_SHA
    assert not (Path(result['bundle']) / 'client' / old_target.relative_to(fixture.client)).exists()


def test_seoul_point_asset_name_cannot_hide_different_content(fixture):
    target = fixture.client / 'assets/seoul-kapt-points-8360eb2d88be0ab4-Fbg8DdT6.geojson'
    target.write_bytes(b'{"type":"FeatureCollection","features":[]}')
    with pytest.raises(ValueError, match='Copied frontend assets changed'):
        restage(fixture)
    assert_no_new_bundle(fixture)


@pytest.mark.parametrize('name', [
    'data/insert.json', 'DATA/insert.json', '.env', '.env.production', '.dev.vars',
    'assets/.env', 'assets/private-12345678.js', 'assets/newChunk-12345678.js',
    'assets/index-12345678.js.map', 'unknown.json', 'cesium/Assets/new.png', '_headers', '404.html',
])
def test_rejects_unknown_private_or_data_files_before_new_bundle(fixture, name):
    target = fixture.client / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'unapproved fixture')
    with pytest.raises(ValueError):
        restage(fixture)
    assert_no_new_bundle(fixture)
    assert_prior_unchanged(fixture)


@pytest.mark.parametrize('name', ['data', '.cache', 'assets/unknown'])
def test_rejects_even_empty_unknown_directories(fixture, name):
    (fixture.client / name).mkdir(parents=True)
    with pytest.raises(ValueError, match='Unknown frontend directory'):
        restage(fixture)
    assert_no_new_bundle(fixture)


@pytest.mark.parametrize('body', [
    b'-----BEGIN ' + b'PRIVATE KEY-----fixture',
    b'const value="ghp_' + b'abcdefghijklmnopqrstuvwxyz1234567890";',
    b'const config={"DATA_GO_KR_SERVICE_KEY":"do-not-publish-this-fixture-value"};',
    b'const config={SEOUL_SUBWAY_API_KEY:"do-not-publish-this-fixture-value"};',
    b'fetch("https://example.invalid/?serviceKey=do-not-publish-this-fixture-value")',
    b'const headers={"Authorization":"Bearer do-not-publish-this-fixture-value"};',
])
def test_secret_scan_rejects_literals_without_reporting_the_value(fixture, body, capsys):
    (fixture.client / 'assets/index-abcdefgh.js').write_bytes(body)
    with pytest.raises(ValueError, match='Credential-like') as caught:
        restage(fixture)
    assert 'do-not-publish' not in str(caught.value)
    assert 'ghp_abcdefghijklmnopqrstuvwxyz' not in str(caught.value)
    assert 'do-not-publish' not in capsys.readouterr().out
    assert_no_new_bundle(fixture)


@pytest.mark.parametrize('name', ['.assetsignore', 'cesium/Workers/engine.js', 'cesium/Assets/texture.png'])
def test_vendor_and_ignore_policy_changes_require_full_staging(fixture, name):
    (fixture.client / name).write_bytes(b'changed copied asset')
    with pytest.raises(ValueError, match='Copied frontend assets changed'):
        restage(fixture)
    assert_no_new_bundle(fixture)


def test_existing_immutable_chunk_url_cannot_receive_new_bytes(fixture):
    (fixture.client / 'assets/Map2D-87654321.js').write_bytes(b'export const changed=true;')
    with pytest.raises(ValueError, match='immutable frontend asset URL'):
        restage(fixture)
    assert_no_new_bundle(fixture)


@pytest.mark.parametrize('name', ['index.html', 'download-gate.js', 'assets/Map2D-87654321.js'])
def test_missing_frontend_artifacts_are_rejected(fixture, name):
    (fixture.client / name).unlink()
    with pytest.raises(ValueError, match='missing'):
        restage(fixture)
    assert_no_new_bundle(fixture)


@pytest.mark.parametrize('change', ['changed', 'extra', 'missing', 'unknown'])
def test_current_worker_must_be_the_exact_previous_runtime(fixture, change):
    if change == 'changed':
        (fixture.worker / 'index.js').write_bytes(b'export default {changed:true};')
    elif change == 'extra':
        (fixture.worker / 'extra.js').write_bytes(b'export const extra=true;')
    elif change == 'unknown':
        (fixture.worker / 'unexpected.json').write_bytes(b'{}')
    else:
        (fixture.worker / 'index.js').unlink()
    with pytest.raises(ValueError, match='Worker.*full staging'):
        restage(fixture)
    assert_no_new_bundle(fixture)


def test_worker_local_secret_file_is_never_opened_or_staged(fixture, monkeypatch):
    secret = fixture.worker / '.dev.vars'
    secret.write_bytes(b'LOCAL_TEST_SECRET=unread-fixture')
    (fixture.worker / 'wrangler.json').write_bytes(b'{"local":true}')
    read_bytes, read_text, open_file = Path.read_bytes, Path.read_text, Path.open
    def guarded_open(path, *args, **kwargs):
        if path == secret:
            pytest.fail('The helper must not read Worker .dev.vars')
        return open_file(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', guarded_open)
    result = restage(fixture)
    assert not (Path(result['bundle']) / 'worker/.dev.vars').exists()
    assert len(result['worker_files']) == 1
    assert read_bytes is not None and read_text is not None


@pytest.mark.parametrize('target', [
    'client/data/roads/road.geojson', 'client/index.html', 'worker/index.js',
    'asset-manifest.json', 'wrangler.json', 'client/data/catalog.json',
])
def test_previous_bundle_actual_byte_tampering_is_rejected(fixture, target):
    path = fixture.bundle / target
    content = path.read_bytes()
    path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    with pytest.raises((ValueError, json.JSONDecodeError)):
        restage(fixture)
    assert_no_new_bundle(fixture)


@pytest.mark.parametrize('field,value', [('bytes', 1), ('complete', False), ('bundle_id', '0' * 16)])
def test_previous_receipt_invariants_cannot_be_bypassed(fixture, field, value):
    path = fixture.bundle / 'receipt.json'
    receipt = json.loads(path.read_bytes())
    receipt[field] = value
    atomic_json(path, receipt)
    with pytest.raises(ValueError):
        restage(fixture)
    assert_no_new_bundle(fixture)


def test_changed_deployment_policy_requires_full_staging(fixture, monkeypatch):
    original = release.deployment_config
    def changed(release_id):
        config = original(release_id)
        config['compatibility_date'] = '2026-09-20'
        return config
    monkeypatch.setattr(release, 'deployment_config', changed)
    with pytest.raises(ValueError, match='Deployment configuration changed'):
        restage(fixture)
    assert_no_new_bundle(fixture)


def test_current_worker_race_cannot_change_the_staged_runtime(fixture, monkeypatch):
    original = release.stage
    def concurrent_build(plan, **kwargs):
        assert kwargs['worker_dir'] == fixture.bundle / 'worker'
        (fixture.worker / 'index.js').write_bytes(b'export default {laterBuild:true};')
        return original(plan, **kwargs)
    monkeypatch.setattr(release, 'stage', concurrent_build)
    result = restage(fixture)
    assert digest(Path(result['bundle']) / 'worker/index.js') == fixture.first['worker_files'][0]['sha256']
    assert_prior_unchanged(fixture)


def test_frontend_race_does_not_complete_or_promote_a_bundle(fixture, monkeypatch):
    original = release.stage
    def concurrent_build(plan, **kwargs):
        (fixture.client / 'assets/index-abcdefgh.js').write_bytes(b'changed after inspection')
        return original(plan, **kwargs)
    monkeypatch.setattr(release, 'stage', concurrent_build)
    with pytest.raises(ValueError, match='Source changed'):
        restage(fixture)
    candidates = [path for path in (fixture.output / 'bundles').iterdir() if path != fixture.bundle]
    assert all(not (path / 'receipt.json').exists() for path in candidates)
    assert json.loads((fixture.output / 'static-stage.json').read_bytes()) == fixture.first
    assert_prior_unchanged(fixture)


def test_disk_reserve_is_still_enforced_before_materializing(fixture, monkeypatch):
    monkeypatch.setattr(release.shutil, 'disk_usage', lambda path: SimpleNamespace(free=release.DISK_RESERVE_BYTES - 1))
    with pytest.raises(ValueError, match='30 GiB disk reserve'):
        restage(fixture)
    assert_no_new_bundle(fixture)
    assert_prior_unchanged(fixture)


def test_rejects_external_paths_and_path_traversal(fixture, tmp_path):
    for client in (tmp_path / 'external', fixture.root / 'dist/client/../client', fixture.bundle / 'client'):
        with pytest.raises(ValueError, match='workspace|traverse|distinct'):
            frontend.restage(root=fixture.root, reuse_bundle=fixture.bundle,
                             client_dir=client, worker_dir=fixture.worker, output=fixture.output)
    assert_no_new_bundle(fixture)


def test_rejects_symlinked_frontend_file(fixture, monkeypatch):
    # Windows may deny creating symlinks; emulate only lstat's real symlink flag
    # so the platform-independent no_links guard is still exercised.
    target = fixture.client / 'assets/index-abcdefgh.js'
    original = Path.lstat
    import stat
    def linked_stat(path, *args, **kwargs):
        if path == target:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', linked_stat)
    with pytest.raises(ValueError, match='links or junctions'):
        restage(fixture)
    assert_no_new_bundle(fixture)


def test_cli_requires_explicit_reuse_bundle():
    with pytest.raises(SystemExit) as caught:
        frontend.main([])
    assert caught.value.code == 2


@pytest.fixture
def region_assets(fixture, monkeypatch):
    """A small canonical source tree with production's exact inventory shape."""
    source_root = fixture.root / 'canonical/src/data'
    source_root.mkdir(parents=True)
    monkeypatch.setattr(frontend, 'REGION_SOURCE_ROOT', source_root)
    codes = [str(10000 + index) for index in range(252)]
    manifests = []
    for stem, prefix, directory, groups in (
        ('region-selection', 'boundary', 'region-selection-areas',
         [(code, 1) for code in [*[str(10 + i) for i in range(17)], *codes]]),
        ('region-selection-dongs', 'dongs', 'region-selection-dongs',
         [(code, 15 if i < 31 else 14) for i, code in enumerate(codes)]),
    ):
        refs = []
        for code, count in groups:
            rows = [[code if prefix == 'boundary' else code + f'{i:03}', 'Fixture region', 5, [['??']]]
                    for i in range(count)]
            body = json.dumps(rows, separators=(',', ':')).encode()
            source = source_root / directory / f'{prefix}-{code}.json'
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(body)
            refs.append([code, len(body), hashlib.sha256(body).hexdigest()])
            (fixture.client / f'assets/{prefix}-{code}-12345678.json').write_bytes(body)
        manifest = {'schema': 1, 'referenceDate': frontend.REGION_REFERENCE_DATE,
                    'namespace': 'SGIS administrative', 'archiveSha256': frontend.REGION_ARCHIVE_SHA,
                    'featureCount': sum(count for _, count in groups), 'chunks': refs}
        index = source_root / (stem + '.json')
        index.write_text(json.dumps(manifest), encoding='utf-8')
        manifests.append(index)
    return source_root, manifests


def test_region_assets_exact_521_inventory_can_be_staged_without_map_data_changes(fixture, region_assets):
    result = restage(fixture)
    bundle = Path(result['bundle'])
    manifest = json.loads((bundle / 'asset-manifest.json').read_bytes())
    assert len([row for row in manifest if frontend.REGION_ASSET.fullmatch(row['target'])]) == 521
    assert result['catalog_hash'] == fixture.first['catalog_hash']
    assert_prior_unchanged(fixture)


@pytest.mark.parametrize('change', ['unlisted', 'hash', 'missing', 'duplicate'])
def test_region_frontend_rejects_unlisted_altered_missing_or_duplicate_assets(fixture, region_assets, change):
    target = fixture.client / 'assets/boundary-10-12345678.json'
    if change == 'unlisted':
        target.rename(fixture.client / 'assets/boundary-99-12345678.json')
    elif change == 'hash':
        target.write_bytes(target.read_bytes().replace(b'Fixture', b'Changed'))
    elif change == 'missing':
        target.unlink()
    else:
        (fixture.client / 'assets/boundary-10-87654321.json').write_bytes(target.read_bytes())
    with pytest.raises(ValueError, match='Region asset|region assets|region asset identity'):
        restage(fixture)
    assert_no_new_bundle(fixture)


@pytest.mark.parametrize('change', ['traversal', 'revision', 'count', 'oversize', 'source_hash', 'credential'])
def test_region_canonical_manifest_and_payload_are_validated(fixture, region_assets, change):
    source_root, indexes = region_assets
    index = indexes[0]
    manifest = json.loads(index.read_bytes())
    if change == 'traversal':
        manifest['chunks'][0][0] = '../10'
    elif change == 'revision':
        manifest['referenceDate'] = '2030-01-01'
    elif change == 'count':
        manifest['featureCount'] -= 1
    elif change == 'oversize':
        manifest['chunks'][0][1] = frontend.REGION_MAX_FILE_BYTES + 1
    elif change == 'source_hash':
        manifest['chunks'][0][2] = '0' * 64
    else:
        source = source_root / 'region-selection-areas/boundary-10.json'
        body = b'[["10","serviceKey=do-not-publish-this-fixture-value",5,[["??"]]]]'
        source.write_bytes(body)
        manifest['chunks'][0][1:] = [len(body), hashlib.sha256(body).hexdigest()]
    index.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='[Rr]egion|Credential-like') as caught:
        restage(fixture)
    assert 'do-not-publish' not in str(caught.value)
    assert_no_new_bundle(fixture)


def test_frontend_cannot_supply_its_own_region_allowlist(fixture, region_assets, monkeypatch):
    monkeypatch.setattr(frontend, 'REGION_SOURCE_ROOT', fixture.root / 'absent-canonical-source')
    # A copied manifest is never consulted to approve the matching path.
    (fixture.client / 'assets/region-selection-12345678.json').write_bytes(region_assets[1][0].read_bytes())
    with pytest.raises((ValueError, OSError)):
        restage(fixture)
    assert_no_new_bundle(fixture)


def test_approved_region_asset_can_replace_hashed_url_but_never_overwrite_old_url(fixture, region_assets):
    previous = restage(fixture)
    fixture.bundle = Path(previous['bundle'])
    old = fixture.client / 'assets/boundary-10-12345678.json'
    old.rename(fixture.client / 'assets/boundary-10-87654321.json')
    result = restage(fixture)
    assert not (Path(result['bundle']) / 'client/assets/boundary-10-12345678.json').exists()
    assert (fixture.bundle / 'client/assets/boundary-10-12345678.json').is_file()


def test_even_audited_region_updates_cannot_overwrite_an_existing_immutable_url(fixture, region_assets):
    previous = restage(fixture)
    fixture.bundle = Path(previous['bundle'])
    source_root, indexes = region_assets
    source = source_root / 'region-selection-areas/boundary-10.json'
    body = source.read_bytes().replace(b'Fixture region', b'Changed region')
    source.write_bytes(body)
    manifest = json.loads(indexes[0].read_bytes())
    manifest['chunks'][0][1:] = [len(body), hashlib.sha256(body).hexdigest()]
    indexes[0].write_text(json.dumps(manifest), encoding='utf-8')
    (fixture.client / 'assets/boundary-10-12345678.json').write_bytes(body)
    with pytest.raises(ValueError, match='immutable frontend asset URL'):
        restage(fixture)


def test_region_total_byte_budget_is_independent_of_per_file_budget(fixture, region_assets, monkeypatch):
    monkeypatch.setattr(frontend, 'REGION_MAX_TOTAL_BYTES', 1)
    with pytest.raises(ValueError, match='byte budget'):
        restage(fixture)


@pytest.fixture
def poi_assets(fixture):
    source_root = frontend.POI_SOURCE_ROOT
    source_root.mkdir(parents=True)
    chunks = []
    for index in range(2):
        payload = {'schema': 1, 'records': [{'id': f'node/{index + 1}', 'name': 'Fixture station',
                    'category': 'transport', 'type': 'subway', 'longitude': 127.1, 'latitude': 37.1,
                    'positionMethod': 'original_node', 'scopeRegionCode': '11'}]}
        body = json.dumps(payload, separators=(',', ':')).encode()
        sha = hashlib.sha256(body).hexdigest()
        name = 'poi-' + sha + '.json'
        (source_root / name).write_bytes(body)
        (fixture.client / f'assets/poi-{sha}-12345678.json').write_bytes(body)
        chunks.append({'key': f'2540-740-{index}0', 'west': 127, 'south': 37, 'east': 128, 'north': 38,
                       'file': name, 'sha256': sha, 'bytes': len(body), 'count': 1})
    manifest = {'schema': 1, 'source': dict(frontend.POI_SOURCE),
                'scope': {**frontend.POI_SCOPE_METADATA, 'regionCodes': sorted(frontend.POI_SCOPE_CODES)},
                'chunks': chunks}
    path = source_root / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path


def rewrite_poi_payload(index, transform):
    manifest = json.loads(index.read_bytes())
    ref = manifest['chunks'][0]
    payload = json.loads((index.parent / ref['file']).read_bytes())
    transform(payload)
    body = json.dumps(payload, separators=(',', ':')).encode()
    sha = hashlib.sha256(body).hexdigest()
    ref.update(file='poi-' + sha + '.json', sha256=sha, bytes=len(body))
    (index.parent / ref['file']).write_bytes(body)
    index.write_text(json.dumps(manifest), encoding='utf-8')


def test_poi_exact_inventory_stages_without_changing_prior_data(fixture, poi_assets):
    result = restage(fixture)
    assets = json.loads((Path(result['bundle']) / 'asset-manifest.json').read_bytes())
    assert len([row for row in assets if frontend.POI_ASSET.fullmatch(row['target'])]) == 2
    assert result['catalog_hash'] == fixture.first['catalog_hash']
    assert_prior_unchanged(fixture)


@pytest.mark.parametrize('change', ['missing', 'all_missing', 'duplicate', 'hash', 'unlisted'])
def test_poi_frontend_rejects_missing_altered_or_duplicate_assets(fixture, poi_assets, change):
    targets = sorted(fixture.client.glob('assets/poi-*.json'))
    if change in ('missing', 'all_missing'):
        for target in targets if change == 'all_missing' else targets[:1]:
            target.unlink()
    elif change == 'duplicate':
        targets[0].with_name(targets[0].name.replace('12345678', 'abcdefgh')).write_bytes(targets[0].read_bytes())
    elif change == 'hash':
        targets[0].write_bytes(targets[0].read_bytes().replace(b'station', b'changed'))
    else:
        targets[0].rename(fixture.client / ('assets/poi-' + '0' * 64 + '-12345678.json'))
    with pytest.raises(ValueError, match='POI'):
        restage(fixture)
    assert_no_new_bundle(fixture)


@pytest.mark.parametrize('change', ['source', 'license', 'revision', 'traversal', 'size', 'count', 'scope', 'extra'])
def test_poi_rejects_unaudited_source_manifest(fixture, poi_assets, change):
    manifest = json.loads(poi_assets.read_bytes())
    if change == 'source':
        manifest['source']['sha256'] = '0' * 64
    elif change == 'license':
        manifest['source']['license'] = 'unknown'
    elif change == 'revision':
        manifest['scope']['boundaryDate'] = '2030-01-01'
    elif change == 'traversal':
        manifest['chunks'][0]['file'] = '../manifest.json'
    elif change == 'size':
        manifest['chunks'][0]['bytes'] += 1
    elif change == 'count':
        manifest['chunks'][0]['count'] += 1
    elif change == 'scope':
        manifest['scope']['regionCodes'] = ['11', '11']
    else:
        manifest['unchecked'] = True
    poi_assets.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='POI'):
        restage(fixture)


@pytest.mark.parametrize('change', ['latitude', 'nan', 'type', 'duplicate', 'position', 'schema', 'extra', 'credential', 'scope'])
def test_poi_payload_checks_do_not_trust_matching_content_hash_alone(fixture, poi_assets, change):
    def alter(payload):
        row = payload['records'][0]
        if change == 'latitude': row['latitude'] = 0
        elif change == 'nan': row['longitude'] = float('nan')
        elif change == 'type': row['type'] = 'unverified_plan'
        elif change == 'duplicate': row['id'] = 'node/2'
        elif change == 'position': row['positionMethod'] = 'name_guess'
        elif change == 'schema': payload['schema'] = True
        elif change == 'extra': row['etaMinutes'] = 3
        elif change == 'credential': row['name'] = 'serviceKey=do-not-publish-this-fixture-value'
        else: row['scopeRegionCode'] = '99999'
    rewrite_poi_payload(poi_assets, alter)
    with pytest.raises(ValueError, match='POI|Credential-like') as caught:
        frontend._poi_asset_inventory()
    assert 'do-not-publish' not in str(caught.value)


@pytest.mark.parametrize('budget', ['POI_MAX_FILE_BYTES', 'POI_MAX_TOTAL_BYTES', 'POI_MAX_FILES'])
def test_poi_all_publication_budgets_are_enforced(fixture, poi_assets, monkeypatch, budget):
    monkeypatch.setattr(frontend, budget, 1)
    with pytest.raises(ValueError, match='POI'):
        frontend._poi_asset_inventory()


def test_poi_requires_canonical_sources_and_preserves_old_immutable_urls(fixture, poi_assets, monkeypatch):
    previous = restage(fixture)
    fixture.bundle = Path(previous['bundle'])
    target = sorted(fixture.client.glob('assets/poi-*.json'))[0]
    target.rename(target.with_name(target.name.replace('12345678', 'abcdefgh')))
    result = restage(fixture)
    assert not (Path(result['bundle']) / 'client' / target.relative_to(fixture.client)).exists()
    assert (fixture.bundle / 'client' / target.relative_to(fixture.client)).is_file()
    monkeypatch.setattr(frontend, 'POI_SOURCE_ROOT', fixture.root / 'absent-source')
    with pytest.raises((OSError, ValueError)):
        restage(fixture)


def test_poi_immutable_url_cannot_replace_prior_bytes_even_if_current_source_is_approved(fixture, poi_assets):
    _, previous = frontend._prior_metadata(fixture.bundle)
    target = sorted(fixture.client.glob('assets/poi-*.json'))[0]
    name = target.relative_to(fixture.client).as_posix()
    previous[name] = {'sha256': '0' * 64, 'bytes': target.stat().st_size}
    with pytest.raises(ValueError, match='immutable frontend asset URL'):
        frontend._frontend_entries(fixture.client, previous)


def test_poi_frontend_cannot_add_its_own_approval_manifest(fixture, poi_assets):
    (fixture.client / 'assets/manifest-12345678.json').write_bytes(poi_assets.read_bytes())
    with pytest.raises(ValueError, match='Unknown frontend file'):
        restage(fixture)


@pytest.mark.parametrize('body', [b'{"schema":1,"schema":1}', b'{"records":[{"id":"node/1","id":"node/2"}]}', b'{"latitude":NaN}', b'{"longitude":Infinity}'])
def test_poi_json_rejects_ambiguous_properties_and_nonfinite_constants(body):
    with pytest.raises(ValueError, match='POI'):
        frontend._poi_json(body)


@pytest.mark.parametrize('stem', ['property-region-metrics-8879dff1b31ac5f0', 'property-region-metrics-b87eea7c1c03dc21', '11710'])
def test_only_exact_audited_property_metrics_can_enter_frontend(fixture, stem):
    data = Path(__file__).resolve().parents[1] / 'src/data'
    source = data / (stem + '.json') if stem.startswith('property-') else data / 'map-price-presets-b87eea7c1c03dc21' / (stem + '.json')
    target = fixture.client / ('assets/' + stem + '-12345678.json')
    target.write_bytes(source.read_bytes())
    result = restage(fixture)
    assert digest(Path(result['bundle']) / 'client' / target.relative_to(fixture.client)) == digest(source)
    assert_prior_unchanged(fixture)


@pytest.mark.parametrize('stem', ['property-region-metrics-8879dff1b31ac5f0', 'property-region-metrics-ffffffffffffffff', '11710', '99999'])
def test_rejects_forged_or_unregistered_property_metric_bytes(fixture, stem):
    (fixture.client / ('assets/' + stem + '-12345678.json')).write_bytes(b'{"count":999}')
    with pytest.raises(ValueError, match='Property metric asset differs from audited bytes'):
        restage(fixture)
    assert_no_new_bundle(fixture)


def test_audited_chunk_replacement_requires_both_old_and_new_identity(fixture, monkeypatch):
    target = fixture.client / 'assets/property-view-abcdefgh.js'
    target.write_bytes(b'export const replacement=1;')
    old_family = ('property-summary-client', 'js')
    new_family = ('property-view', 'js')
    monkeypatch.setattr(frontend, 'AUDITED_ADDITIONAL_CHUNKS', {new_family: {'sha256': digest(target), 'bytes': target.stat().st_size}})
    monkeypatch.setattr(frontend, 'AUDITED_REPLACED_CHUNKS', {old_family: {'sha256': 'a'*64, 'bytes': 42, 'replacement': new_family}})
    _, prior = frontend._prior_metadata(fixture.bundle)
    old_name = 'assets/property-summary-client-12345678.js'
    prior[old_name] = {'target':old_name, 'sha256':'a'*64, 'bytes':42}
    assert frontend._frontend_entries(fixture.client, prior)
    prior[old_name]['sha256'] = 'b'*64
    with pytest.raises(ValueError, match='chunk families are missing'):
        frontend._frontend_entries(fixture.client, prior)
    assert_prior_unchanged(fixture)


def test_only_the_exact_preserved_public_revision_archive_is_approved():
    filename = Path(__file__).resolve().parents[1] / 'src/data/property-revisions-ceeff63959643461.json'
    name = 'assets/property-revisions-ceeff63959643461-abcdefgh.json'
    sha = digest(filename);size = filename.stat().st_size
    assert frontend._approved_metric_asset(name, sha, size)
    assert not frontend._approved_metric_asset(name, 'a'*64, size)
    assert not frontend._approved_metric_asset(name, sha, size+1)
    assert not frontend._approved_metric_asset('assets/property-revisions-ffffffffffffffff-abcdefgh.json', sha, size)
