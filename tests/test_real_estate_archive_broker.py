"""Exercise Python -> operation wire -> deployed fixed SQL -> real SQLite."""
import hashlib
import json
import os
import sqlite3

import pytest

from pipeline.real_estate import RealEstateError
from pipeline.real_estate_archive import BrokerArchive, D1Archive, backup, restore
from pipeline import real_estate_archive_broker as protocol
from pipeline.real_estate_remote import run_remote
from pipeline.real_estate_run_guard import CollectionGuard, DAY
from pipeline.property_automation import run_automation
from test_real_estate_archive import LocalD1, source
from test_real_estate_fetch import xml, STAMP

ENDPOINT = 'https://fixture.example.invalid/v1/query'
TOKEN = 'f' * 64
CONFIG = {'account_id': 'a' * 32, 'control_database': '0' * 36,
          'object_databases': [str(i) * 36 for i in range(1, 5)]}


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr(protocol, 'ENDPOINT_SHA256', hashlib.sha256(ENDPOINT.encode()).hexdigest())
    local = LocalD1()
    requests = []
    state = {'status': 200, 'error': None, 'disconnect_after_write': False, 'forced_body': None}
    class Connection:
        def __init__(self, host, timeout):
            assert host == 'fixture.example.invalid' and timeout == 60
        def request(self, method, path, body, headers):
            assert method == 'POST' and path == '/v1/query'
            assert headers['Authorization'] == 'Bearer ' + TOKEN
            assert len(body) <= protocol.REQUEST_LIMIT
            value = json.loads(body)
            assert set(value) == {'version', 'operation', 'database', 'params'}
            entry = protocol.CATALOG['operations'][value['operation']]
            database = value['database']
            assert database in ['control', 'object0', 'object1', 'object2', 'object3']
            assert entry['database'] == ('control' if database == 'control' else 'object')
            assert len(value['params']) == entry['parameters']
            requests.append(value)
            if state['error']:
                self.raw = json.dumps({'success': False, 'error': state['error']}).encode()
            else:
                try:
                    result = local.query('control' if database == 'control' else database[-1], entry['sql'], value['params'])
                    self.raw = json.dumps({'success': True, 'result': {'success': True, **result}}).encode()
                except sqlite3.IntegrityError as error:
                    self.raw = json.dumps({'success': False, 'error': str(error)}).encode()
                    self.status = 503
            if state['disconnect_after_write'] and entry['sql'].startswith('INSERT OR IGNORE INTO archive_chunks'):
                raise OSError('simulated network failure after server commit')
        def getresponse(self):
            self.status = getattr(self, 'status', state['status'])
            return self
        def read(self, size):
            return (state['forced_body'] if state['forced_body'] is not None else self.raw)[:size]
        def close(self):
            pass
    monkeypatch.setattr('http.client.HTTPSConnection', Connection)
    store = BrokerArchive(CONFIG, token=TOKEN, endpoint=ENDPOINT)
    store.initialize()
    yield store, local, requests, state
    for db in local.databases.values():
        db.close()


