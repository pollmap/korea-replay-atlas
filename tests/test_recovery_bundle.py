"""Recovery changes only disposable fixtures; no cloud access or real data writes."""
import copy
import hashlib
import json
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


@pytest.fixture
def fixture(tmp_path):
    bundle = tmp_path / 'original-bundle'
    release = 'pub-0123456789abcdef'
    content = {
        'index.html': b'<!doctype html><title>Recovery fixture</title>',
        'data/catalog.json': recovery.json_bytes({'schema_version': 2, 'release_id': release}),
        'data/tiles/test.glb': b'fixture geometry ' * 80,
        '_headers': b'/data/*\n  Cache-Control: public, max-age=31536000, immutable\n',
    }
    manifest = []
    for name, body in content.items():
        file = bundle / 'client' / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(body)
        manifest.append({'target': name, 'bytes': len(body), 'sha256': recovery.digest(file)})
    worker = bundle / 'worker/index.js'
    worker.parent.mkdir()
    worker.write_text('export default {fetch(){return new Response("fixture")}};', encoding='utf-8')
    config = {
        'name': 'korea-replay', 'main': './worker/index.js', 'no_bundle': True,
        'vars': {'ENVIRONMENT': 'production', 'DATA_STORAGE': 'static',
                 'COLLECTORS_ENABLED': 'false', 'STATIC_RELEASE_ID': release},
        'assets': {'directory': './client', 'binding': 'ASSETS', 'not_found_handling': 'none',
                   'run_worker_first': ['/api/*']},
        'version_metadata': {'binding': 'CF_VERSION_METADATA'}, 'preview_urls': True,
    }
    put_json(bundle / 'asset-manifest.json', manifest)
    put_json(bundle / 'wrangler.json', config)
    receipt = {
        'schema_version': 1, 'mode': 'static', 'complete': True, 'bundle_id': '0123456789abcdef',
        'release_id': release, 'audit': {'passed': True, 'release_id': release},
        'count': len(manifest), 'bytes': sum(item['bytes'] for item in manifest),
        'bundle': str(bundle), 'config': str(bundle / 'wrangler.json'),
        'catalog_path': str(tmp_path / 'not-an-input/catalog.json'),
        'catalog_hash': recovery.digest(bundle / 'client/data/catalog.json'),
        'config_hash': recovery.digest(bundle / 'wrangler.json'),
        'manifest_hash': recovery.digest(bundle / 'asset-manifest.json'),
        'worker_files': [{'path': str(tmp_path / 'not-an-input/worker.js'),
                          'target': 'worker/index.js', 'sha256': recovery.digest(worker)}],
    }
    put_json(bundle / 'receipt.json', receipt)
    return SimpleNamespace(root=tmp_path, bundle=bundle, receipt=receipt, config=config, manifest=manifest)


def export(fixture, *, name='archive', part_bytes=900 * recovery.CHUNK):
    directory = fixture.root / name
    result = recovery.export_bundle(fixture.bundle, directory, part_bytes=part_bytes, reserve_bytes=0)
    return directory, result


def archive_receipt(directory, manifest):
    part = next(part for part in manifest['parts'] if 'receipt.json' in part['files'])
    with zipfile.ZipFile(directory / part['name']) as package:
        return package.read('receipt.json')


def rewrite_part(directory, manifest, transform):
    part = manifest['parts'][0]
    archive = directory / part['name']
    with zipfile.ZipFile(archive) as package:
        entries = [(item, package.read(item)) for item in package.infolist()]
    with zipfile.ZipFile(archive, 'w') as package:
        for info, body in transform(entries):
            package.writestr(info, body)
    part.update(bytes=archive.stat().st_size, sha256=recovery.digest(archive))
    put_json(directory / 'recovery-manifest.json', manifest)


