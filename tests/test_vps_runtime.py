import io
import json
from pathlib import Path
import sqlite3
import shutil
import tarfile
import time
from contextlib import nullcontext

import pytest

from pipeline.real_estate import RealEstateError, canonical_bytes
from pipeline.real_estate_fetch import Collector
from pipeline.vps_runtime import PropertyAPI, available_trades, write_json
from pipeline import vps_runtime
from pipeline.property_read_model import publish, ReadModel
from pipeline.vps_transfer import export, restore, digest, export_cas, restore_cas
from pipeline.real_estate_local_archive_set import LocalArchiveSet, backup_set
from test_real_estate_fetch import collector, xml, rent, KEY


def acquired(tmp_path):
    data = tmp_path / 'data'; root = data / 'collector'
    c = collector(root, lambda key, trade, *args, **kwargs: xml([rent(), rent()]) if trade == 'rent' else xml())
    c.collect(KEY, max_requests=2, min_interval=0)
    c.close()
    publish(data, reserve_bytes=0)
    return data, root


def test_transfer_exact_call_ledger_rows_and_snapshots_round_trip(tmp_path):
    data, root = acquired(tmp_path)
    bundle = tmp_path / 'cutover.tar.gz'
    receipt = export(root, bundle, reserve=0)
    target = tmp_path / 'restored'
    result = restore(bundle, target, expected_sha256=receipt['archive_sha256'], reserve=0)
    assert result['audit'] == receipt['audit']
    assert result['files'] == receipt['files']
    with sqlite3.connect(root / 'checkpoint.sqlite') as before, sqlite3.connect(target / 'checkpoint.sqlite') as after:
        for table in ('jobs', 'calls', 'snapshots', 'meta'):
            assert before.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall() == after.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()
    with pytest.raises(RealEstateError, match='transfer_existing_target_or_hash'):
        restore(bundle, target, expected_sha256=receipt['archive_sha256'], reserve=0)


def test_transfer_rejects_live_collector_and_wrong_bundle_hash(tmp_path):
    _, root = acquired(tmp_path)
    with sqlite3.connect(root / 'checkpoint.sqlite') as db:
        db.execute('INSERT INTO lease VALUES(1,?,?)', ('other-owner', time.time() + 180))
    with pytest.raises(RealEstateError, match='archive_collector_active'):
        export(root, tmp_path / 'active.tar.gz', reserve=0)
    with sqlite3.connect(root / 'checkpoint.sqlite') as db:
        db.execute('DELETE FROM lease')
    bundle = tmp_path / 'cutover.tar.gz'
    export(root, bundle, reserve=0)
    with pytest.raises(RealEstateError, match='transfer_existing_target_or_hash'):
        restore(bundle, tmp_path / 'wrong', expected_sha256='0' * 64, reserve=0)
    assert not (tmp_path / 'wrong').exists()


@pytest.mark.parametrize('kind', ['missing', 'extra', 'link', 'duplicate', 'corrupt'])
def test_transfer_rejects_incomplete_unknown_link_duplicate_corrupt_files(tmp_path, kind):
    _, root = acquired(tmp_path)
    bundle = tmp_path / 'cutover.tar.gz'; export(root, bundle, reserve=0)
    bad = tmp_path / 'bad.tar.gz'
    with tarfile.open(bundle, 'r:gz') as source, tarfile.open(bad, 'w:gz') as target:
        members = source.getmembers()
        for index, member in enumerate(members):
            if index == 1 and kind == 'missing': continue
            payload = source.extractfile(member).read()
            if index == 1 and kind == 'corrupt': payload = b'x' + payload[1:]
            if index == 1 and kind == 'link':
                member.type = tarfile.SYMTYPE; member.linkname = '/etc/passwd'; member.size = 0
                target.addfile(member); continue
            target.addfile(member, io.BytesIO(payload))
            if index == 1 and kind == 'duplicate': target.addfile(member, io.BytesIO(payload))
        if kind == 'extra':
            member = tarfile.TarInfo('../outside'); member.size = 1
            target.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(RealEstateError):
        restore(bad, tmp_path / 'failed', expected_sha256=digest(bad), reserve=0)
    assert not (tmp_path / 'outside').exists()