def test_fixed_catalog_entire_remote_and_automation_roundtrip(wire, tmp_path):
    store, local, requests, _ = wire
    original = source(tmp_path)
    baseline = backup(original, store)
    assert baseline['public_release'] is False
    first = run_remote(tmp_path / 'remote', store, 'fixture-key', as_of=STAMP,
                       months=2, max_requests=1, reserve_bytes=0, transport=lambda *a, **kw: xml())
    assert first['collection']['requests'] == 1
    second = run_automation(tmp_path / 'scheduled', store, 'fixture-key', as_of='2026-10-01T00:00:00Z',
                            months=2, max_requests=1, reserve_bytes=0, transport=lambda *a, **kw: xml())
    assert second['requests'] == 1 and second['status'] == 'collected'
    restored = restore(tmp_path / 'restored', store)
    assert restored['audit']['calls'] == 3
    with sqlite3.connect(tmp_path / 'restored/checkpoint.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == 3
    assert CollectionGuard(store).status()['owner'][0]['occupied'] == 0
    assert all('sql' not in request and CONFIG['control_database'] not in json.dumps(request) for request in requests)
    assert store.head() == local.head()


def test_more_than_four_chunks_roundtrip_and_rest_compatibility(wire):
    store, local, requests, _ = wire
    raw = os.urandom(700_000)
    descriptor = store.put(raw)
    assert store.get(descriptor['sha256'], descriptor['bytes']) == raw
    assert local.get(descriptor['sha256'], descriptor['bytes']) == raw
    operations = [protocol.CATALOG['operations'][request['operation']] for request in requests]
    assert max(row['parameters'] for row in operations if row['sql'].startswith('INSERT OR IGNORE INTO archive_chunks')) == 6
    assert sum('LIMIT 4' in row['sql'] for row in operations) > 4
    assert (D1Archive.WRITE_BATCH, D1Archive.READ_BATCH) == (8, 30)


def test_head_cas_and_pending_reservation_cannot_be_unlocked(wire):
    store, _, _, _ = wire
    old = store.put(b'old'); store.promote(old, None)
    guard = CollectionGuard(store); guard.initialize(); lease = guard.acquire(old)
    guard.seed_budget(lease, old, [])
    reservation = guard.reserve(lease, 'sale', 'sale/11110/202609', 1)
    with pytest.raises(RealEstateError, match='recovery_required'):
        guard.release(lease, old)
    replacement = store.put(b'new')
    with pytest.raises(RealEstateError, match='head_changed'):
        store.promote(replacement, old)
    guard.finish(lease, reservation['id'], raw=b'<raw/>')
    store.promote(replacement, old, lease=lease)
    with pytest.raises(RealEstateError, match='head_changed'):
        store.promote(old, old, lease=lease)
    guard.release(lease, replacement)
    assert guard.status()['owner'][0]['occupied'] == 0


def test_quota_enforcement_and_mapped_trigger_error(wire):
    store, _, _, _ = wire
    head = store.put(b'base'); store.promote(head, None)
    guard = CollectionGuard(store); guard.initialize(); lease = guard.acquire(head)
    day = guard.query(f'SELECT {DAY} AS day')['results'][0]['day']
    guard.seed_budget(lease, head, [{'day': day, 'trade': 'sale', 'used': 7999}])
    guard.reserve(lease, 'sale', 'sale/11110/202609', 1)
    with pytest.raises(RealEstateError, match='collection_daily_budget'):
        guard.reserve(lease, 'sale', 'sale/11110/202609', 2)
    assert guard.status()['today'][0]['used'] == 8000


def test_uncertain_committed_chunk_is_never_retried(wire):
    store, local, requests, state = wire
    before = len(requests); state['disconnect_after_write'] = True
    with pytest.raises(RealEstateError, match='archive_remote_unavailable'):
        store.put(b'durable but response lost')
    writes = [r for r in requests[before:] if protocol.CATALOG['operations'][r['operation']]['sql'].startswith('INSERT')]
    assert len(writes) == 1
    assert sum(db.execute('SELECT COUNT(*) FROM archive_chunks').fetchone()[0] for name, db in local.databases.items() if name != 'control') == 1
    assert local.head() is None


@pytest.mark.parametrize('sql', ['DROP TABLE backup_heads', 'SELECT * FROM backup_heads',
                                'UPDATE collection_owner SET owner=NULL', "SELECT digest,bytes FROM backup_heads WHERE name='collector'; DELETE FROM backup_heads"])
def test_unknown_sql_rejected_before_network(wire, sql):
    store, _, requests, _ = wire; before = len(requests)
    with pytest.raises(RealEstateError, match='archive_broker_operation'):
        store.query(store.control, sql)
    assert len(requests) == before


@pytest.mark.parametrize('suffix', ['?x=1', '#x', '/', ':443', ' '])
def test_endpoint_is_exact_and_not_merely_same_host(wire, suffix):
    with pytest.raises(RealEstateError, match='archive_broker_endpoint'):
        BrokerArchive(CONFIG, token=TOKEN, endpoint=ENDPOINT + suffix)


def test_missing_token_and_wrong_database_fail_before_network(wire, monkeypatch):
    store, _, requests, _ = wire; before = len(requests)
    monkeypatch.delenv('PROPERTY_ARCHIVE_BROKER_TOKEN', raising=False)
    with pytest.raises(RealEstateError, match='archive_broker_configuration'):
        BrokerArchive(CONFIG, endpoint=ENDPOINT)
    with pytest.raises(RealEstateError, match='archive_broker_database'):
        store.query('arbitrary-database', "SELECT digest,bytes FROM backup_heads WHERE name='collector'")
    with pytest.raises(RealEstateError, match='archive_broker_operation'):
        store.query(store.shards[0], "SELECT digest,bytes FROM backup_heads WHERE name='collector'")
    assert len(requests) == before


@pytest.mark.parametrize('body', [b'[]', b'{"success":true,"result":[]}', b'not-json'])
def test_malformed_response_is_sanitized(wire, body):
    store, _, _, state = wire; state['forced_body'] = body
    with pytest.raises(RealEstateError, match='archive_remote_'):
        store.head()


def test_redirect_and_response_cap_never_retry(wire):
    store, _, requests, state = wire; before = len(requests)
    state['status'] = 307
    with pytest.raises(RealEstateError, match='archive_broker_redirect'):
        store.head()
    assert len(requests) == before + 1
    state['status'] = 200; state['forced_body'] = b' ' * (protocol.RESPONSE_LIMIT + 1)
    with pytest.raises(RealEstateError, match='archive_remote_response_limit'):
        store.head()


def test_daily_write_limit_is_preserved_without_raw_error(wire):
    store, _, _, state = wire
    state.update(status=503, error='archive_daily_write_limit')
    with pytest.raises(RealEstateError, match='^archive_daily_write_limit$'):
        store.head()