def enable_broker(fixture):
    fixture.config.update(compatibility_date='2026-09-16', compatibility_flags=['nodejs_compat'],
                          observability={'enabled': True}, workers_dev=True,
                          services=[{'binding': 'LIVE_TRANSIT_BROKER', 'service': 'korea-replay-live-broker'}])
    fixture.config['vars']['LIVE_TRANSIT_MODE'] = 'broker'
    fixture.receipt['schema_version'] = 2
    fixture.receipt['deployment_contract'] = {'schema_version': 1, 'live_transit_mode': 'broker',
                                            'broker': copy.deepcopy(fixture.config['services'][0])}
    put_json(fixture.bundle / 'wrangler.json', fixture.config)
    fixture.receipt['config_hash'] = recovery.digest(fixture.bundle / 'wrangler.json')
    put_json(fixture.bundle / 'receipt.json', fixture.receipt)


@pytest.mark.parametrize('broker', [False, True])
def test_multipart_roundtrip_relocates_receipt_without_source_paths_or_overwrites(fixture, broker):
    if broker:
        enable_broker(fixture)
    source = {p.relative_to(fixture.bundle).as_posix(): p.read_bytes()
              for p in fixture.bundle.rglob('*') if p.is_file()}
    directory, manifest = export(fixture, part_bytes=500)
    assert len(manifest['parts']) > 1
    portable_bytes = archive_receipt(directory, manifest)
    portable = json.loads(portable_bytes)
    assert portable['bundle'] == '.'
    assert portable['worker_files'][0]['path'] == 'worker/index.js'
    assert str(fixture.root) not in portable_bytes.decode()
    assert manifest['source_receipt_sha256'] == recovery.digest(fixture.bundle / 'receipt.json')

    destination = fixture.root / 'different' / 'restored-bundle'
    result = recovery.restore_bundle(directory / 'recovery-manifest.json', destination, reserve_bytes=0)
    assert result['passed'] and result['files'] == len(manifest['files'])
    restored, files = recovery.inspect_bundle(destination)
    assert restored['bundle'] == str(destination)
    assert restored['config'] == str(destination / 'wrangler.json')
    assert restored['catalog_path'] == str(destination / 'client/data/catalog.json')
    assert restored['worker_files'][0]['path'] == str(destination / 'worker/index.js')
    assert (destination / 'receipt.original.json').read_bytes() == portable_bytes
    assert restored['catalog_hash'] == fixture.receipt['catalog_hash']
    assert restored['config_hash'] == fixture.receipt['config_hash']
    if broker:
        assert portable['deployment_contract'] == restored['deployment_contract'] == fixture.receipt['deployment_contract']
        assert recovery.read_json(destination / 'wrangler.json')['vars']['LIVE_TRANSIT_MODE'] == 'broker'
    for entry in files:
        if entry['path'] != 'receipt.json':
            assert (destination / entry['path']).read_bytes() == source[entry['path']]
    assert source == {p.relative_to(fixture.bundle).as_posix(): p.read_bytes()
                      for p in fixture.bundle.rglob('*') if p.is_file()}

    # The production JS verifier must accept the new location independently.
    node = shutil.which('node')
    assert node, 'The project Node runtime is required for the relocation contract test'
    script = Path(__file__).resolve().parents[1] / 'scripts/deploy-preflight.mjs'
    check = subprocess.run([node, str(script), str(destination / 'receipt.json')],
                           capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert check.returncode == 0, check.stderr


@pytest.mark.parametrize('change', ['missing-service', 'wrong-service', 'wrong-binding', 'service-environment',
                                   'extra-service', 'override-env', 'missing-mode', 'direct-mode', 'extra-variable',
                                   'map-do', 'map-migration', 'missing-contract', 'null-contract', 'contract-version',
                                   'contract-target', 'stripped-broker-fields', 'legacy-schema'])
def test_broker_archive_rejects_changed_dependency_even_with_rehashed_config(fixture, change):
    enable_broker(fixture)
    config, receipt = fixture.config, fixture.receipt
    if change == 'missing-service':
        del config['services']
    elif change == 'wrong-service':
        config['services'][0]['service'] = 'another-worker'
    elif change == 'wrong-binding':
        config['services'][0]['binding'] = 'OTHER_BROKER'
    elif change == 'service-environment':
        config['services'][0]['environment'] = 'preview'
    elif change == 'extra-service':
        config['services'].append({'binding': 'OTHER', 'service': 'other-worker'})
    elif change == 'override-env':
        config['env'] = {'production': {'vars': {'LIVE_TRANSIT_MODE': 'direct'}}}
    elif change == 'missing-mode':
        del config['vars']['LIVE_TRANSIT_MODE']
    elif change == 'direct-mode':
        config['vars']['LIVE_TRANSIT_MODE'] = 'direct'
    elif change == 'extra-variable':
        config['vars']['LIVE_TRANSIT_BROKER'] = 'https://other.invalid'
    elif change == 'map-do':
        config['durable_objects'] = {'bindings': [{'name': 'STATE', 'class_name': 'TransitCoordinator'}]}
    elif change == 'map-migration':
        config['migrations'] = [{'tag': 'v1', 'new_sqlite_classes': ['TransitCoordinator']}]
    elif change == 'missing-contract':
        del receipt['deployment_contract']
    elif change == 'null-contract':
        receipt['deployment_contract'] = None
    elif change == 'contract-version':
        receipt['deployment_contract']['schema_version'] = 2
    elif change == 'stripped-broker-fields':
        del receipt['deployment_contract']
        del config['vars']['LIVE_TRANSIT_MODE']
        del config['services']
    elif change == 'legacy-schema':
        receipt['schema_version'] = 1
    else:
        receipt['deployment_contract']['broker']['service'] = 'another-worker'
    put_json(fixture.bundle / 'wrangler.json', config)
    receipt['config_hash'] = recovery.digest(fixture.bundle / 'wrangler.json')
    put_json(fixture.bundle / 'receipt.json', receipt)
    with pytest.raises(ValueError):
        export(fixture)
    assert not (fixture.root / 'archive').exists()


def test_existing_output_or_restore_directory_remains_unchanged(fixture):
    directory, _ = export(fixture)
    original = (directory / 'recovery-manifest.json').read_bytes()
    with pytest.raises(ValueError, match='new directory'):
        recovery.export_bundle(fixture.bundle, directory, reserve_bytes=0)
    with pytest.raises(ValueError, match='outside the bundle'):
        recovery.export_bundle(fixture.bundle, fixture.bundle / 'archive', reserve_bytes=0)
    destination = fixture.root / 'occupied'
    destination.mkdir()
    sentinel = destination / 'user-file.txt'
    sentinel.write_text('preserve', encoding='utf-8')
    with pytest.raises(ValueError, match='new directory'):
        recovery.restore_bundle(directory / 'recovery-manifest.json', destination, reserve_bytes=0)
    assert sentinel.read_text() == 'preserve'
    assert (directory / 'recovery-manifest.json').read_bytes() == original


@pytest.mark.parametrize('change', ['data', 'worker', 'config', 'missing', 'added'])
def test_export_rejects_mutated_or_missing_source_files(fixture, change):
    if change == 'data':
        (fixture.bundle / 'client/data/tiles/test.glb').write_bytes(b'changed')
    elif change == 'worker':
        (fixture.bundle / 'worker/index.js').write_bytes(b'changed')
    elif change == 'config':
        (fixture.bundle / 'wrangler.json').write_bytes(b'{}')
    elif change == 'missing':
        # Simulate a missing declared file without deleting a file.
        fixture.manifest[0]['target'] = 'missing.html'
        put_json(fixture.bundle / 'asset-manifest.json', fixture.manifest)
        fixture.receipt['manifest_hash'] = recovery.digest(fixture.bundle / 'asset-manifest.json')
        put_json(fixture.bundle / 'receipt.json', fixture.receipt)
    else:
        (fixture.bundle / 'worker/unlisted.js').write_bytes(b'extra')
    with pytest.raises(ValueError):
        export(fixture)
    assert not (fixture.root / 'archive').exists()


@pytest.mark.parametrize('change', ['release', 'audit', 'free-config', 'entrypoint'])
def test_export_checks_release_and_free_configuration(fixture, change):
    if change == 'release':
        fixture.config['vars']['STATIC_RELEASE_ID'] = 'pub-fedcba9876543210'
    elif change == 'audit':
        fixture.receipt['audit']['release_id'] = 'pub-fedcba9876543210'
    elif change == 'free-config':
        fixture.config['r2_buckets'] = [{'binding': 'DATA', 'bucket_name': 'fixture-only'}]
    else:
        fixture.config['main'] = '../different.js'
    put_json(fixture.bundle / 'wrangler.json', fixture.config)
    fixture.receipt['config_hash'] = recovery.digest(fixture.bundle / 'wrangler.json')
    put_json(fixture.bundle / 'receipt.json', fixture.receipt)
    with pytest.raises(ValueError):
        export(fixture)


@pytest.mark.parametrize('unsafe', ['../escape', '/absolute', 'client/../../escape',
                                   'client\\escape', 'C:/file', 'client/file:stream',
                                   'client//file', 'client/file.', 'client/NUL.json',
                                   'client/COM¹', 'client/file?', 'client/a\x00b'])
def test_unsafe_archive_paths_are_rejected(unsafe):
    with pytest.raises(ValueError, match='Unsafe'):
        recovery.safe_relative(unsafe)


def test_expanded_size_limits_counts_case_collisions_and_prefix_collisions(fixture):
    _, files = recovery.inspect_bundle(fixture.bundle)
    for item in [
        {'path': 'client/INDEX.HTML', 'bytes': 1, 'sha256': 'a' * 64},
        {'path': 'client/index.html/subfile', 'bytes': 1, 'sha256': 'a' * 64},
        {'path': 'client/oversize', 'bytes': recovery.MAX_FILE, 'sha256': 'a' * 64},
    ]:
        with pytest.raises(ValueError):
            recovery.check_files(files + [item])
    with pytest.raises(ValueError):
        recovery.check_files(files + [{'path': f'client/f{i}', 'bytes': 0, 'sha256': 'a' * 64}
                                     for i in range(recovery.MAX_ASSETS)])


def test_archive_hash_mismatch_fails_before_restore_directory_exists(fixture):
    directory, manifest = export(fixture)
    archive = directory / manifest['parts'][0]['name']
    with archive.open('ab') as output:
        output.write(b'changed')
    destination = fixture.root / 'restored'
    with pytest.raises(ValueError, match='hash mismatch'):
        recovery.restore_bundle(directory / 'recovery-manifest.json', destination, reserve_bytes=0)
    assert not destination.exists()


@pytest.mark.parametrize('change', ['extra', 'traversal', 'symlink', 'reparse', 'expanded-size', 'duplicate'])
def test_invalid_zip_members_fail_before_creating_destination(fixture, change):
    directory, manifest = export(fixture)
    def transform(entries):
        entries = copy.deepcopy(entries)
        if change == 'extra':
            entries.append((zipfile.ZipInfo('client/extra'), b'extra'))
        elif change == 'traversal':
            entries[0][0].filename = '../escape'
        elif change == 'symlink':
            entries[0][0].external_attr = (stat.S_IFLNK | 0o777) << 16
        elif change == 'reparse':
            entries[0][0].external_attr |= 0x400
        elif change == 'expanded-size':
            entries[0] = (entries[0][0], entries[0][1] + b'changed size')
        else:
            entries.append(entries[0])
        return entries
    if change == 'duplicate':
        with pytest.warns(UserWarning, match='Duplicate name'):
            rewrite_part(directory, manifest, transform)
    else:
        rewrite_part(directory, manifest, transform)
    destination = fixture.root / 'restored'
    with pytest.raises(ValueError):
        recovery.restore_bundle(directory / 'recovery-manifest.json', destination, reserve_bytes=0)
    assert not destination.exists()
    assert not (fixture.root / 'escape').exists()


def test_inner_file_hash_mismatch_retains_failure_without_complete_evidence(fixture):
    directory, manifest = export(fixture)
    def change(entries):
        return [(info, b'X' * len(body) if info.filename == 'client/index.html' else body)
                for info, body in entries]
    rewrite_part(directory, manifest, change)
    destination = fixture.root / 'restored'
    with pytest.raises(ValueError, match='Restored file hash mismatch'):
        recovery.restore_bundle(directory / 'recovery-manifest.json', destination, reserve_bytes=0)
    assert destination.is_dir()
    assert not (destination / 'recovery-verification.json').exists()


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'release', 'incomplete'])
def test_manifest_part_and_release_integrity(fixture, change):
    directory, manifest = export(fixture, part_bytes=500)
    if change == 'missing':
        manifest['parts'] = manifest['parts'][1:]
    elif change == 'duplicate':
        manifest['parts'].append(manifest['parts'][0])
    elif change == 'release':
        manifest['release_id'] = 'pub-fedcba9876543210'
    else:
        manifest['complete'] = False
    put_json(directory / 'recovery-manifest.json', manifest)
    destination = fixture.root / 'restored'
    with pytest.raises(ValueError):
        recovery.restore_bundle(directory / 'recovery-manifest.json', destination, reserve_bytes=0)
    assert not (destination / 'recovery-verification.json').exists()


