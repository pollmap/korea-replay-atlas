from contextlib import closing
import json
import sqlite3
import time

import pytest

from pipeline.real_estate import RealEstateError, sha256
from pipeline.real_estate_archive import audit_checkpoint, backup, restore
from pipeline.real_estate_fetch import Collector, immutable
from pipeline.real_estate_local_archive import LocalArchive
from pipeline.real_estate_local_archive_set import LocalArchiveSet, backup_set, load_set, restore_set
from pipeline.real_estate_publish import verify_snapshot
from pipeline.real_estate_working_store import INDEX, append_archived_paths, migrate, read_reference
from test_real_estate_archive import source
from test_real_estate_fetch import registry, STAMP, xml, rent


def setup(tmp_path):
    root = source(tmp_path)
    store = LocalArchiveSet(tmp_path / 'cas')
    backup_set(root, store)
    manifest = load_set(store, store.head())
    rows = [row for row in manifest['files'] if row['path'].split('/')[0] in ('raw', 'snapshots')]
    return root, store, rows


def job(root):
    with closing(sqlite3.connect(root / 'checkpoint.sqlite')) as db:
        db.row_factory = sqlite3.Row
        return dict(db.execute("SELECT * FROM jobs WHERE snapshot IS NOT NULL LIMIT 1").fetchone())


def files(root):
    return {str(path.relative_to(root)): sha256(path.read_bytes())
            for path in root.rglob('*') if path.is_file()}


def test_prepare_does_not_delete_and_retirement_preserves_exact_ledger_and_source(tmp_path):
    root, store, rows = setup(tmp_path)
    original = {row['path']: (root / row['path']).read_bytes() for row in rows}
    checkpoint = (root / 'checkpoint.sqlite').read_bytes()
    archive_files = files(store.root)
    prior_audit = audit_checkpoint(root)
    prior_partition = verify_snapshot(root, job(root))
    prepared = migrate(root, store)
    assert prepared['indexed'] == len(rows) and prepared['retired'] == 0
    assert all((root / name).exists() for name in original)
    retired = migrate(root, store, retire_plaintext=True, readers_deployed=True)
    assert retired['retired'] == len(rows)
    assert retired['source_calls'] == retired['archive_objects_written'] == 0
    assert retired['retired_logical_bytes'] == sum(map(len, original.values()))
    assert (root / 'checkpoint.sqlite').read_bytes() == checkpoint
    assert files(store.root) == archive_files
    assert not any((root / row['path']).exists() for row in rows)
    for row in rows:
        assert read_reference(root, row) == original[row['path']]
    assert audit_checkpoint(root) == prior_audit
    assert verify_snapshot(root, job(root)) == prior_partition
    before = files(root)
    assert migrate(root, store, retire_plaintext=True, readers_deployed=True)['verified'] == 0
    assert files(root) == before


def test_reader_deployment_is_required_before_any_retirement(tmp_path):
    root, store, rows = setup(tmp_path)
    with pytest.raises(RealEstateError, match='readers_not_deployed'):
        migrate(root, store, retire_plaintext=True)
    assert not (root / INDEX).exists()
    assert all((root / row['path']).exists() for row in rows)


def test_interrupted_migration_resumes_without_duplicate_archive_or_ledger_changes(tmp_path):
    root, store, rows = setup(tmp_path)
    checkpoint = (root / 'checkpoint.sqlite').read_bytes()
    previous = files(store.root)
    def interrupted(_):
        raise RuntimeError('interrupted_after_commit')
    with pytest.raises(RuntimeError, match='interrupted_after_commit'):
        migrate(root, store, retire_plaintext=True, readers_deployed=True, progress=interrupted)
    assert len([row for row in rows if not (root / row['path']).exists()]) == 1
    result = migrate(root, store, retire_plaintext=True, readers_deployed=True)
    assert result['retired'] == len(rows) - 1
    assert (root / 'checkpoint.sqlite').read_bytes() == checkpoint
    assert files(store.root) == previous
    assert audit_checkpoint(root)['verified_references'] > 0


def test_limit_is_a_checkpoint_boundary_and_missing_plaintext_is_not_silently_adopted(tmp_path):
    root, store, rows = setup(tmp_path)
    result = migrate(root, store, limit=1, retire_plaintext=True, readers_deployed=True)
    assert result['verified'] == result['retired'] == 1
    assert migrate(root, store, limit=1, retire_plaintext=True, readers_deployed=True)['retired'] == 1
    other = tmp_path / 'other'; other.mkdir()
    root2, store2, rows2 = setup(other)
    (root2 / rows2[0]['path']).unlink()
    with pytest.raises(RealEstateError, match='working_store_missing'):
        migrate(root2, store2)


