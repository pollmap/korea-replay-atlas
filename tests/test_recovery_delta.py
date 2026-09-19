"""Delta recovery uses isolated, tiny fixtures; no cloud or nationwide data."""
import copy
import os
from pathlib import Path
import shutil
import stat
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from pipeline import recovery_bundle as recovery


def put_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(recovery.json_bytes(value))


def make_bundle(folder, identity, *, latest):
    release = 'pub-' + identity
    content = {
        'index.html': b'new index' if latest else b'old index',
        'data/catalog.json': recovery.json_bytes({'schema_version': 2, 'release_id': release}),
        'data/tiles/reuse.glb': b'unchanged geometry' * 30,
        '_headers': b'/data/*\n  Cache-Control: public, max-age=31536000, immutable\n',
        ('data/live.json' if latest else 'legacy.txt'): b'new observation' if latest else b'old only',
    }
    assets = []
    for name, body in content.items():
        file = folder / 'client' / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(body)
        assets.append({'target': name, 'bytes': len(body), 'sha256': recovery.digest(file)})
    worker = folder / 'worker/index.js'
    worker.parent.mkdir()
    worker.write_text('export default {fetch(){return new Response("' + identity + '")}};', encoding='utf-8')
    config = {
        'name': 'korea-replay', 'main': './worker/index.js', 'no_bundle': True,
        'vars': {'ENVIRONMENT': 'production', 'DATA_STORAGE': 'static',
                 'COLLECTORS_ENABLED': 'false', 'STATIC_RELEASE_ID': release},
        'assets': {'directory': './client', 'binding': 'ASSETS', 'not_found_handling': 'none',
                   'run_worker_first': ['/api/*']},
        'version_metadata': {'binding': 'CF_VERSION_METADATA'}, 'preview_urls': True,
    }
    put_json(folder / 'asset-manifest.json', assets)
    put_json(folder / 'wrangler.json', config)
    receipt = {
        'schema_version': 1, 'mode': 'static', 'complete': True, 'bundle_id': identity,
        'release_id': release, 'audit': {'passed': True, 'release_id': release},
        'count': len(assets), 'bytes': sum(item['bytes'] for item in assets),
        'bundle': str(folder), 'config': str(folder / 'wrangler.json'),
        'catalog_hash': recovery.digest(folder / 'client/data/catalog.json'),
        'config_hash': recovery.digest(folder / 'wrangler.json'),
        'manifest_hash': recovery.digest(folder / 'asset-manifest.json'),
        'worker_files': [{'target': 'worker/index.js', 'path': str(worker), 'sha256': recovery.digest(worker)}],
    }
    put_json(folder / 'receipt.json', receipt)


@pytest.fixture
def fixture(tmp_path):
    old, new = tmp_path / 'old', tmp_path / 'new'
    make_bundle(old, '0123456789abcdef', latest=False)
    make_bundle(new, 'fedcba9876543210', latest=True)
    base_dir = tmp_path / 'base'
    recovery.export_bundle(old, base_dir, reserve_bytes=0, part_bytes=100)
    base_path = base_dir / 'recovery-manifest.json'
    return SimpleNamespace(root=tmp_path, old=old, new=new, base=base_path,
                           base_hash=recovery.digest(base_path))


def export_delta(fixture, *, name='delta', part_bytes=900 * recovery.CHUNK):
    directory = fixture.root / name
    manifest = recovery.export_delta_bundle(fixture.base, fixture.new, directory,
                                            base_manifest_sha256=fixture.base_hash,
                                            reserve_bytes=0, part_bytes=part_bytes)
    return directory / 'recovery-delta-manifest.json', manifest


def restore_delta(fixture, manifest_path, *, name='restored', expected_hash=None):
    return recovery.restore_delta_bundle(manifest_path, fixture.base, fixture.root / name,
                                         manifest_sha256=expected_hash or recovery.digest(manifest_path),
                                         reserve_bytes=0)


def repin_base(fixture, delta_path, delta, base):
    put_json(fixture.base, base)
    delta['base'] = recovery.base_dependency(base, recovery.digest(fixture.base))
    put_json(delta_path, delta)


def rewrite_archive(directory, manifest, transform, *, selected=None):
    part = next((part for part in manifest['parts'] if selected in part['files']), manifest['parts'][0])
    file = directory / part['name']
    with zipfile.ZipFile(file) as archive:
        entries = [(item, archive.read(item)) for item in archive.infolist()]
    with zipfile.ZipFile(file, 'w') as archive:
        for item, body in transform(entries):
            archive.writestr(item, body)
    part.update(bytes=file.stat().st_size, sha256=recovery.digest(file))


