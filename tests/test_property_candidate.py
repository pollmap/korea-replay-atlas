import json
import sqlite3
import shutil
from pathlib import Path
import pytest
from pipeline.real_estate import RealEstateError
from pipeline.property_read_model import publish as read_model
from pipeline.property_candidate import run
from test_real_estate_publish import setup


def prepare(tmp_path):
    source = setup(tmp_path)
    data = tmp_path / 'data'; data.mkdir()
    shutil.move(source, data / 'collector')
    model = read_model(data, reserve_bytes=0)
    return data, model


def test_candidate_pins_closed_model_and_preserves_writer(tmp_path):
    data, model = prepare(tmp_path)
    # Change the writer AFTER the closed generation is published.
    with sqlite3.connect(data / 'collector/checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET status='pending', snapshot=NULL, pages='[]'")
    output = tmp_path / 'candidates'; events = []
    result = run(data, output, reserve_bytes=0, progress=events.append)
    assert result['read_model'] == model
    assert result['audit']['source_rows'] == 4
    assert result['audit']['raw_pages_reparsed']
    assert result['audit']['all_transaction_ids_preserved']
    assert result['public_release'] is False
    assert result['source_calls'] == result['ledger_writes'] == 0
    assert json.loads((output / 'last-verified.json').read_bytes()) == result
    with sqlite3.connect(data / 'collector/checkpoint.sqlite') as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE snapshot IS NOT NULL").fetchone()[0] == 0
    assert {e['phase'] for e in events} >= {'raw_audit','monthly_summary','summary_pack','verified'}
    assert run(data, output, reserve_bytes=0) == result


def test_failed_raw_audit_keeps_previous_candidate_and_resumes_pin(tmp_path):
    data, model = prepare(tmp_path); output = tmp_path / 'candidates'
    first = run(data, output, reserve_bytes=0)
    previous = (output / 'last-verified.json').read_bytes()
    # Force a different fingerprint so the immutable existing candidate isn't reused.
    with sqlite3.connect(data / 'collector/checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET updated_at='2026-09-16T00:00:00Z'")
    next_model = read_model(data, reserve_bytes=0)
    raw = next((data / 'collector/raw').rglob('*.xml')); original = raw.read_bytes()
    raw.write_bytes(b'corrupted')
    with pytest.raises(RealEstateError): run(data, output, reserve_bytes=0)
    failed = json.loads((output / 'status.json').read_bytes())
    assert failed['state'] == 'failed'
    assert failed['read_model'] == next_model
    assert (output / 'last-verified.json').read_bytes() == previous
    raw.write_bytes(original)
    read_model(data, reserve_bytes=0)  # Current advances while retry must stay pinned.
    result = run(data, output, reserve_bytes=0)
    assert result['read_model'] == next_model
    assert result['read_model'] != first['read_model']


def test_missing_model_and_forbidden_output_do_not_publish(tmp_path):
    data, _ = prepare(tmp_path)
    with pytest.raises(RealEstateError, match='public_output_forbidden'):
        run(data, tmp_path / 'public', reserve_bytes=0)
    (data / 'read-model/current.json').write_text('{}')
    with pytest.raises(RealEstateError, match='read_model_not_ready'):
        run(data, tmp_path / 'candidates', reserve_bytes=0)
    assert not (tmp_path / 'candidates/last-verified.json').exists()


def test_concurrent_candidate_does_not_start_second_writer(tmp_path):
    from pipeline.real_estate_local_archive import _lock
    data, _ = prepare(tmp_path); output = tmp_path / 'candidates'; output.mkdir()
    with _lock(output / 'candidate.lock'):
        with pytest.raises(RealEstateError, match='local_archive_writer_active'):
            run(data, output, reserve_bytes=0)
    assert not (output / 'status.json').exists()



def test_candidate_explicit_gzip_all_stages_keeps_plain_publications(tmp_path):
    from pipeline.real_estate_publish import publish, checked_read, MAX_ASSET
    from test_real_estate_fetch import registry
    data, model = prepare(tmp_path); output = tmp_path / 'candidates'
    legacy = publish(data / 'collector', registry(), output / 'transactions',
                     checkpoint=data / 'read-model' / (model['generation'] + '.sqlite'),
                     packed_transactions=True, reserve_bytes=0)
    legacy_root = output / 'transactions' / legacy['release_id']
    legacy_bytes = {p.relative_to(legacy_root): p.read_bytes() for p in legacy_root.rglob('*') if p.is_file()}
    result = run(data, output, reserve_bytes=0)
    assert result['property_release']['release_id'] != legacy['release_id']
    assert legacy_bytes == {p.relative_to(legacy_root): p.read_bytes() for p in legacy_root.rglob('*') if p.is_file()}
    receipts = [result['property_publication'], result['summary_publication']]
    receipts.extend(str(p) for p in (output / 'summaries').glob('summary-*/publication.json'))
    assert len(receipts) == 3
    for receipt in receipts:
        receipt_path = Path(receipt)
        publication = json.loads(receipt_path.read_bytes())
        encoded = [f for f in publication['files'] if 'transport' in f]
        assert encoded
        for asset in encoded:
            body = (receipt_path.parent / asset['path']).read_bytes()
            assert body.startswith(b'\x1f\x8b')
            decoded = checked_read(receipt_path.parent,
                                   {**asset, 'bytes':asset['byte_length']}, MAX_ASSET)
            assert len(decoded) == asset['transport']['decoded_bytes']
            assert isinstance(json.loads(decoded), dict)
        assert publication['audit']['source_rows'] == 4
    assert result['audit']['all_transaction_ids_preserved']
    assert result['summary_audit']['source_calls'] == result['summary_audit']['ledger_writes'] == 0


def test_candidate_closes_cache_and_reuses_unchanged_jobs_across_generations(tmp_path, monkeypatch):
    from unittest.mock import Mock
    import pipeline.property_candidate as candidate
    import pipeline.real_estate_publish as publishing
    data, _ = prepare(tmp_path); output = tmp_path / 'candidates'; caches = []
    original = candidate.VerificationCache
    class TrackedCache(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs); caches.append(self)
    monkeypatch.setattr(candidate, 'VerificationCache', TrackedCache)
    parse = Mock(wraps=publishing.normalize_xml_page)
    monkeypatch.setattr(publishing, 'normalize_xml_page', parse)
    first = run(data, output, reserve_bytes=0)
    assert parse.call_count == 2
    with pytest.raises(sqlite3.ProgrammingError): caches[-1].db.execute('SELECT 1')
    files = {str(p):p.read_bytes() for folder in ['transactions','summaries','summary-packs']
             for p in (output / folder).rglob('*') if p.is_file()}
    parse.reset_mock()
    assert run(data, output, reserve_bytes=0) == first
    assert parse.call_count == 0
    assert all(Path(path).read_bytes() == value for path, value in files.items())
    assert len(files) == sum(1 for folder in ['transactions','summaries','summary-packs']
                             for p in (output / folder).rglob('*') if p.is_file())
    with sqlite3.connect(data / 'collector/checkpoint.sqlite') as db:
        # A completed no-change correction changes the read-model generation,
        # but neither the immutable publication nor its audited job inputs.
        db.execute("UPDATE calls SET started_at='2026-09-16T00:00:00Z'")
    next_model = read_model(data, reserve_bytes=0)
    second = run(data, output, reserve_bytes=0)
    assert second['read_model'] == next_model
    assert second['property_release'] == first['property_release']
    assert parse.call_count == 0
    status = json.loads((output / 'status.json').read_bytes())
    assert status['verification_execution']['cache_hits'] == 0
    assert status['verification_execution']['cache_misses'] == 0
    assert all(Path(path).read_bytes() == value for path, value in files.items())
    assert len(files) == sum(1 for folder in ['transactions','summaries','summary-packs']
                             for p in (output / folder).rglob('*') if p.is_file())
    # One changed audited input creates a new publication. Its unchanged peer
    # must reuse its receipt while the changed job is reparsed exactly once.
    with sqlite3.connect(data / 'collector/checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET updated_at='2026-09-17T00:00:00Z' WHERE trade_type='sale'")
    read_model(data, reserve_bytes=0)
    third = run(data, output, reserve_bytes=0)
    assert third['property_release'] != first['property_release']
    assert parse.call_count == 1
    assert third['audit']['verification_cache']['hits'] == 1
    assert third['audit']['verification_cache']['misses'] == 1
    assert second['audit']['source_transaction_identity_sha256'] == first['audit']['source_transaction_identity_sha256']
    for cache in caches:
        with pytest.raises(sqlite3.ProgrammingError): cache.db.execute('SELECT 1')
    status = json.loads((output / 'status.json').read_bytes())
    assert status['verification_execution']['cache_hits'] == 1
    assert status['verification_execution']['cache_misses'] == 1
    assert status['input_sizes']['payloads_read'] == 0


def test_candidate_cache_closes_on_audit_error(tmp_path, monkeypatch):
    import pipeline.property_candidate as candidate
    data, _ = prepare(tmp_path); caches = []
    original = candidate.VerificationCache
    class TrackedCache(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs); caches.append(self)
        def verify(self, *args):
            raise RealEstateError('audit_failure')
    monkeypatch.setattr(candidate, 'VerificationCache', TrackedCache)
    output = tmp_path / 'candidates'
    with pytest.raises(RealEstateError, match='audit_failure'): run(data, output, reserve_bytes=0)
    assert not (output / 'last-verified.json').exists()
    assert json.loads((output / 'status.json').read_bytes())['state'] == 'failed'
    with pytest.raises(sqlite3.ProgrammingError): caches[-1].db.execute('SELECT 1')


@pytest.mark.parametrize('reserve', [-1, True, 2.5, None])
def test_candidate_rejects_invalid_reserve_before_work(tmp_path, reserve):
    data, _ = prepare(tmp_path); output = tmp_path / 'candidates'
    with pytest.raises(RealEstateError, match='invalid_disk_reserve'):
        run(data, output, reserve_bytes=reserve)
    assert not output.exists()


def test_candidate_space_guard_preserves_last_good_before_any_audit(tmp_path, monkeypatch):
    from collections import namedtuple
    from unittest.mock import Mock
    import pipeline.property_candidate as candidate
    data, _ = prepare(tmp_path); output = tmp_path / 'candidates'
    run(data, output, reserve_bytes=0)
    previous = (output / 'last-verified.json').read_bytes()
    assert candidate.RESERVE == 2 * 1024**3
    Usage = namedtuple('Usage', 'total used free')
    monkeypatch.setattr(candidate.shutil, 'disk_usage', lambda _: Usage(20*1024**3, 0, candidate.RESERVE))
    publisher = Mock(side_effect=AssertionError('must stop before publication'))
    monkeypatch.setattr(candidate, 'publish', publisher)
    with pytest.raises(RealEstateError, match='disk_reserve'): run(data, output)
    publisher.assert_not_called()
    assert (output / 'last-verified.json').read_bytes() == previous
    assert json.loads((output / 'status.json').read_bytes())['error_code'] == 'disk_reserve'


def test_candidate_descriptor_totals_do_not_read_or_decode_payloads(tmp_path, monkeypatch):
    from pipeline.property_candidate import input_sizes
    data, model = prepare(tmp_path)
    db = sqlite3.connect(data / 'read-model' / (model['generation'] + '.sqlite'))
    descriptors = [json.loads(row[0]) for row in db.execute('SELECT snapshot FROM jobs WHERE snapshot IS NOT NULL')]
    def forbidden(*args, **kwargs): raise AssertionError('descriptor planning must not read payloads')
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    result = input_sizes(db); db.close()
    assert result['snapshot_references'] == len(descriptors) == 2
    assert result['snapshot_stored_bytes'] == sum(row['bytes'] for row in descriptors)
    assert result['snapshot_decoded_bytes'] == sum(row.get('decoded_bytes', row['bytes']) for row in descriptors)
    assert result['payloads_read'] == 0
    assert result['publication_upper_bytes'] is None


def test_candidate_cache_is_not_written_into_collector(tmp_path):
    data, _ = prepare(tmp_path)
    with pytest.raises(RealEstateError, match='candidate_cache_path'):
        run(data, data / 'collector/private-candidate', reserve_bytes=0)
    assert not (data / 'collector/private-candidate').exists()



def test_changed_verifier_version_does_not_reuse_old_job_receipts(tmp_path, monkeypatch):
    from unittest.mock import Mock
    import pipeline.property_verification_cache as verification
    import pipeline.real_estate_publish as publishing
    data, _ = prepare(tmp_path); output = tmp_path / 'candidates'
    first = run(data, output, reserve_bytes=0)
    # Keep the same ledger generation and mock a source-code change only at the
    # fingerprint's file read. Both imported version functions must observe it;
    # no test-only rebinding of the publisher alias or source file mutation.
    previous_version = verification.version()
    original_read = Path.read_bytes
    normalizer = Path(verification.__file__).parent / 'real_estate.py'
    def changed_code_read(path):
        body = original_read(path)
        return body + b'\n# next normalizer build\n' if path == normalizer else body
    monkeypatch.setattr(Path, 'read_bytes', changed_code_read)
    version = verification.version()
    assert version != previous_version
    assert publishing.verification_version() == version
    parse = Mock(wraps=publishing.normalize_xml_page)
    monkeypatch.setattr(publishing, 'normalize_xml_page', parse)
    result = run(data, output, reserve_bytes=0)
    assert result['read_model'] == first['read_model']
    assert result['property_release'] != first['property_release']
    assert parse.call_count == 2
    assert result['audit']['verification_cache'] == {'hits':0, 'misses':2, 'version':version}
    assert result['audit']['source_transaction_identity_sha256'] == first['audit']['source_transaction_identity_sha256']


def test_candidate_cli_forwards_explicit_reserve(monkeypatch, tmp_path, capsys):
    from unittest.mock import Mock
    import pipeline.property_candidate as candidate
    fake = Mock(return_value={'state':'ready', 'public_release':False})
    monkeypatch.setattr(candidate, 'run', fake)
    monkeypatch.setattr('sys.argv', ['property_candidate', '--data', str(tmp_path/'data'),
                                   '--output', str(tmp_path/'output'), '--reserve-bytes', '1048576'])
    candidate.main()
    assert fake.call_args.kwargs['reserve_bytes'] == 1048576
    assert json.loads(capsys.readouterr().out)['public_release'] is False


@pytest.mark.parametrize('descriptor', [None, [], {'path':'snapshot.json','bytes':True,'sha256':'a'*64},
                                       {'path':'snapshot.json.xz','bytes':20,'sha256':'a'*64,'encoding':'xz','decoded_bytes':-1}])
def test_candidate_input_descriptor_rejects_unusable_sizes(descriptor):
    from pipeline.property_candidate import input_sizes
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE jobs(status TEXT,snapshot TEXT)')
        db.execute('INSERT INTO jobs VALUES (?,?)', ('complete',json.dumps(descriptor)))
        with pytest.raises(RealEstateError, match='candidate_input_descriptor'): input_sizes(db)



def test_candidate_cache_refuses_hardlink_to_writer(tmp_path):
    import os
    data, _ = prepare(tmp_path); output = tmp_path / 'candidates'; output.mkdir()
    source = data / 'collector/checkpoint.sqlite'; before = source.read_bytes()
    os.link(source, output / 'verification.sqlite')
    with pytest.raises(RealEstateError, match='candidate_cache_path'):
        run(data, output, reserve_bytes=0)
    assert source.read_bytes() == before
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='audited'").fetchall() == []


def test_candidate_descriptor_inventory_deduplicates_files_and_ignores_unpublished_states():
    from pipeline.property_candidate import input_sizes
    descriptor = {'path':'silver/pinned.json.xz','sha256':'a'*64,'bytes':10,'encoding':'xz','decoded_bytes':100,'decoded_sha256':'b'*64}
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE jobs(status TEXT,snapshot TEXT)')
        db.executemany('INSERT INTO jobs VALUES (?,?)', [('complete',json.dumps(descriptor)),
            ('failed',json.dumps(descriptor)), ('source_unavailable','not a published input')])
        assert input_sizes(db) == {'snapshot_references':2,'snapshot_files':1,
            'snapshot_stored_bytes':10,'snapshot_decoded_bytes':100,'largest_snapshot_decoded_bytes':100,
            'payloads_read':0,'publication_upper_bytes':None,'enforcement':'stage_preflight_and_per_file_reserve'}
        db.execute('INSERT INTO jobs VALUES (?,?)', ('empty',json.dumps({**descriptor,'bytes':20})))
        with pytest.raises(RealEstateError, match='candidate_input_descriptor_conflict'): input_sizes(db)


@pytest.mark.parametrize('path', ['/absolute.json', '../outside.json', 'silver/../outside.json',
                                 'silver//snapshot.json', './snapshot.json', r'C:\snapshot.json',
                                 r'silver\snapshot.json', 'snapshot.xml', 'snapshot.json.xz'])
def test_candidate_inventory_rejects_ambiguous_or_mismatched_paths(path):
    from pipeline.property_candidate import input_sizes
    descriptor = {'path': path, 'sha256': 'a'*64, 'bytes': 10}
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE jobs(status TEXT,snapshot TEXT)')
        db.execute('INSERT INTO jobs VALUES (?,?)', ('complete', json.dumps(descriptor)))
        with pytest.raises(RealEstateError, match='candidate_input_descriptor'):
            input_sizes(db)


@pytest.mark.parametrize('change', [{'decoded_sha256': None}, {'decoded_sha256': 'not-a-hash'},
                                    {'encoding': 'gzip'}, {'decoded_bytes': True}])
def test_candidate_inventory_validates_compression_identity(change):
    from pipeline.property_candidate import input_sizes
    descriptor = {'path':'silver/snapshot.json.xz', 'sha256':'a'*64, 'bytes':10,
                  'encoding':'xz', 'decoded_bytes':100, 'decoded_sha256':'b'*64, **change}
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE jobs(status TEXT,snapshot TEXT)')
        db.execute('INSERT INTO jobs VALUES (?,?)', ('complete', json.dumps(descriptor)))
        with pytest.raises(RealEstateError, match='candidate_input_descriptor'):
            input_sizes(db)


def test_candidate_inventory_rejects_conflicting_decoded_identity():
    from pipeline.property_candidate import input_sizes
    descriptor = {'path':'silver/snapshot.json.xz', 'sha256':'a'*64, 'bytes':10,
                  'encoding':'xz', 'decoded_bytes':100, 'decoded_sha256':'b'*64}
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE jobs(status TEXT,snapshot TEXT)')
        db.executemany('INSERT INTO jobs VALUES (?,?)', [
            ('complete', json.dumps(descriptor)),
            ('complete', json.dumps({**descriptor, 'decoded_sha256':'c'*64}))])
        with pytest.raises(RealEstateError, match='candidate_input_descriptor_conflict'):
            input_sizes(db)


def test_candidate_output_never_writes_into_read_model(tmp_path):
    data, _ = prepare(tmp_path)
    output = data / 'read-model' / 'candidate'
    with pytest.raises(RealEstateError, match='candidate_cache_path'):
        run(data, output, reserve_bytes=0)
    assert not output.exists()


@pytest.mark.parametrize('operation', ['execute', 'commit'])
def test_cache_constructor_failure_closes_open_connection(tmp_path, monkeypatch, operation):
    import pipeline.property_verification_cache as verification
    real_connect = sqlite3.connect
    connections = []
    class FailingConnection:
        def __init__(self, filename):
            self.connection = real_connect(filename); connections.append(self.connection)
        def execute(self, *args):
            if operation == 'execute': raise sqlite3.OperationalError('schema failure')
            return self.connection.execute(*args)
        def commit(self):
            raise sqlite3.OperationalError('commit failure')
        def close(self): self.connection.close()
    monkeypatch.setattr(verification.sqlite3, 'connect', FailingConnection)
    with pytest.raises(sqlite3.OperationalError):
        verification.VerificationCache(tmp_path / 'cache.sqlite', tmp_path / 'source')
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError): connections[0].execute('SELECT 1')


@pytest.mark.parametrize('suffix', ['-journal', '-wal', '-shm'])
@pytest.mark.parametrize('link_kind', ['symlink', 'hardlink'])
def test_candidate_cache_companion_cannot_modify_source(tmp_path, suffix, link_kind):
    import os
    data, _ = prepare(tmp_path); output = tmp_path / 'candidate'; output.mkdir()
    source = data / 'collector' / 'checkpoint.sqlite'; before = source.read_bytes()
    companion = output / ('verification.sqlite' + suffix)
    if link_kind == 'symlink': companion.symlink_to(source)
    else: os.link(source, companion)
    with pytest.raises(RealEstateError, match='linked_path|verification_cache_path'):
        run(data, output, reserve_bytes=0)
    assert source.read_bytes() == before
    assert not (output / 'verification.sqlite').exists()
    assert not (output / 'last-verified.json').exists()
