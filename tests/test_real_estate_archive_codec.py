import base64
import json
import lzma
import random
import zlib

import pytest

from pipeline.real_estate import RealEstateError, sha256
from pipeline import real_estate_archive as archive
from test_real_estate_archive import LocalD1, source


def chunks(encoded):
    return [base64.b64encode(encoded[i:i+archive.CHUNK]).decode()
            for i in range(0, len(encoded), archive.CHUNK)]


def install(store, raw, encoded, *, parts=None):
    digest = sha256(raw)
    key = archive.xz_object_key(digest) if encoded.startswith(archive.XZ_OBJECT_HEADER) else digest
    db = store.databases[store.database(key)]
    rows = chunks(encoded)
    for part, value in enumerate(rows):
        if parts is None or part in parts:
            db.execute('INSERT INTO archive_chunks VALUES (?,?,?)', [key, part, value])
    db.commit()
    return key, db


def stored_rows(store):
    return {name: [tuple(row) for row in db.execute('SELECT * FROM archive_chunks ORDER BY digest,part')]
            for name, db in store.databases.items() if name != 'control'}


@pytest.mark.parametrize('raw', [b'x', b'raw XML ' * 10000, random.Random(4).randbytes(300000)], ids=['tiny', 'repeated', 'incompressible'])
def test_adaptive_envelope_never_exceeds_legacy_budget_and_roundtrips(raw):
    encoded = archive.encode_object(raw)
    assert len(encoded) <= len(zlib.compress(raw, 6))
    assert archive.decode_object(encoded, sha256(raw), len(raw)) == raw
    assert sum(len(c) + 512 for c in chunks(encoded)) <= archive.raw_storage_allowance(len(raw), 1)


def test_new_xz_namespace_keeps_existing_legacy_key_empty():
    raw = b'<apt>official source bytes</apt>' * 50000
    store = LocalD1(); ref = store.put(raw)
    key = archive.xz_object_key(ref['sha256'])
    assert key != ref['sha256']
    assert not store.databases[store.database(ref['sha256'])].execute(
        'SELECT 1 FROM archive_chunks WHERE digest=?', [ref['sha256']]).fetchall()
    encoded = b''.join(base64.b64decode(row[0]) for row in store.databases[store.database(key)].execute(
        'SELECT payload FROM archive_chunks WHERE digest=? ORDER BY part', [key]))
    assert encoded.startswith(archive.XZ_OBJECT_HEADER)
    offset = len(archive.XZ_OBJECT_HEADER)
    assert encoded[offset:offset+32].hex() == sha256(encoded[offset+32:])
    assert store.get(ref['sha256'], ref['bytes']) == raw


@pytest.mark.parametrize('codec', ['zlib', 'xz3-v1'])
def test_complete_existing_object_is_not_reencoded_or_rewritten(codec, monkeypatch):
    raw = b'official XML ' * 10000
    store = LocalD1(); install(store, raw, archive.encode_object(raw, codec=codec))
    before = stored_rows(store)
    def unexpected(*args, **kwargs):
        raise AssertionError('A complete verified object must be reused')
    monkeypatch.setattr(archive, 'encode_object', unexpected)
    assert store.put(raw) == {'sha256': sha256(raw), 'bytes': len(raw)}
    assert stored_rows(store) == before


@pytest.mark.parametrize('codec', ['zlib', 'xz3-v1'])
def test_interrupted_object_resumes_its_original_codec_without_rewriting_parts(codec):
    raw = random.Random(7).randbytes(archive.CHUNK * 7)
    encoded = archive.encode_object(raw, codec=codec)
    store = LocalD1(); key, db = install(store, raw, encoded, parts={0, 2})
    before = list(db.execute('SELECT * FROM archive_chunks WHERE digest=? ORDER BY part', [key]))
    store.put(raw)
    after = {row['part']: tuple(row) for row in db.execute('SELECT * FROM archive_chunks WHERE digest=?', [key])}
    assert all(after[row['part']] == tuple(row) for row in before)
    assert store.get(sha256(raw), len(raw)) == raw
    assert [row[0] for row in db.execute('SELECT payload FROM archive_chunks WHERE digest=? ORDER BY part', [key])] == chunks(encoded)


def test_unknown_encoder_or_conflicting_partial_stream_never_writes_missing_chunks():
    raw = random.Random(14).randbytes(archive.CHUNK * 5)
    encoded = archive.encode_object(raw, codec='xz3-v1')
    store = LocalD1(); key, db = install(store, raw, encoded, parts={0})
    altered = bytearray(encoded[:archive.CHUNK]); altered[len(archive.XZ_OBJECT_HEADER)] ^= 1
    db.execute('UPDATE archive_chunks SET payload=? WHERE digest=?', [base64.b64encode(altered).decode(), key]); db.commit()
    before = stored_rows(store)
    with pytest.raises(RealEstateError, match='archive_object_conflict'):
        store.put(raw)
    assert stored_rows(store) == before


