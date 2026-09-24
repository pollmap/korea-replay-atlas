import json
import random
from copy import deepcopy

import pytest

from pipeline.real_estate import RealEstateError, canonical_bytes, sha256
from pipeline.real_estate_archive import backup, restore, validate_manifest, audit_checkpoint
from pipeline.real_estate_manifest import (load_manifest, save_manifest, checkpoint_row,
    read_file, BLOCK_BYTES, BUCKETS, MAX_BUCKET_BYTES)
from pipeline.real_estate_remote import run_remote
from pipeline.real_estate_run_guard import CollectionGuard
from test_real_estate_archive import LocalD1, source
from test_real_estate_fetch import xml, STAMP


def parent(tmp_path):
    root = source(tmp_path); store = LocalD1(); backup(root, store)
    return root, store


def test_shared_checkpoint_slices_roundtrip_and_only_changed_bytes_uploaded():
    store = LocalD1(); raw = random.Random(42).randbytes(4 * BLOCK_BYTES + 77)
    obj = store.put(b'prefix' + raw)
    old = {'path': 'checkpoint.sqlite', 'sha256': sha256(raw), 'bytes': len(raw), 'object': obj, 'offset': 6}
    changed = raw[:BLOCK_BYTES] + b'X' + raw[BLOCK_BYTES + 1:]
    row, size = checkpoint_row(changed, raw, old, store)
    assert size == BLOCK_BYTES
    assert read_file(row, store.get) == changed
    assert row['segments'][0]['object'] == obj
    # Growth, another edit and truncation all reconstruct without parent recipes.
    latest = changed[:2 * BLOCK_BYTES] + b'Y' + changed[2 * BLOCK_BYTES + 1:] + b'new'
    nextrow, size = checkpoint_row(latest, changed, row, store)
    assert size == BLOCK_BYTES + 80
    assert read_file(nextrow, store.get) == latest
    short = latest[:BLOCK_BYTES + 32]
    shortrow, size = checkpoint_row(short, latest, nextrow, store)
    assert size == 32 and read_file(shortrow, store.get) == short


def test_v2_root_restores_without_parent_manifest_and_old_v1_still_restores(tmp_path):
    root, store = parent(tmp_path); old = store.head()
    result = run_remote(tmp_path/'runner', store, 'fixture-key', months=2, as_of=STAMP,
                        max_requests=1, transport=lambda *a, **k: xml(), reserve_bytes=0)
    current = store.head(); manifest = load_manifest(store, current)
    assert manifest['schema_version'] == 2
    assert result['checkpoint']['rewritten_manifest_buckets'] == BUCKETS
    recovered = restore(tmp_path/'new', store)
    assert recovered['audit'] == result['checkpoint']['audit']
    assert restore(tmp_path/'old', store, old)['audit'] == audit_checkpoint(root)
    # Old manifest is provenance only; remove it in this disposable fixture.
    db = store.databases[store.database(old['sha256'])]
    db.execute('DELETE FROM archive_chunks WHERE digest=?', [old['sha256']]); db.commit()
    assert restore(tmp_path/'independent', store)['audit'] == recovered['audit']


def test_second_remote_run_reuses_buckets_and_full_manual_backup_is_compatible(tmp_path):
    _, store = parent(tmp_path)
    reports = [run_remote(tmp_path/f'run{i}', store, 'fixture-key', months=2, as_of=STAMP,
                         max_requests=1, transport=lambda *a, **k: xml(), reserve_bytes=0) for i in range(2)]
    assert reports[1]['baseline']['already_imported']
    assert 1 <= reports[1]['checkpoint']['rewritten_manifest_buckets'] <= 4
    full = tmp_path/'full'; restore(full, store)
    before = audit_checkpoint(full)
    backup(full, store)
    assert load_manifest(store, store.head())['schema_version'] == 1
    assert restore(tmp_path/'legacy-format', store)['audit'] == before


def test_reordering_rows_reuses_all_buckets(tmp_path):
    _, store = parent(tmp_path); manifest = load_manifest(store, store.head())
    ref, _ = save_manifest(store, manifest['files'], manifest['audit'], store.head())
    first = load_manifest(store, ref)
    other, count = save_manifest(store, list(reversed(first['files'])), first['audit'], first['parent'], previous=first)
    assert count == 0 and ref == other


