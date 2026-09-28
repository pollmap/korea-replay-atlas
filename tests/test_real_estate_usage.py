"""Observe real archive transports without reporting credentials or assuming zero cost."""
import json

import pytest

from pipeline import property_automation as automation
from pipeline.real_estate import RealEstateError
from pipeline.real_estate_archive import D1Archive, backup, restore
from pipeline.real_estate_usage import usage_report
from test_real_estate_archive import LocalD1, source
from test_real_estate_archive_broker import CONFIG, TOKEN, ENDPOINT, wire  # noqa: F401
from test_real_estate_fetch import STAMP, xml


@pytest.fixture
def rest(monkeypatch):
    state = {'status': 200, 'body': None, 'fail_request': False}
    sent = []

    class Connection:
        def __init__(self, *args, **kwargs):
            pass

        def request(self, method, path, body, headers):
            sent.append(body)
            if state['fail_request']:
                raise OSError('secret-private-transport-detail')

        def getresponse(self):
            self.status = state['status']
            return self

        def read(self, limit):
            return state['body'][:limit]

        def close(self):
            pass

    monkeypatch.setattr('http.client.HTTPSConnection', Connection)
    return D1Archive(CONFIG, token=TOKEN), state, sent


def response(state, *metas, success=True):
    state['body'] = json.dumps({'success': success, 'result': [
        {'success': success, 'results': [], 'meta': meta} for meta in metas
    ]}).encode()


def test_rest_counts_every_result_and_keeps_valid_zero_and_aliases(rest):
    store, state, sent = rest
    response(state, {'rows_read': 2, 'rows_written': 0, 'size_after': 100},
             {'rows_read': 3, 'rows_written': 0, 'size_after': 100})
    assert store.query(store.control, 'SELECT secret-column WHERE token=?', ['private-param'])['meta']['rows_read'] == 2
    first_response_bytes = len(state['body'])
    response(state, {'rows_read': 0, 'rows_written': 4, 'size_after': 80})
    store.query(store.control, 'UPDATE secret-table SET value=?', ['private-param'])
    report = usage_report(store)
    assert report['query_calls'] == report['transport_attempts'] == report['outcomes']['success'] == 2
    assert report['sql_results_observed'] == 3 and report['in_flight'] == 0
    assert report['rows_read']['total'] == 5 and report['rows_written']['total'] == 4
    assert report['request_body_bytes_attempted'] == sum(map(len, sent))
    assert report['response_body_bytes_observed'] == first_response_bytes + len(state['body'])
    size = report['database_sizes']['control']
    assert size['observed_delta_bytes'] == -20  # Never clamp or attribute SQLite size to raw payload.
    assert size['maximum_observed_bytes'] == 100
    assert size['first_observation_precedes_mutations'] and size['last_observation_covers_mutations']
    serialized = json.dumps(report)
    assert all(secret not in serialized for secret in [TOKEN, ENDPOINT, CONFIG['account_id'],
               CONFIG['control_database'], 'private-param', 'secret-column', 'secret-table'])
    report['outcomes']['success'] = 999
    assert usage_report(store)['outcomes']['success'] == 2


@pytest.mark.parametrize('invalid', [None, True, -1, 1.25, '0', 2**53])
def test_missing_or_invalid_metadata_never_becomes_zero(rest, invalid):
    store, state, _ = rest
    response(state, {'rows_read': 0, 'rows_written': invalid, 'size_after': invalid, 'changes': 7})
    store.query(store.shards[0], 'SELECT 1')
    report = usage_report(store)
    assert report['rows_read']['total'] == 0 and report['rows_read']['complete']
    assert report['rows_written']['total'] is None and report['rows_written']['missing_results'] == 1
    assert report['database_sizes']['object0']['last_observed_bytes'] is None
    assert report['database_sizes']['object0']['missing_results'] == 1


def test_first_size_after_mutation_is_not_a_before_run_baseline(rest):
    store, state, _ = rest
    response(state, {'rows_read': 0, 'rows_written': 1, 'size_after': 123})
    store.query(store.control, 'INSERT INTO secret VALUES (?)', ['private-param'])
    size = usage_report(store)['database_sizes']['control']
    assert size['observed_delta_bytes'] is None
    assert not size['first_observation_after_read'] and not size['first_observation_precedes_mutations']
    assert size['last_observation_covers_mutations']


