from copy import deepcopy
import json
import shutil
from types import SimpleNamespace

import pytest

from pipeline.real_estate import RealEstateError, canonical_bytes, sha256
from pipeline.real_estate_archive import audit_checkpoint, backup
from pipeline.real_estate_local_archive import LocalArchive
from pipeline.real_estate_local_archive_set import (
    LocalArchiveSet, MAX_GROUP, backup_set, load_set, plan_set, restore_set,
)
from test_real_estate_archive import source


def add_run(root, body):
    directory = root / 'runs'
    directory.mkdir(exist_ok=True)
    (directory / (sha256(body) + '.json')).write_bytes(body)


def test_set_roundtrip_splits_source_groups_without_changing_legacy_head(tmp_path):
    root = source(tmp_path)
    legacy = LocalArchive(tmp_path / 'archive')
    backup(root, legacy)
    old_head = legacy.head()
    for index in range(3):
        add_run(root, canonical_bytes({'kind': 'fixture', 'index': index, 'padding': 'x' * 900}))
    store = LocalArchiveSet(legacy.root)
    # A small configured budget exercises the same split used at 8 GiB.
    limit = 2 * (root / 'checkpoint.sqlite').stat().st_size + 4096
    # Make the metadata group larger than the checkpoint-sized test budget.
    for index in range(3):
        add_run(root, canonical_bytes({'kind': 'large-fixture', 'index': index, 'padding': 'x' * (limit - 1024)}))
    result = backup_set(root, store, group_limit=limit)
    assert legacy.head() == old_head
    manifest = load_set(store, store.head())
    assert result['audit'] == audit_checkpoint(root)
    assert manifest['source_bytes'] > limit
    assert all(group['source_bytes'] <= limit for group in manifest['groups'])
    assert sum(group['group'] == 'metadata' for group in manifest['groups']) >= 3
    assert any(group['group'].startswith('apartment/rent/11110/2026') for group in manifest['groups'])
    recovered = tmp_path / 'recovered'
    restored = restore_set(recovered, store)
    assert restored['audit'] == audit_checkpoint(root)
    assert restored['source_calls'] == 0
    for row in manifest['files']:
        assert sha256((recovered / row['path']).read_bytes()) == row['sha256']
    assert (store.root / 'set-heads' / (store.head()['sha256'] + '.json')).is_file()
    assert (store.root / 'heads' / (old_head['sha256'] + '.json')).is_file()


def test_old_backup_remains_restorable_before_set_migration(tmp_path):
    root = source(tmp_path)
    legacy = LocalArchive(tmp_path / 'archive')
    backup(root, legacy)
    store = LocalArchiveSet(legacy.root)
    before = legacy.head()
    result = restore_set(tmp_path / 'restored', store)
    assert result['format'] == 'legacy'
    assert result['audit'] == audit_checkpoint(root)
    assert store.head() is None and legacy.head() == before


def test_partitioned_legacy_manifest_and_checkpoint_segments_still_restore(tmp_path):
    from pipeline.real_estate_manifest import checkpoint_row, load_manifest, read_file, save_manifest
    root = source(tmp_path); legacy = LocalArchive(tmp_path / 'archive')
    backup(root, legacy)
    previous = legacy.head(); flat = load_manifest(legacy, previous)
    old_checkpoint = next(row for row in flat['files'] if row['path'] == 'checkpoint.sqlite')
    raw = read_file(old_checkpoint, legacy.get)
    segmented, _ = checkpoint_row(raw, raw, old_checkpoint, legacy)
    rows = [segmented if row['path'] == 'checkpoint.sqlite' else row for row in flat['files']]
    modern, _ = save_manifest(legacy, rows, flat['audit'], previous)
    legacy.promote(modern, previous)
    grouped = LocalArchiveSet(legacy.root)
    assert restore_set(tmp_path / 'legacy-v2-restored', grouped)['audit'] == audit_checkpoint(root)
    backup_set(root, grouped)
    assert restore_set(tmp_path / 'set-after-v2-restored', grouped)['audit'] == audit_checkpoint(root)
    assert legacy.head() == modern


def test_incremental_set_reuses_objects_and_retains_historical_set_heads(tmp_path):
    root = source(tmp_path); store = LocalArchiveSet(tmp_path / 'archive')
    backup_set(root, store)
    previous = store.head()
    before = {row['path']: row for row in load_set(store, previous)['files']}
    add_run(root, b'{"fixture":"new-run"}')
    plan = plan_set(root, store)
    assert plan['new_object_upper_bytes'] < plan['source_bytes']
    assert plan['backup_and_restore_upper_bytes'] >= plan['backup_additional_upper_bytes'] + plan['source_bytes']
    result = backup_set(root, store)
    after = {row['path']: row for row in load_set(store, store.head())['files']}
    for path, row in before.items():
        if path != 'checkpoint.sqlite':
            assert after[path] == row
    assert result['space']['fits_backup']
    assert (store.root / 'set-heads' / (previous['sha256'] + '.json')).exists()
    assert restore_set(tmp_path / 'old-restore', store, previous)['audit'] == audit_checkpoint(tmp_path / 'old-restore')