@pytest.mark.parametrize('fault', ['missing', 'wrong_bucket', 'count', 'oversized'])
def test_corrupt_partition_never_restores_as_valid(tmp_path, fault):
    _, store = parent(tmp_path); manifest = load_manifest(store, store.head())
    ref, _ = save_manifest(store, manifest['files'], manifest['audit'], store.head())
    root = json.loads(store.get(ref['sha256'], ref['bytes']))
    populated = next(i for i, obj in enumerate(root['buckets']) if obj['bytes'] > 3)
    if fault == 'missing':
        obj = root['buckets'][populated]; db = store.databases[store.database(obj['sha256'])]
        db.execute('DELETE FROM archive_chunks WHERE digest=?', [obj['sha256']]); db.commit()
    elif fault == 'wrong_bucket':
        index = (populated + 1) % BUCKETS
        root['buckets'][index], root['buckets'][populated] = root['buckets'][populated], root['buckets'][index]
    elif fault == 'count': root['file_count'] += 1
    else: root['buckets'][populated]['bytes'] = MAX_BUCKET_BYTES + 1
    ref = store.put(canonical_bytes(root))
    with pytest.raises(RealEstateError): restore(tmp_path/'invalid', store, ref)
    assert not (tmp_path/'invalid').exists()


def test_checkpoint_gaps_overlaps_and_tampering_are_rejected(tmp_path):
    _, store = parent(tmp_path); manifest = load_manifest(store, store.head())
    old = next(r for r in manifest['files'] if r['path'] == 'checkpoint.sqlite')
    raw = read_file(old, store.get)
    segmented, _ = checkpoint_row(raw, raw, old, store)
    value = {**manifest, 'schema_version': 2, 'files': [segmented]}
    for mutate in [lambda r: r['segments'].pop(), lambda r: r['segments'][0].update(bytes=1),
                   lambda r: r['segments'][0].update(offset=10**10)]:
        invalid = deepcopy(value); mutate(invalid['files'][0])
        with pytest.raises(RealEstateError): validate_manifest(invalid)
    swapped = deepcopy(segmented)
    swapped['segments'][0]['sha256'] = '0' * 64
    with pytest.raises(RealEstateError, match='file_hash'): read_file(swapped, store.get)


def test_failed_v2_root_write_keeps_old_head_and_stored_source_for_recovery(tmp_path):
    _, store = parent(tmp_path); before = store.head(); put = store.put
    def fail_root(raw):
        if raw.startswith(b'{'):
            value = json.loads(raw)
            if value.get('schema_version') == 2 and value.get('kind') == 'private-collector-backup':
                raise RealEstateError('fixture_root_write_failed')
        return put(raw)
    store.put = fail_root
    with pytest.raises(RealEstateError, match='fixture_root_write_failed'):
        run_remote(tmp_path/'interrupted', store, 'fixture-key', months=2, as_of=STAMP,
                   max_requests=1, transport=lambda *a, **k: xml(), reserve_bytes=0)
    assert store.head() == before
    guard = CollectionGuard(store)
    assert guard.status()['owner'][0]['occupied'] == 1
    reservations = guard.query('SELECT phase FROM collection_reservations')['results']
    assert reservations == [{'phase': 'stored'}]
    # The last published version remains independently recoverable.
    assert restore(tmp_path/'previous-version', store)['audit'] == load_manifest(store, before)['audit']


def test_many_checkpoint_generations_stay_flat_and_restore_without_recipes():
    store = LocalD1(); raw = random.Random(83).randbytes(16 * BLOCK_BYTES)
    original = store.put(raw)
    row = {'path': 'checkpoint.sqlite', 'sha256': sha256(raw), 'bytes': len(raw),
           'object': original, 'offset': 0}
    # Rotate edits over the whole checkpoint, eventually replacing every old slice.
    for generation in range(40):
        position = generation % 16 * BLOCK_BYTES
        updated = raw[:position] + bytes([generation]) + raw[position + 1:]
        row, size = checkpoint_row(updated, raw, row, store)
        assert size <= BLOCK_BYTES
        assert all(set(part['object']) == {'sha256', 'bytes'} for part in row['segments'])
        raw = updated
    assert read_file(row, store.get) == raw
    assert len(row['segments']) == 16