@pytest.mark.parametrize('status,body', [(200, b'not-json'), (200, b'\xff'),
                                        (307, b''), (500, b''),
                                        (200, b' ' * (16 * 1024**2 + 1))],
                         ids=['json', 'encoding', 'redirect', 'http', 'oversized'])
def test_failed_rest_write_is_counted_as_unconfirmed_without_retry(rest, status, body):
    store, state, sent = rest
    state.update(status=status, body=body)
    with pytest.raises(RealEstateError):
        store.query(store.control, 'UPDATE private-table SET value=?', ['private-param'])
    report = usage_report(store)
    assert len(sent) == 1 and report['writes_unconfirmed'] == 1
    assert report['rows_written']['total'] is None and report['rows_written']['unverified_queries'] == 1
    assert not report['database_sizes']['control']['last_observation_covers_mutations']


def test_error_with_partial_metadata_preserves_observation_without_confirmed_total(rest):
    store, state, _ = rest
    state['status'] = 400
    response(state, {'rows_read': 5, 'rows_written': 2, 'size_after': 100}, success=False)
    with pytest.raises(RealEstateError, match='archive_remote_http_400'):
        store.query(store.control, 'UPDATE private-table SET value=1')
    report = usage_report(store)
    assert report['rows_written']['observed'] == 2 and report['rows_written']['total'] is None
    assert report['outcomes']['response_error'] == 1
    assert not report['database_sizes']['control']['last_observation_covers_mutations']
    state['status'] = 200
    response(state, {'rows_read': 1, 'rows_written': 0, 'size_after': 100})
    store.query(store.control, 'SELECT 1')
    report = usage_report(store)
    assert report['database_sizes']['control']['last_observation_covers_mutations']
    assert report['writes_unconfirmed'] == 1 and report['rows_written']['total'] is None


def test_request_disconnect_reports_attempted_bytes_not_an_observed_response(rest):
    store, state, sent = rest
    state['fail_request'] = True
    with pytest.raises(RealEstateError, match='archive_remote_unavailable'):
        store.query(store.control, 'UPDATE private-table SET value=1')
    report = usage_report(store)
    assert report['transport_attempts'] == 1 and report['request_body_bytes_attempted'] == len(sent[0])
    assert report['responses_observed'] == 0 and report['response_body_bytes_observed'] == 0
    assert report['outcomes']['response_unresolved'] == report['writes_unconfirmed'] == 1


def test_select_prefix_cannot_hide_later_uncertain_mutation(rest):
    store, state, _ = rest
    state['fail_request'] = True
    with pytest.raises(RealEstateError):
        store.query(store.control, 'SELECT 1; UPDATE backup_heads SET bytes=1')
    report = usage_report(store)
    assert report['mutating_attempts'] == report['writes_unconfirmed'] == 1


def test_broker_allowlist_rejection_is_not_remote_work(wire):
    store, _, requests, _ = wire
    before = usage_report(store)
    with pytest.raises(RealEstateError, match='archive_broker_operation'):
        store.query(store.control, 'DELETE FROM secret-table')
    after = usage_report(store)
    assert after['query_calls'] == before['query_calls'] + 1
    assert after['outcomes']['local_rejected'] == 1
    assert after['transport_attempts'] == before['transport_attempts'] == len(requests)
    assert after['mutating_attempts'] == before['mutating_attempts']


@pytest.mark.parametrize('status,success', [(200, True), (200, False), (503, False)])
def test_broker_reports_counters_even_when_response_is_rejected(wire, status, success):
    store, _, _, state = wire
    before = usage_report(store)
    state.update(status=status, forced_body=json.dumps({'success': success, 'result': {
        'success': success, 'results': [], 'meta': {'rows_read': 7, 'rows_written': 0, 'size_after': 90}
    }}).encode())
    if success:
        assert store.head() is None
    else:
        with pytest.raises(RealEstateError):
            store.head()
    after = usage_report(store)
    assert after['rows_read']['observed'] == before['rows_read']['observed'] + 7
    assert after['rows_written']['observed'] == before['rows_written']['observed']
    assert after['database_sizes']['control']['last_observed_bytes'] == 90
    assert after['outcomes']['response_error'] == int(not success)


@pytest.mark.parametrize('status,body', [(307, b''), (200, b'[]'), (200, b'invalid'),
                                        (200, b'\xff')])