def test_disk_reserve_blocks_export_and_restore_before_any_output(fixture, monkeypatch):
    directory, _ = export(fixture)
    monkeypatch.setattr(recovery.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match='free space'):
        export(fixture, name='no-space')
    with pytest.raises(ValueError, match='free space'):
        recovery.restore_bundle(directory / 'recovery-manifest.json', fixture.root / 'no-space-restore')
    assert not (fixture.root / 'no-space').exists()
    assert not (fixture.root / 'no-space-restore').exists()


def test_streamed_zipinfo_uses_requested_fast_compression(fixture, monkeypatch):
    seen = []
    original = zipfile.ZipFile.open
    def capture(package, name, mode='r', *args, **kwargs):
        if mode == 'w':
            seen.append(name.compress_level)
        return original(package, name, mode, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'open', capture)
    export(fixture)
    assert seen and set(seen) == {1}


def test_junction_or_symlink_inputs_are_rejected_without_reading_target(fixture):
    linked = fixture.root / 'linked-bundle'
    if os.name == 'nt':
        node = shutil.which('node')
        assert node
        result = subprocess.run([node, '--input-type=module', '-e',
                                 "import {symlink} from 'node:fs/promises'; await symlink(process.argv[1],process.argv[2],'junction');",
                                 str(fixture.bundle), str(linked)], capture_output=True, timeout=15)
        assert result.returncode == 0, result.stderr
    else:
        linked.symlink_to(fixture.bundle, target_is_directory=True)
    with pytest.raises(ValueError, match='links or junctions'):
        recovery.inspect_bundle(linked)
    with pytest.raises(ValueError, match='links or junctions'):
        recovery.export_bundle(fixture.bundle, linked / 'new-output', reserve_bytes=0)
    assert not (fixture.bundle / 'new-output').exists()


def test_metadata_rejects_duplicate_keys_and_non_finite_values(tmp_path):
    metadata = tmp_path / 'bad.json'
    metadata.write_text('{"complete":false,"complete":true}', encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate'):
        recovery.read_json(metadata)
    metadata.write_text('{"bytes":NaN}', encoding='utf-8')
    with pytest.raises(ValueError, match='constant'):
        recovery.read_json(metadata)
