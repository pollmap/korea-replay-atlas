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
def fixture(tmp_path):
    root = tmp_path / 'workspace'
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


def restage(fixture, **kwargs):
    return frontend.restage(root=fixture.root, reuse_bundle=fixture.bundle,
                            client_dir=fixture.client, worker_dir=fixture.worker,
                            output=fixture.output, **kwargs)


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