def test_broker_unusable_response_preserves_unknown_usage(wire, status, body):
    store, _, requests, state = wire
    state.update(status=status, forced_body=body)
    with pytest.raises(RealEstateError):
        store.head()
    after = usage_report(store)
    assert after['outcomes']['response_unresolved'] == 1
    assert after['transport_attempts'] == len(requests)
    assert after['rows_read']['total'] is None and after['rows_read']['unverified_queries'] == 1


def test_broker_committed_but_disconnected_write_does_not_claim_zero_cost(wire):
    store, local, requests, state = wire
    before = len(requests)
    state['disconnect_after_write'] = True
    with pytest.raises(RealEstateError, match='archive_remote_unavailable'):
        store.put(b'durable secret payload')
    report = usage_report(store)
    assert report['transport_attempts'] == len(requests) > before
    assert report['writes_unconfirmed'] == report['outcomes']['response_unresolved'] == 1
    assert report['rows_written']['total'] is None
    assert sum(db.execute('SELECT COUNT(*) FROM archive_chunks').fetchone()[0]
               for name, db in local.databases.items() if name != 'control') == 1
    assert local.head() is None


def test_cli_report_includes_entire_real_broker_roundtrip_and_cleanup(wire, tmp_path, monkeypatch, capsys):
    store, _, requests, _ = wire
    baseline = backup(source(tmp_path), store)
    actual = automation.run_automation
    monkeypatch.setattr(automation, 'credentials', lambda: (store, 'private-service-key'))

    def execute(root, client, key, **kwargs):
        result = actual(root, client, key, as_of=STAMP, months=2,
                        max_requests=1, reserve_bytes=0, transport=lambda *a, **kw: xml())
        # This read represents final release verification, and must be in the receipt.
        restore(tmp_path / 'restored', client)
        return result

    monkeypatch.setattr(automation, 'run_automation', execute)
    assert automation.main(['--execute', '--work-parent', str(tmp_path / 'scheduled')]) == 0
    output = capsys.readouterr().out
    receipt = json.loads(output)
    report = receipt['archive_io']
    assert receipt['status'] == 'collected' and not receipt['public_release']
    assert report['transport'] == 'broker' and report['transport_attempts'] == len(requests)
    assert report['query_calls'] == report['outcomes']['success'] and report['in_flight'] == 0
    assert report['rows_read']['total'] is None  # SQLite fixture deliberately has no D1 row counters.
    assert report['rows_read']['missing_results'] == report['sql_results_observed']
    assert all(value not in output for value in [TOKEN, ENDPOINT, CONFIG['account_id'], 'private-service-key'])
    assert baseline['public_release'] is False


@pytest.mark.parametrize('fail', [False, True])
def test_cli_pause_or_failure_reports_final_cleanup_work(rest, monkeypatch, tmp_path, capsys, fail):
    store, state, _ = rest
    response(state, {'rows_read': 1, 'rows_written': 0, 'size_after': 100})
    monkeypatch.setattr(automation, 'credentials', lambda: (store, 'private-key'))

    def execute(*args, **kwargs):
        try:
            store.query(store.control, 'SELECT private-content')
            if fail:
                raise RuntimeError('secret-exception-string')
            return {'status': 'storage_paused', 'public_release': False}
        finally:
            store.query(store.control, 'SELECT private-cleanup')

    monkeypatch.setattr(automation, 'run_automation', execute)
    assert automation.main(['--execute', '--work-parent', str(tmp_path)]) == int(fail)
    output = capsys.readouterr().out
    value = json.loads(output)
    assert value['archive_io']['rows_read']['total'] == 2
    assert value['archive_io']['query_calls'] == 2
    assert all(word not in output for word in ['secret-exception-string', 'private-content', 'private-cleanup'])


def test_cli_missing_credentials_and_noninstrumented_clients_are_unavailable(monkeypatch, tmp_path, capsys):
    def missing():
        raise RealEstateError('automation_credentials_missing')
    monkeypatch.setattr(automation, 'credentials', missing)
    assert automation.main(['--execute', '--work-parent', str(tmp_path)]) == 1
    value = json.loads(capsys.readouterr().out)
    assert value['archive_io']['available'] is False
    assert 'rows_read' not in value['archive_io']
    local = LocalD1()
    try:
        assert usage_report(local)['available'] is False
    finally:
        for db in local.databases.values():
            db.close()
