import pytest
import shutil
from types import SimpleNamespace

from pipeline.real_estate import RealEstateError
from pipeline.real_estate_archive import audit_checkpoint, backup, restore
from pipeline.real_estate_local_archive import LocalArchive
from test_real_estate_archive import source


def test_local_archive_compressed_objects_keep_logical_identity(tmp_path):
    store = LocalArchive(tmp_path / 'backup')
    raw = b'official raw XML original\n' * 2000
    descriptor = store.put(raw)
    path = store._path(descriptor['sha256'])
    assert not path.exists()
    assert path.with_suffix('.encoded').stat().st_size < len(raw)
    assert store.get(**dict(digest=descriptor['sha256'], size=len(raw))) == raw
    assert store.put(raw) == descriptor


def test_compaction_preserves_head_and_restores_old_bytes(tmp_path):
    from pipeline.real_estate import sha256
    store = LocalArchive(tmp_path / 'backup')
    raw = b'old XML backup\n' * 3000
    descriptor = {'sha256': sha256(raw), 'bytes': len(raw)}
    path = store._path(descriptor['sha256']); path.parent.mkdir(parents=True)
    path.write_bytes(raw); store.promote(descriptor, None)
    original_head = (store.root / 'head.json').read_bytes()
    staged = store.compact()
    assert not staged['retired'] and path.exists()
    result = store.compact(retire_raw=True)
    assert result['objects'] == 1 and not path.exists()
    assert store.get(descriptor['sha256'], len(raw)) == raw
    assert store.head() == descriptor
    assert (store.root / 'head.json').read_bytes() == original_head
    assert store.compact(retire_raw=True)['objects'] == 0


def test_compression_rejects_corruption_without_retiring_raw(tmp_path):
    from pipeline.real_estate import sha256
    store = LocalArchive(tmp_path / 'backup'); raw = b'original' * 4000
    digest = sha256(raw); path = store._path(digest)
    path.parent.mkdir(parents=True); path.write_bytes(raw)
    path.with_suffix('.encoded').write_bytes(b'broken')
    with pytest.raises(RealEstateError): store.compact(retire_raw=True)
    assert path.read_bytes() == raw
    path.unlink()
    with pytest.raises(RealEstateError): store.get(digest, len(raw))


def test_compression_reserve_accounts_actual_stored_bytes(tmp_path, monkeypatch):
    store = LocalArchive(tmp_path / 'backup', reserve_bytes=10000)
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=11000))
    raw = b'repeated' * 10000
    descriptor = store.put(raw)
    assert store.get(descriptor['sha256'], len(raw)) == raw
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=10000))
    with pytest.raises(RealEstateError, match='disk_reserve'): store.put(b'another' * 10000)


def test_compressed_restore_of_legacy_checkpoint_roundtrip(tmp_path):
    working = source(tmp_path); store = LocalArchive(tmp_path / 'backup')
    expected = audit_checkpoint(working)
    backup(working, store)
    assert list((store.root / 'objects').glob('*/*.encoded'))
    store.compact(retire_raw=True)
    assert restore(tmp_path / 'restored', store)['audit'] == expected


def test_local_archive_restores_exact_checkpoint_and_original_pages(tmp_path):
    working = source(tmp_path)
    store = LocalArchive(tmp_path / 'backup', reserve_bytes=0)
    expected = audit_checkpoint(working)
    first = backup(working, store)
    assert first['audit'] == expected
    first_head = store.head()
    assert first_head is not None
    assert (store.root / 'heads' / (first_head['sha256'] + '.json')).is_file()

    result = restore(tmp_path / 'recovered', store)
    assert result['audit'] == expected
    assert (tmp_path / 'recovered' / 'checkpoint.sqlite').is_file()
    assert len(list((tmp_path / 'recovered' / 'raw').rglob('*.xml'))) == 1
    assert audit_checkpoint(tmp_path / 'recovered') == expected
    assert backup(working, store)['audit'] == expected
    assert store.head() != first_head
    assert (store.root / 'heads' / (first_head['sha256'] + '.json')).is_file()


def test_local_archive_rejects_corrupt_object_and_stale_head(tmp_path):
    store = LocalArchive(tmp_path / 'backup', reserve_bytes=0)
    first = store.put(b'first')
    second = store.put(b'second')
    store.promote(first, None)
    store.promote(second, first)
    with pytest.raises(RealEstateError, match='archive_head_changed'):
        store.promote(first, first)
    assert store.head() == second
    store._path(second['sha256']).write_bytes(b'broken')
    with pytest.raises(RealEstateError, match='local_archive_object_changed'):
        store.head()
    with pytest.raises(RealEstateError, match='local_archive_object_changed'):
        store.put(b'second')


def test_failed_local_backup_never_promotes_partial_head(tmp_path):
    working = source(tmp_path)
    store = LocalArchive(tmp_path / 'backup', reserve_bytes=0)
    backup(working, store)
    original = store.head()
    actual = store.put

    def interrupt(raw):
        actual(raw)
        raise RealEstateError('simulated_interruption')

    store.put = interrupt
    with pytest.raises(RealEstateError, match='simulated_interruption'):
        backup(working, store)
    assert store.head() == original