@pytest.mark.parametrize('partial_codec', ['zlib', 'xz3-v1'])
def test_partial_other_namespace_cannot_hide_or_rewrite_complete_copy(partial_codec, monkeypatch):
    raw = random.Random(19).randbytes(archive.CHUNK * 5) * 2
    complete_codec = 'xz3-v1' if partial_codec == 'zlib' else 'zlib'
    store = LocalD1()
    install(store, raw, archive.encode_object(raw, codec=complete_codec))
    install(store, raw, archive.encode_object(raw, codec=partial_codec), parts={0})
    before = stored_rows(store)
    def unexpected(*args, **kwargs):
        raise AssertionError('A verified alternate namespace must be reused')
    monkeypatch.setattr(archive, 'encode_object', unexpected)
    assert store.get(sha256(raw), len(raw)) == raw
    assert store.put(raw) == {'sha256': sha256(raw), 'bytes': len(raw)}
    assert stored_rows(store) == before


def test_concurrent_first_chunk_claim_does_not_mix_encoded_streams():
    raw = random.Random(8).randbytes(archive.CHUNK * 4) * 3
    encoded = archive.encode_object(raw)
    assert encoded.startswith(archive.XZ_OBJECT_HEADER)
    store = LocalD1(); query = store.query; competing = bytearray(encoded[:archive.CHUNK])
    competing[len(archive.XZ_OBJECT_HEADER)] ^= 1
    def race(database, sql, params=()):
        if sql == 'INSERT OR IGNORE INTO archive_chunks(digest,part,payload) VALUES (?,?,?)':
            query(database, sql, [params[0], 0, base64.b64encode(competing).decode()])
        return query(database, sql, params)
    store.query = race
    with pytest.raises(RealEstateError, match='archive_immutable_conflict'):
        store.put(raw)
    all_rows = [row for rows in stored_rows(store).values() for row in rows]
    assert len(all_rows) == 1 and all_rows[0][1] == 0


@pytest.mark.parametrize('fault', ['truncated', 'trailing', 'multiple', 'wrong-hash', 'bomb', 'version', 'stream-hash', 'crc'])
def test_xz_decoder_rejects_invalid_bounded_objects(fault):
    raw = b'<source>exact original</source>' * 1000
    encoded = archive.encode_object(raw, codec='xz3-v1'); size = len(raw); digest = sha256(raw)
    if fault in ('truncated', 'trailing', 'multiple', 'crc'):
        stream = encoded[len(archive.XZ_OBJECT_HEADER)+32:]
        if fault == 'truncated': stream = stream[:-3]
        if fault == 'trailing': stream += b'garbage'
        if fault == 'multiple': stream += stream
        if fault == 'crc': stream = lzma.compress(raw, check=lzma.CHECK_NONE, preset=3)
        encoded = archive.XZ_OBJECT_HEADER + bytes.fromhex(sha256(stream)) + stream
    if fault == 'wrong-hash': digest = '0' * 64
    if fault == 'bomb': size = 1
    if fault == 'version': encoded = b'KRAR\x02' + encoded[5:]
    if fault == 'stream-hash': encoded = encoded[:9] + b'0' * 32 + encoded[41:]
    with pytest.raises(RealEstateError, match='archive_object'):
        archive.decode_object(encoded, digest, size)


def test_xz_decoder_memory_limit_is_enforced(monkeypatch):
    raw = b'restore with a bounded dictionary' * 1000
    encoded = archive.encode_object(raw, codec='xz3-v1')
    monkeypatch.setattr(archive, 'XZ_MEMORY_LIMIT', 1024)
    with pytest.raises(RealEstateError, match='archive_object_encoding'):
        archive.decode_object(encoded, sha256(raw), len(raw))


def test_legacy_backup_and_new_xz_incremental_restore_exact_originals(tmp_path, monkeypatch):
    root = source(tmp_path); store = LocalD1(); adaptive = archive.encode_object
    with monkeypatch.context() as legacy:
        legacy.setattr(archive, 'encode_object', lambda raw, **kw: zlib.compress(raw, 6))
        archive.backup(root, store)
    before = stored_rows(store); old_head = store.head()
    raw = (b'{"evidence":"' + b'unchanged source ' * 30000 + b'"}\n')
    name = sha256(raw) + '.json'; (root/'runs'/name).write_bytes(raw)
    archive.backup(root, store)
    after = stored_rows(store)
    assert all(set(rows) <= set(after[key]) for key, rows in before.items())
    assert store.head() != old_head and archive.encode_object is adaptive
    result = archive.restore(tmp_path/'restored-mixed', store)
    assert result['audit'] == archive.audit_checkpoint(root)
    assert (tmp_path/'restored-mixed'/'runs'/name).read_bytes() == raw
    assert json.loads(store.get(old_head['sha256'], old_head['bytes']))['kind'] == 'private-collector-backup'