def test_changed_checkpoint_keeps_unchanged_slices_instead_of_daily_full_copies(tmp_path):
    import sqlite3
    from contextlib import closing
    root = source(tmp_path); store = LocalArchiveSet(tmp_path / 'archive')
    with closing(sqlite3.connect(root / 'checkpoint.sqlite')) as db:
        db.execute('CREATE TABLE fixture_retained_payload (id INTEGER PRIMARY KEY, body BLOB)')
        db.executemany('INSERT INTO fixture_retained_payload(body) VALUES (?)', [(b'x' * 4000,)] * 200)
        db.commit()
    backup_set(root, store)
    original = load_set(store, store.head())
    old_checkpoint = next(row for row in original['files'] if row['path'] == 'checkpoint.sqlite')
    with closing(sqlite3.connect(root / 'checkpoint.sqlite')) as db:
        db.execute("INSERT INTO meta(key,value) VALUES ('fixture_updated','small-change')")
        db.commit()
    result = backup_set(root, store)
    updated = load_set(store, store.head())
    checkpoint = next(row for row in updated['files'] if row['path'] == 'checkpoint.sqlite')
    assert 'segments' in checkpoint
    assert 0 < result['checkpoint_changed_bytes'] < checkpoint['bytes'] // 2
    assert any(part['object'] == old_checkpoint['object'] for part in checkpoint['segments'])
    recovered = tmp_path / 'delta-restored'
    assert restore_set(recovered, store)['audit'] == audit_checkpoint(root)
    with closing(sqlite3.connect(recovered / 'checkpoint.sqlite')) as db:
        assert db.execute("SELECT value FROM meta WHERE key='fixture_updated'").fetchone() == ('small-change',)
        assert db.execute('SELECT COUNT(*) FROM fixture_retained_payload').fetchone() == (200,)


def test_failed_group_write_keeps_both_heads_and_source(tmp_path):
    root = source(tmp_path); store = LocalArchiveSet(tmp_path / 'archive')
    backup_set(root, store)
    before = store.head()
    add_run(root, b'{"fixture":"next-run"}')
    original = store.put

    def interrupt(raw):
        original(raw)
        raise RealEstateError('simulated_interruption')

    store.put = interrupt
    with pytest.raises(RealEstateError, match='simulated_interruption'):
        backup_set(root, store)
    assert store.head() == before
    assert (root / 'checkpoint.sqlite').exists()
    assert len(list((store.root / 'set-heads').glob('*.json'))) == 1


def test_disk_reserve_prevents_backup_and_restore_without_partial_destination(tmp_path, monkeypatch):
    root = source(tmp_path); store = LocalArchiveSet(tmp_path / 'archive', reserve_bytes=1024)
    backup_set(root, store)
    before = store.head()
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=1024))
    with pytest.raises(RealEstateError, match='disk_reserve'):
        backup_set(root, store)
    with pytest.raises(RealEstateError, match='disk_reserve'):
        restore_set(tmp_path / 'no-space', store)
    assert store.head() == before
    assert not (tmp_path / 'no-space').exists()


def test_restore_readback_corruption_cannot_be_reported_as_success(tmp_path, monkeypatch):
    from pathlib import Path
    root = source(tmp_path); store = LocalArchiveSet(tmp_path / 'archive')
    backup_set(root, store)
    recovery = tmp_path / 'recovered'
    read_bytes = Path.read_bytes

    def broken_readback(path):
        raw = read_bytes(path)
        if path.is_relative_to(recovery / 'raw'):
            return raw + b'fixture-disk-readback-corruption'
        return raw

    monkeypatch.setattr(Path, 'read_bytes', broken_readback)
    before = store.head()
    with pytest.raises(RealEstateError, match='archive_set_restore_file_hash'):
        restore_set(recovery, store)
    assert recovery.is_dir()  # Interrupted destination remains for diagnosis.
    assert store.head() == before


@pytest.mark.parametrize('kind', ['duplicate', 'wrong-total', 'oversize', 'path-traversal', 'wrong-object'])
def test_restore_list_tampering_is_rejected_before_target_creation(tmp_path, kind):
    root = source(tmp_path); store = LocalArchiveSet(tmp_path / 'archive')
    backup_set(root, store)
    manifest = load_set(store, store.head())
    bad = {key: deepcopy(value) for key, value in manifest.items() if key != 'files'}
    if kind == 'duplicate':
        bad['groups'].append(deepcopy(bad['groups'][0]))
    elif kind == 'wrong-total':
        bad['source_bytes'] += 1
    elif kind == 'oversize':
        bad['groups'][0]['source_bytes'] = MAX_GROUP + 1
    elif kind == 'wrong-object':
        obj = bad['groups'][0]['manifest']
        store._path(obj['sha256']).write_bytes(b'changed')
    else:
        group = bad['groups'][0]
        obj = group['manifest']
        rows = json.loads(store.get(obj['sha256'], obj['bytes']))
        rows[0]['path'] = '../outside.json'
        group['manifest'] = store.put(canonical_bytes(rows))
    descriptor = store.put(canonical_bytes(bad))
    with pytest.raises(RealEstateError):
        restore_set(tmp_path / 'bad-restore', store, descriptor)
    assert not (tmp_path / 'bad-restore').exists()


