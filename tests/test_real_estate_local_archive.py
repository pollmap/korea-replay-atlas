import pytest

from pipeline.real_estate import RealEstateError
from pipeline.real_estate_archive import audit_checkpoint, backup, restore
from pipeline.real_estate_local_archive import LocalArchive
from test_real_estate_archive import source


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