@pytest.mark.parametrize('broker', [False, True])
def test_delta_roundtrip_extracts_only_final_allowlist_and_passes_deployment_preflight(fixture, monkeypatch, broker):
    if broker:
        config = recovery.read_json(fixture.new / 'wrangler.json')
        config.update(compatibility_date='2026-09-16', compatibility_flags=['nodejs_compat'],
                      observability={'enabled': True}, workers_dev=True,
                      services=[{'binding': 'LIVE_TRANSIT_BROKER', 'service': 'korea-replay-live-broker'}])
        config['vars']['LIVE_TRANSIT_MODE'] = 'broker'
        put_json(fixture.new / 'wrangler.json', config)
        receipt = recovery.read_json(fixture.new / 'receipt.json')
        receipt['schema_version'] = 2
        receipt['config_hash'] = recovery.digest(fixture.new / 'wrangler.json')
        receipt['deployment_contract'] = {'schema_version': 1, 'live_transit_mode': 'broker',
                                          'broker': copy.deepcopy(config['services'][0])}
        put_json(fixture.new / 'receipt.json', receipt)
    original = {p.relative_to(fixture.old).as_posix(): p.read_bytes()
                for p in fixture.old.rglob('*') if p.is_file()}
    path, manifest = export_delta(fixture, part_bytes=200)
    assert len(manifest['parts']) > 1
    payload = {name for part in manifest['parts'] for name in part['files']}
    assert 'client/data/tiles/reuse.glb' not in payload
    assert 'client/legacy.txt' not in payload
    assert 'client/data/live.json' in payload
    assert manifest['base']['manifest_sha256'] == fixture.base_hash
    assert manifest['export_verification']['reused_contents_rehashed'] is False
    regular = recovery.regular_file

    def require_archives_only(path):
        assert not path.is_relative_to(fixture.old) and not path.is_relative_to(fixture.new)
        return regular(path)

    monkeypatch.setattr(recovery, 'regular_file', require_archives_only)
    result = restore_delta(fixture, path)
    assert result['passed'] and result['reused_files'] == 2
    assert result['base_archive_sha256_verified'] and result['delta_archive_sha256_verified']
    destination = fixture.root / 'restored'
    if broker:
        assert recovery.read_json(destination / 'receipt.json')['deployment_contract'] == receipt['deployment_contract']
        assert recovery.read_json(destination / 'wrangler.json')['services'] == config['services']
    assert not (destination / 'client/legacy.txt').exists()
    assert (destination / 'client/data/live.json').read_bytes() == b'new observation'
    assert original == {p.relative_to(fixture.old).as_posix(): p.read_bytes()
                        for p in fixture.old.rglob('*') if p.is_file()}
    expected_paths = {entry['path'] for entry in manifest['files']}
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
    assert actual == expected_paths | {'receipt.original.json', 'recovery-verification.json'}
    node = shutil.which('node')
    assert node
    script = Path(__file__).resolve().parents[1] / 'scripts/deploy-preflight.mjs'
    check = subprocess.run([node, str(script), str(destination / 'receipt.json')],
                           capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert check.returncode == 0, check.stderr


def test_delta_export_does_not_rehash_unchanged_payload_or_base_archives(fixture, monkeypatch):
    original = recovery.digest

    def bounded(path):
        assert path.name != 'reuse.glb'
        assert not (path.suffix == '.zip' and path.parent == fixture.base.parent)
        return original(path)

    monkeypatch.setattr(recovery, 'digest', bounded)
    path, manifest = export_delta(fixture)
    assert path.exists() and manifest['complete']


@pytest.mark.parametrize('which', ['export-base', 'restore-delta', 'restore-base'])
def test_pinned_manifest_hash_is_mandatory_and_checked_before_output(fixture, which):
    if which == 'export-base':
        with pytest.raises(ValueError, match='manifest hash'):
            recovery.export_delta_bundle(fixture.base, fixture.new, fixture.root / 'delta',
                                         base_manifest_sha256='0' * 64, reserve_bytes=0)
        assert not (fixture.root / 'delta').exists()
        return
    path, manifest = export_delta(fixture)
    if which == 'restore-base':
        manifest['base']['manifest_sha256'] = '0' * 64
        put_json(path, manifest)
    with pytest.raises(ValueError, match='manifest hash'):
        restore_delta(fixture, path, expected_hash='0' * 64 if which == 'restore-delta' else None)
    assert not (fixture.root / 'restored').exists()


@pytest.mark.parametrize('which', ['base', 'delta'])
@pytest.mark.parametrize('fault', ['archive-sha', 'missing-archive'])
def test_all_archive_dependencies_are_checked_before_output(fixture, which, fault):
    path, delta = export_delta(fixture)
    manifest = recovery.read_json(fixture.base) if which == 'base' else delta
    directory = fixture.base.parent if which == 'base' else path.parent
    # In base mode choose a part used only by the obsolete file: it is still pinned.
    part = next((p for p in manifest['parts'] if p['files'] == ['client/legacy.txt']), manifest['parts'][0])
    if fault == 'archive-sha':
        archive = directory / part['name']
        body = archive.read_bytes()
        archive.write_bytes(body[:-1] + bytes([body[-1] ^ 1]))
    else:
        part['name'] = 'missing.zip'
        if which == 'base':
            repin_base(fixture, path, delta, manifest)
        else:
            put_json(path, delta)
    with pytest.raises((ValueError, FileNotFoundError)):
        restore_delta(fixture, path)
    assert not (fixture.root / 'restored').exists()


@pytest.mark.parametrize('which', ['base', 'delta'])
@pytest.mark.parametrize('fault', ['traversal', 'symlink', 'duplicate', 'unexpected', 'missing', 'expanded'])
def test_invalid_base_or_delta_zip_members_fail_before_output(fixture, which, fault):
    path, delta = export_delta(fixture)
    manifest = recovery.read_json(fixture.base) if which == 'base' else delta
    directory = fixture.base.parent if which == 'base' else path.parent

    def transform(entries):
        if fault == 'traversal':
            entries[0][0].filename = '../escape'
        elif fault == 'symlink':
            entries[0][0].external_attr = (stat.S_IFLNK | 0o777) << 16
        elif fault == 'duplicate':
            entries.append(entries[0])
        elif fault == 'unexpected':
            entries.append((zipfile.ZipInfo('client/unlisted.txt'), b'unexpected'))
        elif fault == 'missing':
            entries.pop()
        else:
            entries[0] = (entries[0][0], entries[0][1] + b'!')
        return entries

    if fault == 'duplicate':
        with pytest.warns(UserWarning, match='Duplicate name'):
            rewrite_archive(directory, manifest, transform)
    else:
        rewrite_archive(directory, manifest, transform)
    if which == 'base':
        repin_base(fixture, path, delta, manifest)
    else:
        put_json(path, delta)
    with pytest.raises(ValueError):
        restore_delta(fixture, path)
    assert not (fixture.root / 'restored').exists()
    assert not (fixture.root / 'escape').exists()


@pytest.mark.parametrize('which', ['base', 'delta'])
def test_inner_file_sha_failure_retains_new_tree_without_success_marker(fixture, which):
    path, delta = export_delta(fixture)
    manifest = recovery.read_json(fixture.base) if which == 'base' else delta
    directory = fixture.base.parent if which == 'base' else path.parent
    name = 'client/data/tiles/reuse.glb' if which == 'base' else 'client/index.html'
    rewrite_archive(directory, manifest,
                    lambda entries: [(item, b'X' * len(body) if item.filename == name else body)
                                     for item, body in entries], selected=name)
    if which == 'base':
        repin_base(fixture, path, delta, manifest)
    else:
        put_json(path, delta)
    with pytest.raises(ValueError, match='Restored file hash mismatch'):
        restore_delta(fixture, path)
    assert (fixture.root / 'restored').exists()
    assert not (fixture.root / 'restored/recovery-verification.json').exists()


@pytest.mark.parametrize('fault', ['duplicate-file', 'traversal-file', 'duplicate-part', 'missing-part',
                                 'base-parts', 'base-identity', 'incomplete', 'reused-in-delta', 'wrong-release'])
def test_manifest_relationships_are_strict(fixture, fault):
    path, manifest = export_delta(fixture)
    if fault == 'duplicate-file':
        manifest['files'].append(copy.deepcopy(manifest['files'][0]))
    elif fault == 'traversal-file':
        manifest['files'][0]['path'] = 'client/../../escape'
    elif fault == 'duplicate-part':
        manifest['parts'].append(copy.deepcopy(manifest['parts'][0]))
    elif fault == 'missing-part':
        manifest['parts'] = []
    elif fault == 'base-parts':
        manifest['base']['parts'][0]['sha256'] = '0' * 64
    elif fault == 'base-identity':
        manifest['base']['release_id'] = manifest['release_id']
    elif fault == 'incomplete':
        manifest['complete'] = False
    elif fault == 'reused-in-delta':
        manifest['parts'][0]['files'].append('client/data/tiles/reuse.glb')
    else:
        manifest['release_id'] = 'pub-1111111111111111'
    put_json(path, manifest)
    with pytest.raises(ValueError):
        restore_delta(fixture, path)
    assert not (fixture.root / 'restored/recovery-verification.json').exists()


def test_existing_outputs_and_inside_base_outputs_are_preserved(fixture):
    path, _ = export_delta(fixture)
    snapshot = path.read_bytes()
    with pytest.raises(ValueError, match='new directory'):
        export_delta(fixture)
    for output in (fixture.base.parent / 'nested', fixture.new / 'nested'):
        with pytest.raises(ValueError, match='outside'):
            recovery.export_delta_bundle(fixture.base, fixture.new, output,
                                         base_manifest_sha256=fixture.base_hash, reserve_bytes=0)
        assert not output.exists()
    destination = fixture.root / 'restored'
    destination.mkdir()
    sentinel = destination / 'keep.txt'
    sentinel.write_bytes(b'keep')
    with pytest.raises(ValueError, match='new directory'):
        restore_delta(fixture, path)
    assert sentinel.read_bytes() == b'keep'
    assert path.read_bytes() == snapshot


def test_export_input_change_and_unexpected_files_fail_before_output(fixture):
    (fixture.new / 'client/index.html').write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='delta source'):
        export_delta(fixture)
    assert not (fixture.root / 'delta').exists()
    (fixture.new / 'worker/extra.js').write_bytes(b'extra')
    with pytest.raises(ValueError, match='Added or missing'):
        export_delta(fixture)
    assert not (fixture.root / 'delta').exists()