def test_removed_previous_source_and_active_collector_are_rejected(tmp_path):
    root = source(tmp_path); store = LocalArchiveSet(tmp_path / 'archive')
    add_run(root, b'{"fixture":"retained"}')
    backup_set(root, store)
    # Rename isolates a fixture; production code never removes files.
    path = next((root / 'runs').glob('*.json'))
    path.rename(tmp_path / 'retained-fixture.json')
    with pytest.raises(RealEstateError, match='archive_previous_files_missing'):
        backup_set(root, store)
    import sqlite3
    with sqlite3.connect(root / 'checkpoint.sqlite') as db:
        db.execute("INSERT OR REPLACE INTO lease(id,owner,expires) VALUES (1,'fixture-active',9999999999)")
    with pytest.raises(RealEstateError, match='archive_collector_active'):
        backup_set(root, store)


def test_restore_list_can_exceed_legacy_twenty_gib_without_oversized_group(tmp_path, monkeypatch):
    """Validate large address-space metadata; this is not a 20 GiB disk test."""
    store = LocalArchiveSet(tmp_path / 'archive')
    groups = []; total = 0; count = 0
    for number in range(3):
        rows = []
        for index in range(80):
            size = 100 * 1024**2
            digest = sha256(f'fixture-reference-{number}-{index}'.encode())
            rows.append({'path': f'raw/sale/11110/20260{number + 1}/{digest}.xml',
                         'sha256': digest, 'bytes': size,
                         'object': {'sha256': digest, 'bytes': size}, 'offset': 0})
        if number == 0:
            digest = sha256(b'fixture-checkpoint-reference')
            rows.append({'path': 'checkpoint.sqlite', 'sha256': digest, 'bytes': 65536,
                         'object': {'sha256': digest, 'bytes': 65536}, 'offset': 0})
        size = sum(row['bytes'] for row in rows)
        groups.append({'group': 'apartment/sale/11110/2026', 'part': number,
                       'manifest': store.put(canonical_bytes(rows)), 'files': len(rows), 'source_bytes': size})
        count += len(rows); total += size
    descriptor = store.put(canonical_bytes({'schema_version': 1, 'kind': 'private-collector-backup-set',
        'groups': groups, 'group_limit_bytes': MAX_GROUP, 'file_count': count,
        'source_bytes': total, 'audit': {}, 'parent': None}))
    value = load_set(store, descriptor)
    assert value['source_bytes'] > 20 * 1024**3
    assert all(group['source_bytes'] <= MAX_GROUP for group in value['groups'])
    # This metadata-only fixture must not depend on the host having 20 GiB free.
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=64 * 1024**3))
    # Metadata acceptance cannot make missing objects a successful restore.
    with pytest.raises(OSError):
        restore_set(tmp_path / 'not-restored', store, descriptor)
    assert not (tmp_path / 'not-restored').exists()


def test_small_file_group_also_splits_before_index_limit(tmp_path, monkeypatch):
    import pipeline.real_estate_local_archive_set as archive_set
    root = source(tmp_path); store = LocalArchiveSet(tmp_path / 'archive')
    for index in range(15):
        add_run(root, canonical_bytes({'index': index}))
    monkeypatch.setattr(archive_set, 'MAX_GROUP_MANIFEST', 2048)
    backup_set(root, store)
    manifest = load_set(store, store.head())
    assert sum(group['group'] == 'metadata' for group in manifest['groups']) > 1
    assert all(group['manifest']['bytes'] <= 2048 for group in manifest['groups'])


@pytest.mark.parametrize('missing', [False, True])
def test_declared_checkpoint_hash_never_substitutes_for_actual_source_bytes(tmp_path, missing):
    import sqlite3
    root = source(tmp_path); store = LocalArchiveSet(tmp_path / 'archive')
    with sqlite3.connect(root / 'checkpoint.sqlite') as db:
        job_id, raw_pages = db.execute("SELECT id,pages FROM jobs WHERE pages!='[]' LIMIT 1").fetchone()
        pages = json.loads(raw_pages)
        if missing:
            path = root / pages[0]['path']
            path.rename(tmp_path / 'retained-original.xml')
        else:
            pages[0]['sha256'] = '0' * 64
            db.execute('UPDATE jobs SET pages=? WHERE id=?', [json.dumps(pages), job_id])
    with pytest.raises(RealEstateError, match='archive_reference_hash'):
        backup_set(root, store)
    assert store.head() is None
    assert not (store.root / 'objects').exists()