def test_corrupt_plaintext_or_cas_never_becomes_success(tmp_path):
    root, store, rows = setup(tmp_path)
    damaged = root / rows[0]['path']
    damaged.write_bytes(b'corrupt')
    with pytest.raises(RealEstateError, match='checkpoint_size_mismatch'):
        migrate(root, store, retire_plaintext=True, readers_deployed=True)
    assert damaged.exists()
    other = tmp_path / 'other'; other.mkdir()
    root2, store2, rows2 = setup(other)
    row = rows2[0]
    encoded = store2._path(row['object']['sha256']).with_suffix('.encoded')
    target = encoded if encoded.exists() else store2._path(row['object']['sha256'])
    target.write_bytes(b'corrupt')
    with pytest.raises((RealEstateError, OSError)):
        migrate(root2, store2, retire_plaintext=True, readers_deployed=True)
    assert all((root2 / item['path']).exists() for item in rows2)


def test_missing_or_mismatched_compressed_file_fails_closed(tmp_path):
    root, store, rows = setup(tmp_path)
    migrate(root, store, retire_plaintext=True, readers_deployed=True)
    row = rows[0]
    with pytest.raises(RealEstateError, match='reference_mismatch'):
        read_reference(root, {**row, 'sha256': '0' * 64})
    target = store._path(row['object']['sha256'])
    target.unlink(missing_ok=True); target.with_suffix('.encoded').unlink(missing_ok=True)
    with pytest.raises(OSError):
        read_reference(root, row)
    with pytest.raises(OSError):
        audit_checkpoint(root)


def test_both_backup_formats_include_retired_files_and_restore_plaintext(tmp_path):
    root, store, rows = setup(tmp_path)
    original = audit_checkpoint(root)
    migrate(root, store, retire_plaintext=True, readers_deployed=True)
    # Incremental and independent backups must not drop retired logical paths.
    backup_set(root, store)
    target = tmp_path / 'set-restored'
    assert restore_set(target, store)['audit'] == original
    legacy = LocalArchive(tmp_path / 'legacy')
    assert backup(root, legacy)['audit'] == original
    plain = tmp_path / 'legacy-restored'
    assert restore(plain, legacy)['audit'] == original
    for restored in (target, plain):
        assert not (restored / INDEX).exists()
        for row in rows:
            assert sha256((restored / row['path']).read_bytes()) == row['sha256']


def test_collector_refresh_reads_previous_snapshot_and_does_not_reinflate_same_raw(tmp_path):
    root, store, rows = setup(tmp_path)
    migrate(root, store, retire_plaintext=True, readers_deployed=True)
    raw = next(row for row in rows if row['path'].startswith('raw/'))
    body = read_reference(root, raw)
    assert immutable(root, raw['path'], body)['sha256'] == raw['sha256']
    assert not (root / raw['path']).exists()
    c = Collector(root, registry(), months=1, clock=lambda: STAMP,
                  transport=lambda *a, **kw: xml([rent()]), reserve_bytes=0)
    try:
        result = c.reprocess()
        assert result['requests'] == 0 and result['jobs'] == 1
        with c.db:
            c.db.execute("UPDATE jobs SET status='pending' WHERE snapshot IS NOT NULL")
        refreshed = c.collect('fixture-key', max_requests=1, min_interval=0, refresh=True)
        assert refreshed['requests'] == 1
    finally:
        c.close()
    assert not (root / raw['path']).exists()
    assert verify_snapshot(root, job(root))['audit']['record_count'] == 1


def test_active_collector_or_linked_path_prevents_retirement(tmp_path):
    root, store, rows = setup(tmp_path)
    with closing(sqlite3.connect(root / 'checkpoint.sqlite')) as db:
        db.execute('INSERT OR REPLACE INTO lease VALUES (1,?,?)', ('test-owner', time.time() + 300))
        db.commit()
    with pytest.raises(RealEstateError, match='archive_collector_active'):
        migrate(root, store, retire_plaintext=True, readers_deployed=True)
    assert all((root / row['path']).exists() for row in rows)
    for name in ('../outside.xml', '/outside.xml', 'raw/../outside.xml'):
        with pytest.raises(RealEstateError, match='working_store_path'):
            read_reference(root, {**rows[0], 'path': name})


def test_archive_mount_override_does_not_create_missing_store(tmp_path, monkeypatch):
    root, store, rows = setup(tmp_path)
    migrate(root, store, retire_plaintext=True, readers_deployed=True)
    nonexistent = tmp_path / 'missing-mount'
    monkeypatch.setenv('KOREA_REPLAY_WORKING_ARCHIVE', str(nonexistent))
    with pytest.raises(RealEstateError, match='working_store_archive_missing'):
        read_reference(root, rows[0])
    assert not nonexistent.exists()


def test_pack_cache_reuses_decoding_but_detects_replaced_object(tmp_path, monkeypatch):
    import pipeline.real_estate_working_store as working
    root, store, rows = setup(tmp_path)
    migrate(root, store, retire_plaintext=True, readers_deployed=True)
    working._PACK_CACHE.clear()
    calls = []
    original = LocalArchive.get
    def counted(self, digest, size):
        calls.append(digest)
        return original(self, digest, size)
    monkeypatch.setattr(LocalArchive, 'get', counted)
    row = rows[0]
    for _ in range(3):
        read_reference(root, row)
    assert calls == [row['object']['sha256']]
    target = store._path(row['object']['sha256'])
    if not target.exists():
        target = target.with_suffix('.encoded')
    target.write_bytes(b'changed-after-cache')
    with pytest.raises(RealEstateError):
        read_reference(root, row)
    assert len(calls) == 2
    assert sum(map(len, working._PACK_CACHE.values())) <= working.PACK_CACHE_BYTES