def test_api_snapshot_pagination_state_and_hash_binding(tmp_path):
    data, root = acquired(tmp_path); api = PropertyAPI(data)
    code, empty = api.dispatch('/api/v1/property/transactions?regionCode=11110&month=202609&trade=sale')
    assert code == 200 and empty['status'] == 'empty' and empty['total'] == 0
    _, first = api.dispatch('/api/v1/property/transactions?regionCode=11110&month=202609&trade=rent&limit=1')
    assert len(first['records']) == 1 and first['total'] == 2 and first['next_offset'] == 1
    _, second = api.dispatch(f'/api/v1/property/transactions?regionCode=11110&month=202609&trade=rent&limit=1&offset=1&snapshot={first["snapshot"]}')
    assert second['records'][0]['id'] != first['records'][0]['id'] and second['next_offset'] is None
    code, _ = api.dispatch('/api/v1/property/transactions?regionCode=11110&month=202609&trade=rent&snapshot=changed')
    assert code == 409
    with sqlite3.connect(root / 'checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET status='pending' WHERE trade_type='rent'")
    publish(data, reserve_bytes=0)
    _, retained = api.dispatch('/api/v1/property/transactions?regionCode=11110&month=202609&trade=rent')
    assert retained['retained_previous'] and retained['records']
    assert api.dispatch('/.env')[0] == api.dispatch('/raw/foo')[0] == 404


@pytest.mark.parametrize('query', [
    'regionCode=../&month=202609', 'regionCode=11110&month=202613',
    'regionCode=11110&month=202609&limit=101', 'regionCode=11110&month=202609&offset=1',
    'regionCode=11110&month=202609&limit=1&limit=2', 'regionCode=11110&month=202609&key=secret'])
def test_api_rejects_invalid_queries(tmp_path, query):
    data, _ = acquired(tmp_path)
    with pytest.raises(RealEstateError, match='invalid_query'):
        PropertyAPI(data).dispatch('/api/v1/property/transactions?' + query)


def test_api_distinguishes_unacquired_not_planned_and_failed(tmp_path):
    data = tmp_path / 'data'
    c = collector(data / 'collector', lambda *args, **kwargs: xml())
    c.close(); publish(data, reserve_bytes=0); api = PropertyAPI(data)
    _, body = api.dispatch('/api/v1/property/transactions?regionCode=11110&month=202609')
    assert body['status'] == 'pending' and body['total'] is None
    assert api.dispatch('/api/v1/property/transactions?regionCode=11110&month=200609')[0] == 404
    with sqlite3.connect(data / 'collector/checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET status='failed',error_code='upstream_timeout'")
    publish(data, reserve_bytes=0)
    _, body = api.dispatch('/api/v1/property/transactions?regionCode=11110&month=202609')
    assert body['status'] == 'failed' and body['total'] is None


def test_separate_source_budget_keeps_other_trade_collecting(tmp_path):
    c = collector(tmp_path, lambda *args, **kwargs: xml())
    with c.db:
        c.db.execute('INSERT INTO calls(day,trade_type,job_id,page_no,started_at,status) VALUES(?,?,?,?,?,?)',
                     ('2026-09-20', 'rent', 'rent/11110/202609', 1, '2026-09-20T00:00:00Z', 'reserved'))
    assert available_trades(c.db, day='2026-09-20', budget=1) == ['sale']
    result = c.collect(KEY, max_requests=1, min_interval=0, collect_trades=['sale'], daily_budget=1)
    assert result['requests'] == 1
    assert c.db.execute("SELECT status FROM jobs WHERE trade_type='rent'").fetchone()[0] == 'pending'
    assert available_trades(c.db, day='2026-09-20', budget=1) == []
    c.close()


@pytest.mark.parametrize('trades', [[], ['other'], ['sale','sale'], 'sale', [{}], [None]])
def test_collect_trade_filter_validation(tmp_path, trades):
    c = collector(tmp_path, lambda *args, **kwargs: xml())
    with pytest.raises(RealEstateError, match='invalid_trade_filter'):
        c.collect(KEY, collect_trades=trades)
    c.close()


def test_immutable_snapshot_corruption_fails_closed(tmp_path):
    data, root = acquired(tmp_path)
    with sqlite3.connect(root / 'checkpoint.sqlite') as db:
        ref = json.loads(db.execute("SELECT snapshot FROM jobs WHERE trade_type='rent'").fetchone()[0])
    (root / ref['path']).write_bytes(b'corrupted')
    with pytest.raises(RealEstateError, match='snapshot_storage_hash_mismatch'):
        PropertyAPI(data).dispatch('/api/v1/property/transactions?regionCode=11110&month=202609&trade=rent')


def test_runtime_atomic_status_does_not_change_collector(tmp_path):
    data, root = acquired(tmp_path)
    before = digest(root / 'checkpoint.sqlite')
    write_json(data / 'worker-status.json', {'at': '2026-10-01T17:00:00Z', 'state': 'paused'})
    assert PropertyAPI(data).dispatch('/health')[1]['collector_state'] == 'paused'
    assert digest(root / 'checkpoint.sqlite') == before


def test_worker_failure_latches_without_exposing_exception_text(tmp_path, monkeypatch):
    data = tmp_path / 'data'; data.mkdir()
    (data / 'migration-verified.json').write_text('{"status":"verified","audit":{},"files":1}')
    (data / 'collection-enabled').touch()
    monkeypatch.setattr(vps_runtime, 'collector_lock', lambda path: nullcontext())
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError('serviceKey=never-print-this-secret')
    monkeypatch.setattr(vps_runtime, 'collect_once', fail)
    class Finished(Exception): pass
    monkeypatch.setattr(vps_runtime.time, 'sleep', lambda _: (_ for _ in ()).throw(Finished()))
    with pytest.raises(Finished): vps_runtime.worker(data, tmp_path / 'backups', tmp_path / 'secret')
    assert len(attempts) == 1
    hold = (data / 'collection-hold.json').read_text()
    assert 'vps_collection_failed' in hold and 'secret' not in hold
    # Another start still refuses source calls until the operator resolves hold.
    with pytest.raises(Finished): vps_runtime.worker(data, tmp_path / 'backups', tmp_path / 'secret')
    assert len(attempts) == 1


def test_worker_requires_verified_migration_and_explicit_enable(tmp_path, monkeypatch):
    data = tmp_path / 'data'; data.mkdir()
    with pytest.raises(RealEstateError, match='verified_migration_required'):
        vps_runtime.worker(data, tmp_path / 'backups', tmp_path / 'secret')
    (data / 'migration-verified.json').write_text('{}')
    with pytest.raises(RealEstateError, match='verified_migration_required'):
        vps_runtime.worker(data, tmp_path / 'backups', tmp_path / 'secret')
    (data / 'migration-verified.json').write_text('{"status":"verified","audit":{},"files":1}')
    monkeypatch.setattr(vps_runtime, 'collector_lock', lambda path: nullcontext())
    monkeypatch.setattr(vps_runtime, 'collect_once', lambda *a, **k: pytest.fail('paused collector called source'))
    class Finished(Exception): pass
    monkeypatch.setattr(vps_runtime.time, 'sleep', lambda _: (_ for _ in ()).throw(Finished()))
    with pytest.raises(Finished): vps_runtime.worker(data, tmp_path / 'backups', tmp_path / 'secret')
    assert json.loads((data / 'worker-status.json').read_text())['state'] == 'paused'


def test_transfer_disk_reserve_fails_before_bundle_creation(tmp_path, monkeypatch):
    _, root = acquired(tmp_path)
    import collections
    Usage = collections.namedtuple('Usage', 'total used free')
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: Usage(10**12, 10**12-100, 100))
    with pytest.raises(RealEstateError, match='disk_reserve'):
        export(root, tmp_path / 'too-large.tar.gz', reserve=30 * 1024**3)
    assert not (tmp_path / 'too-large.tar.gz').exists()


def test_verified_cas_closure_full_restore_preserves_calls_and_old_data(tmp_path):
    _, root = acquired(tmp_path)
    storage = tmp_path / 'cas'; store = LocalArchiveSet(storage, reserve_bytes=0)
    backup_set(root, store)
    bundle = tmp_path / 'cas.tar.gz'
    receipt = export_cas(storage, bundle, reserve=0)
    recovered = tmp_path / 'recovered'
    result = restore_cas(bundle, tmp_path / 'new-cas', recovered, expected_sha256=receipt['archive_sha256'], reserve=0)
    assert result['audit'] == receipt['audit'] and result['files'] == receipt['files']
    with sqlite3.connect(root / 'checkpoint.sqlite') as before, sqlite3.connect(recovered / 'checkpoint.sqlite') as after:
        assert before.execute('SELECT * FROM calls').fetchall() == after.execute('SELECT * FROM calls').fetchall()
        assert before.execute('SELECT * FROM snapshots').fetchall() == after.execute('SELECT * FROM snapshots').fetchall()
    with pytest.raises(RealEstateError, match='transfer_existing_target_or_hash'):
        restore_cas(bundle, tmp_path / 'new-cas', tmp_path / 'other', expected_sha256=receipt['archive_sha256'], reserve=0)


def test_cas_missing_closure_pack_cannot_become_verified(tmp_path):
    _, root = acquired(tmp_path); store = LocalArchiveSet(tmp_path / 'cas', reserve_bytes=0)
    backup_set(root, store); bundle = tmp_path / 'cas.tar.gz'
    export_cas(store.root, bundle, reserve=0)
    damaged = tmp_path / 'missing.tar.gz'
    with tarfile.open(bundle, 'r:gz') as source, tarfile.open(damaged, 'w:gz') as target:
        for member in source.getmembers()[:-1]:
            target.addfile(member, io.BytesIO(source.extractfile(member).read()))
    with pytest.raises((RealEstateError, OSError)):
        restore_cas(damaged, tmp_path / 'new-cas', tmp_path / 'unverified', expected_sha256=digest(damaged), reserve=0)
    assert not (tmp_path / 'unverified').exists()


def test_cas_stream_receipt_matches_received_bytes_without_local_bundle(tmp_path):
    _, root = acquired(tmp_path); store = LocalArchiveSet(tmp_path / 'cas', reserve_bytes=0)
    backup_set(root, store)
    received = io.BytesIO()
    receipt = export_cas(store.root, stream=received, reserve=0)
    assert not received.closed
    bundle = tmp_path / 'received.tar.gz'; bundle.write_bytes(received.getvalue())
    assert receipt['archive_sha256'] == digest(bundle)
    assert receipt['archive_bytes'] == bundle.stat().st_size
    result = restore_cas(bundle, tmp_path / 'new-cas', tmp_path / 'restored', expected_sha256=receipt['archive_sha256'], reserve=0)
    assert result['audit'] == receipt['audit']


def test_collection_progress_is_bounded_and_excludes_credentials(tmp_path):
    c = collector(tmp_path, lambda key, trade, *a, **kw: xml([rent()]) if trade == 'rent' else xml())
    events = []
    try:
        result = c.collect(KEY, max_requests=2, min_interval=0, progress=events.append)
        assert result['requests'] == 2
        assert [event['requests'] for event in events] == [1, 2]
        assert all(set(event) == {'phase', 'requests', 'response_bytes'} for event in events)
        assert all(event['phase'] == 'collecting' for event in events)
        assert events[1]['response_bytes'] > 0
        with pytest.raises(RealEstateError, match='invalid_progress_callback'):
            c.collect(KEY, progress='invalid')
    finally:
        c.close()


def test_month_filter_preserves_trade_budget_filter(tmp_path):
    seen = []
    def transport(key, trade, *args, **kwargs):
        seen.append(trade)
        return xml()
    c = collector(tmp_path, transport)
    try:
        month = c.db.execute('SELECT deal_month FROM jobs ORDER BY priority LIMIT 1').fetchone()[0]
        c.collect(KEY, max_requests=1, min_interval=0, collect_months=[month], collect_trades=['sale'])
        assert seen == ['sale']
    finally:
        c.close()


def test_read_model_survives_closed_writer_and_has_no_wal_dependency(tmp_path):
    data, root = acquired(tmp_path)
    from contextlib import closing
    with closing(sqlite3.connect(root / 'checkpoint.sqlite')) as writer:
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    assert not (root / 'checkpoint.sqlite-wal').exists()
    model = ReadModel(data)
    path, manifest = model.resolve()
    assert not Path(str(path) + '-wal').exists()
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
    api = PropertyAPI(data)
    assert api.dispatch('/health')[0] == 200
    # Source can be offline and the API still serves the audited generation.
    (root / 'checkpoint.sqlite').rename(root / 'writer-preserved.sqlite')
    assert api.dispatch('/health')[0] == 200
    assert api.dispatch('/api/v1/property/acquisition')[1]['read_model']['generation'] == manifest['generation']


def test_read_model_atomic_promotion_and_bad_candidate_keeps_last_good(tmp_path):
    data, root = acquired(tmp_path)
    reader = ReadModel(data); old = reader.resolve()
    with sqlite3.connect(root / 'checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET status='pending' WHERE trade_type='rent'")
    assert reader.resolve()[1]['generation'] == old[1]['generation']
    new = publish(data, reserve_bytes=0)
    assert reader.resolve()[1]['generation'] == new['generation']
    (data / 'read-model/current.json').write_text('{broken')
    assert reader.resolve()[1]['generation'] == new['generation']
    assert reader.error_code == 'read_model_update_unavailable'
    # Cold starts fail closed if no valid manifest can be selected.
    api = PropertyAPI(data)
    assert api.dispatch('/live')[0] == 200
    assert api.dispatch('/health')[0] == 503


def test_read_model_hash_failure_never_replaces_good_reader(tmp_path):
    data, _ = acquired(tmp_path)
    reader = ReadModel(data); old = reader.resolve()
    candidate = publish(data, reserve_bytes=0)
    (data / 'read-model' / (candidate['generation'] + '.sqlite')).write_bytes(b'bad')
    assert reader.resolve()[1]['generation'] == old[1]['generation']
    assert reader.error_code


def test_online_read_model_excludes_uncommitted_writer_changes(tmp_path):
    data, root = acquired(tmp_path)
    with sqlite3.connect(root / 'checkpoint.sqlite') as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute("UPDATE jobs SET status='pending' WHERE trade_type='rent'")
        manifest = publish(data, reserve_bytes=0)
        path, _ = ReadModel(data).resolve()
        with sqlite3.connect(path) as read:
            assert read.execute("SELECT status FROM jobs WHERE trade_type='rent'").fetchone()[0] == 'complete'
        writer.rollback()
    assert manifest['counts']['jobs'] > 0


def test_read_model_failure_preserves_current_manifest(tmp_path, monkeypatch):
    data, _ = acquired(tmp_path)
    original = (data / 'read-model/current.json').read_bytes()
    import pipeline.property_read_model as module
    monkeypatch.setattr(module, 'sha256', lambda _: (_ for _ in ()).throw(OSError('failure')))
    with pytest.raises(OSError): publish(data, reserve_bytes=0)
    assert (data / 'read-model/current.json').read_bytes() == original


def test_acquisition_cache_is_keyed_by_closed_generation_and_kst_date(tmp_path, monkeypatch):
    from datetime import datetime as actual_datetime
    data, _ = acquired(tmp_path); api = PropertyAPI(data); calls = []
    class Clock:
        value = '2026-10-03T10:00:00+09:00'
        @classmethod
        def now(cls, tz): return actual_datetime.fromisoformat(cls.value).astimezone(tz)
    monkeypatch.setattr(vps_runtime, 'datetime', Clock)
    def audit(path, *, as_of):
        calls.append((str(path),as_of))
        return {'call':len(calls),'as_of':as_of}
    monkeypatch.setattr(vps_runtime, 'history_audit', audit)
    first = api.acquisition()
    Clock.value = '2026-10-03T22:00:00+09:00'
    write_json(data/'worker-status.json', {'state':'waiting'})
    cached = api.acquisition()
    assert len(calls) == 1 and cached['acquisition'] == first['acquisition']
    assert cached['worker']['state'] == 'waiting'
    assert cached['at'] != first['at']
    Clock.value = '2026-10-04T00:00:00+09:00'
    next_day = api.acquisition()
    assert len(calls) == 2 and next_day['acquisition']['as_of'] == '2026-10-04'
    publish(data, reserve_bytes=0)
    updated = api.acquisition()
    assert len(calls) == 3
    assert updated['read_model']['generation'] != first['read_model']['generation']


def test_acquisition_keeps_response_bound_to_manifest_when_another_reader_advances(tmp_path, monkeypatch):
    data, _ = acquired(tmp_path); api = PropertyAPI(data)
    monkeypatch.setattr(vps_runtime, 'history_audit', lambda *args, **kwargs:{'generation':'first'})
    class InterleavedLock:
        def __enter__(self): return self
        def __exit__(self, *args):
            # Another request may update the shared cache immediately on unlock.
            api.coverage = {'generation':'second'}
    api.lock = InterleavedLock()
    assert api.acquisition()['acquisition'] == {'generation':'first'}


def test_failed_coverage_refresh_keeps_previous_cache_without_marking_new_generation(tmp_path, monkeypatch):
    data, _ = acquired(tmp_path); api = PropertyAPI(data)
    monkeypatch.setattr(vps_runtime, 'history_audit', lambda *args, **kwargs:{'value':1})
    before = api.acquisition(); publish(data, reserve_bytes=0)
    def fail(*args, **kwargs): raise RealEstateError('history_failed')
    monkeypatch.setattr(vps_runtime, 'history_audit', fail)
    with pytest.raises(RealEstateError): api.acquisition()
    assert api.coverage == before['acquisition']
    assert api.coverage_generation == before['read_model']['generation']