def test_source_change_during_delta_packing_never_publishes_complete_manifest(fixture, monkeypatch):
    original = zipfile.ZipFile.open
    changed = False

    def mutate_after_preflight(archive, name, mode='r', *args, **kwargs):
        nonlocal changed
        if mode == 'w' and not changed:
            changed = True
            (fixture.new / 'client/index.html').write_bytes(b'corrupted')
        return original(archive, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'open', mutate_after_preflight)
    with pytest.raises(ValueError, match='Source changed'):
        export_delta(fixture)
    assert changed and (fixture.root / 'delta').exists()
    assert not (fixture.root / 'delta/recovery-delta-manifest.json').exists()


def test_receipt_byte_total_must_match_exact_asset_manifest(fixture):
    path = fixture.new / 'receipt.json'
    receipt = recovery.read_json(path)
    receipt['bytes'] += 1
    put_json(path, receipt)
    with pytest.raises(ValueError, match='byte total'):
        export_delta(fixture)
    assert not (fixture.root / 'delta').exists()


def test_disk_reserve_and_write_failure_cannot_create_success_evidence(fixture, monkeypatch):
    path, _ = export_delta(fixture)
    with monkeypatch.context() as patch:
        patch.setattr(recovery.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
        with pytest.raises(ValueError, match='free space'):
            recovery.export_delta_bundle(fixture.base, fixture.new, fixture.root / 'no-space',
                                         base_manifest_sha256=fixture.base_hash)
        with pytest.raises(ValueError, match='free space'):
            restore_delta(fixture, path)
    assert not (fixture.root / 'no-space').exists()
    assert not (fixture.root / 'restored').exists()
    original = Path.open

    def disk_error(file, mode='r', *args, **kwargs):
        if mode == 'xb' and file.name == 'index.html':
            raise OSError('fixture disk write failed')
        return original(file, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', disk_error)
    with pytest.raises(OSError, match='fixture disk'):
        restore_delta(fixture, path)
    assert not (fixture.root / 'restored/recovery-verification.json').exists()


def test_linked_archive_or_destination_parent_is_rejected(fixture):
    path, _ = export_delta(fixture)
    linked = fixture.root / 'linked-base'
    if os.name == 'nt':
        node = shutil.which('node')
        assert node
        result = subprocess.run([node, '--input-type=module', '-e',
                                 "import {symlink} from 'node:fs/promises'; await symlink(process.argv[1],process.argv[2],'junction');",
                                 str(fixture.base.parent), str(linked)], capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr
    else:
        linked.symlink_to(fixture.base.parent, target_is_directory=True)
    with pytest.raises(ValueError, match='links or junctions'):
        recovery.restore_delta_bundle(path, linked / fixture.base.name, fixture.root / 'restored',
                                      manifest_sha256=recovery.digest(path), reserve_bytes=0)
    with pytest.raises(ValueError, match='links or junctions'):
        recovery.restore_delta_bundle(path, fixture.base, linked / 'restored',
                                      manifest_sha256=recovery.digest(path), reserve_bytes=0)
    assert not (fixture.base.parent / 'restored').exists()