def test_index_tampering_cannot_redirect_or_relabel_bytes(tmp_path):
    root, store, rows = setup(tmp_path)
    migrate(root, store, retire_plaintext=True, readers_deployed=True)
    row = rows[0]
    forged = {**row, 'path': '../outside.xml'}
    with closing(sqlite3.connect(root / INDEX)) as db:
        db.execute('UPDATE files SET descriptor=? WHERE path=?', (json.dumps(forged), row['path']))
        db.commit()
    with pytest.raises(RealEstateError):
        read_reference(root, row)
    with pytest.raises(RealEstateError):
        append_archived_paths(root, [])


def test_plain_json_snapshot_can_retire_without_rewriting_old_ledger_descriptor(tmp_path):
    from pipeline.real_estate_storage import decode_snapshot
    root = source(tmp_path)
    old_job = job(root)
    old_ref = json.loads(old_job['snapshot'])
    decoded = decode_snapshot((root / old_ref['path']).read_bytes(), old_ref)
    old_name = 'snapshots/' + old_job['id'] + '/' + sha256(decoded) + '.json'
    plain_ref = immutable(root, old_name, decoded)
    with closing(sqlite3.connect(root / 'checkpoint.sqlite')) as db:
        db.execute('UPDATE jobs SET snapshot=? WHERE id=?', (json.dumps(plain_ref), old_job['id']))
        db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?)',
                   (old_job['id'], plain_ref['sha256'], json.dumps(plain_ref), STAMP))
        db.commit()
    before = verify_snapshot(root, job(root))
    store = LocalArchiveSet(tmp_path / 'cas'); backup_set(root, store)
    migrate(root, store, retire_plaintext=True, readers_deployed=True)
    assert read_reference(root, plain_ref) == decoded
    assert not (root / old_name).exists()
    assert json.loads(job(root)['snapshot']) == plain_ref
    assert verify_snapshot(root, job(root)) == before


def test_pack_cache_evicts_to_its_byte_budget(tmp_path, monkeypatch):
    import pipeline.real_estate_working_store as working
    store = LocalArchive(tmp_path / 'cas')
    working._PACK_CACHE.clear()
    monkeypatch.setattr(working, 'PACK_CACHE_BYTES', 64)
    for index in range(3):
        body = bytes([index]) * 40
        ref = store.put(body)
        assert working._read_pack(store, ref['sha256'], ref['bytes']) == body
        assert sum(map(len, working._PACK_CACHE.values())) <= 64
    assert len(working._PACK_CACHE) == 1
    working._PACK_CACHE.clear()


def test_vps_api_and_cutover_export_read_retired_snapshots(tmp_path):
    from pipeline.vps_runtime import PropertyAPI
    from pipeline.vps_transfer import export, restore as restore_transfer
    from test_vps_runtime import acquired
    data, root = acquired(tmp_path)
    store = LocalArchiveSet(tmp_path / 'cas'); backup_set(root, store)
    expected = PropertyAPI(data).dispatch('/api/v1/property/transactions?regionCode=11110&month=202609&trade=rent')
    migrate(root, store, retire_plaintext=True, readers_deployed=True)
    assert PropertyAPI(data).dispatch('/api/v1/property/transactions?regionCode=11110&month=202609&trade=rent') == expected
    bundle = tmp_path / 'transfer.tar.gz'
    receipt = export(root, bundle, reserve=0)
    target = tmp_path / 'restored-transfer'
    restored = restore_transfer(bundle, target, expected_sha256=receipt['archive_sha256'], reserve=0)
    assert restored['audit'] == audit_checkpoint(root)
    assert verify_snapshot(target, job(target)) == verify_snapshot(root, job(root))


def test_plan_has_no_writes_and_distinguishes_new_unarchived_references(tmp_path):
    from pipeline.real_estate_working_store import plan
    root, store, rows = setup(tmp_path)
    before, archived = files(root), files(store.root)
    result = plan(root, store)
    assert result['writes'] == 0 and result['payload_hashes_verified'] is False
    assert result['ledger_references_not_in_head'] == 0
    assert result['plaintext_files'] == len(rows)
    assert files(root) == before and files(store.root) == archived
    assert not (root / INDEX).exists()
    descriptor = {'path': 'snapshots/test/' + '0' * 64 + '.json', 'sha256': '0' * 64, 'bytes': 123}
    with closing(sqlite3.connect(root / 'checkpoint.sqlite')) as db:
        db.execute('INSERT INTO snapshots VALUES (?,?,?,?)', ('test', '0' * 64, json.dumps(descriptor), STAMP))
        db.commit()
    assert plan(root, store)['ledger_references_not_in_head'] == 1
