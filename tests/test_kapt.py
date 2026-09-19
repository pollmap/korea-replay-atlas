"""Synthetic K-apt responses; no credentials or personal account data."""
import json
from pathlib import Path

import pytest

from pipeline import kapt


KEY = 'synthetic_nonsecret_fixture_key'


def response(body, *, code='00', status=200):
    return kapt.HttpResult(status, kapt.canonical({'header': {'resultCode': code, 'resultMsg': 'synthetic'}, 'body': body}), 'application/json')


def item(code):
    return {'kaptCode': code, 'kaptName': '검증 단지 ' + code, 'bjdCode': '1111010100',
            'as1': '검증도', 'as2': '검증시', 'as3': '검증동', 'as4': None}


def page(number, size, total, codes):
    return {'pageNo': str(number), 'numOfRows': str(size), 'totalCount': str(total),
            'items': [item(code) for code in codes]}


def basic(code, **extra):
    return {'item': {**item(code), 'kaptdaCnt': '300', 'kaptDongCnt': 4, 'hoCnt': 300,
                     'kaptUsedate': '19991230', 'kaptTarea': '51234.50', **extra}}


def test_full_list_first_basic_samples_identity_and_resume(tmp_path):
    order = []

    def fetch(key, kind, params, *, max_bytes):
        assert key == KEY and max_bytes <= kapt.MAX_RESPONSE_BYTES
        order.append((kind, dict(params)))
        if kind == 'basic':
            return response(basic(params['kaptCode']))
        return response(page(params['pageNo'], 2, 3, ['A001', 'A002'] if params['pageNo'] == 1 else ['A003']))

    checkpoint = kapt.Checkpoint(tmp_path)
    result = kapt.collect(checkpoint, KEY, page_size=2, fetcher=fetch, interval=0)
    assert [entry[0] for entry in order] == ['list', 'basic', 'basic', 'list']
    payload = json.loads(Path(result['candidate_path']).read_bytes())
    assert payload['coverage']['list_complete'] is True
    assert payload['coverage']['list_rows'] == 3
    assert payload['coverage']['all_korean_apartments_complete'] is False
    assert payload['coverage']['basic_sample_rows'] == 2
    assert payload['identity']['same_as_molit_aptSeq'] is False
    assert all(row['coordinate'] is None and row['property_complex_id'] is None for row in payload['complexes'])
    assert payload['basic_samples'][0]['household_count'] == 300
    assert payload['basic_samples'][0]['gross_floor_area_m2'] == 51234.5
    assert payload['basic_samples'][0]['use_approval_date'] == '1999-12-30'
    assert result['sha256'] == kapt.digest(Path(result['candidate_path']).read_bytes())
    for source in payload['raw_sources']:
        assert kapt.digest(checkpoint.raw(source['raw_sha256'])) == source['raw_sha256']
    checkpoint.close()
    resumed = kapt.Checkpoint(tmp_path)
    assert kapt.collect(resumed, KEY, page_size=2, fetcher=lambda *a, **k: pytest.fail('cache miss'), interval=0) == result
    resumed.close()
    assert not any(KEY.encode() in f.read_bytes() for f in tmp_path.rglob('*') if f.is_file())


@pytest.mark.parametrize('code,error', [('30', 'upstream_auth'), ('31', 'upstream_auth'), ('22', 'upstream_quota'), ('99', 'upstream_result')])
def test_provider_errors_stop_without_retry(tmp_path, code, error):
    calls = []
    checkpoint = kapt.Checkpoint(tmp_path)
    def fetch(*args, **kwargs):
        calls.append(1)
        return response({}, code=code)
    for _ in range(2):
        with pytest.raises(kapt.KaptError, match=error) as caught:
            checkpoint.request('list', {'pageNo': 1, 'numOfRows': 10}, KEY, fetcher=fetch, interval=0)
        assert caught.value.provider_code == code
    assert len(calls) == 1
    assert checkpoint.summary()['requests_reserved'] == 1
    checkpoint.close()


def test_gateway_xml_errors_are_sanitized():
    raw = b'<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode><returnAuthMsg>private diagnostic must not escape</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>'
    with pytest.raises(kapt.KaptError, match='upstream_auth') as caught:
        kapt.parse_body(kapt.HttpResult(200, raw, 'text/xml'))
    assert caught.value.provider_code == '30'
    assert 'private' not in str(caught.value)


def test_request_and_byte_budget_survive_reopen_and_uncertain_request(tmp_path):
    checkpoint = kapt.Checkpoint(tmp_path, max_requests=2, max_bytes=1000)
    def interrupted(*a, **k):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        checkpoint.request('list', {'pageNo': 1, 'numOfRows': 1}, KEY, fetcher=interrupted, interval=0)
    assert checkpoint.summary()['budget_bytes_used_or_reserved'] == 1000
    checkpoint.close()
    checkpoint = kapt.Checkpoint(tmp_path, max_requests=2, max_bytes=1000)
    with pytest.raises(kapt.KaptError, match='uncertain_previous_request'):
        checkpoint.request('list', {'pageNo': 1, 'numOfRows': 1}, KEY, fetcher=interrupted, interval=0)
    with pytest.raises(kapt.KaptError, match='byte_budget_exhausted'):
        checkpoint.request('list', {'pageNo': 2, 'numOfRows': 1}, KEY, fetcher=interrupted, interval=0)
    assert checkpoint.summary()['requests_reserved'] == 1
    checkpoint.close()
    with pytest.raises(kapt.KaptError, match='checkpoint_contract_mismatch'):
        kapt.Checkpoint(tmp_path, max_requests=100, max_bytes=1000)


def test_request_budget_charges_failed_requests(tmp_path):
    checkpoint = kapt.Checkpoint(tmp_path, max_requests=1)
    def failed(*a, **k):
        raise kapt.KaptError('upstream_timeout', received_bytes=12)
    with pytest.raises(kapt.KaptError, match='upstream_timeout'):
        checkpoint.request('list', {'pageNo': 1, 'numOfRows': 1}, KEY, fetcher=failed, interval=0)
    with pytest.raises(kapt.KaptError, match='request_budget_exhausted'):
        checkpoint.request('basic', {'kaptCode': 'A001'}, KEY, fetcher=failed, interval=0)
    assert checkpoint.summary()['response_bytes_received'] == 12
    checkpoint.close()


def test_reflected_secret_is_not_saved(tmp_path):
    checkpoint = kapt.Checkpoint(tmp_path)
    def fetch(*a, **k):
        return kapt.HttpResult(403, ('{"error":"' + KEY + '"}').encode(), 'application/json')
    with pytest.raises(kapt.KaptError, match='secret_reflection'):
        checkpoint.request('basic', {'kaptCode': 'A001'}, KEY, fetcher=fetch, interval=0)
    checkpoint.close()
    assert not (tmp_path / 'raw').exists()
    assert not any(KEY.encode() in f.read_bytes() for f in tmp_path.rglob('*') if f.is_file())


def test_list_page_count_duplicate_and_basic_identity_are_fail_closed():
    with pytest.raises(kapt.KaptError, match='pagination_mismatch'):
        kapt.normalize_list(page(2, 2, 3, ['A001']), 1, 2)
    with pytest.raises(kapt.KaptError, match='incomplete_page'):
        kapt.normalize_list(page(1, 2, 3, ['A001']), 1, 2)
    with pytest.raises(kapt.KaptError, match='duplicate_kapt_code'):
        kapt.normalize_list(page(1, 2, 3, ['A001', 'A001']), 1, 2)
    with pytest.raises(kapt.KaptError, match='basic_identity_mismatch'):
        kapt.normalize_basic(basic('A002'), 'A001')


@pytest.mark.parametrize('changed,expected', [(True, 'upstream_total_changed'), (False, 'national_coverage_mismatch')])
def test_changed_total_or_cross_page_duplicate_prevents_candidate(tmp_path, changed, expected):
    checkpoint = kapt.Checkpoint(tmp_path)
    def fetch(key, kind, params, **kwargs):
        if params['pageNo'] == 1:
            return response(page(1, 2, 3, ['A001', 'A002']))
        return response(page(2, 2, 4 if changed else 3, ['A003', 'A004'] if changed else ['A001']))
    with pytest.raises(kapt.KaptError, match=expected):
        kapt.collect(checkpoint, KEY, page_size=2, basic_samples=0, fetcher=fetch, interval=0)
    assert not (tmp_path / 'candidates').exists()
    checkpoint.close()


def test_source_quality_does_not_invent_zero_dates_or_coordinates():
    row = kapt.normalize_basic(basic('A001', kaptdaCnt='', kaptDongCnt=-3, kaptUsedate='20001340', kaptTarea='nan',
                                     kaptTopFloor=3.5, kaptMarea=None), 'A001')
    assert row['household_count'] is None and row['building_count'] is None
    assert row['top_floor'] is None and row['gross_floor_area_m2'] is None
    assert row['maintenance_area_m2'] is None and row['use_approval_date'] is None
    assert row['source_use_approval_date'] == '20001340'
    assert row['coordinate'] is None
    assert set(row['quality_flags']) == {'invalid_building_count', 'invalid_gross_floor_area_m2', 'invalid_top_floor', 'invalid_use_approval_date'}


def test_modified_raw_checkpoint_is_rejected_without_new_call(tmp_path):
    checkpoint = kapt.Checkpoint(tmp_path)
    checkpoint.request('basic', {'kaptCode': 'A001'}, KEY, fetcher=lambda *a, **k: response(basic('A001')), interval=0)
    next((tmp_path / 'raw').iterdir()).write_bytes(b'{}')
    with pytest.raises(kapt.KaptError, match='raw_hash_mismatch'):
        checkpoint.request('basic', {'kaptCode': 'A001'}, KEY, fetcher=lambda *a, **k: pytest.fail('new request'), interval=0)
    assert checkpoint.summary()['requests_reserved'] == 1
    checkpoint.close()


def test_invalid_operation_and_unsafe_output_are_rejected_before_request(tmp_path):
    for kind, params in [('other', {}), ('list', {'pageNo': 0, 'numOfRows': 1}),
                         ('basic', {'kaptCode': '../escape'}), ('basic', {'kaptCode': 'A001', 'serviceKey': KEY})]:
        with pytest.raises(kapt.KaptError):
            kapt.params_for(kind, params)
    with pytest.raises(kapt.KaptError, match='unsafe_output_path'):
        kapt.immutable(tmp_path, '../escape', b'bad')
    kapt.immutable(tmp_path, 'safe.bin', b'original')
    with pytest.raises(kapt.KaptError, match='immutable_file_conflict'):
        kapt.immutable(tmp_path, 'safe.bin', b'replacement')
    assert (tmp_path / 'safe.bin').read_bytes() == b'original'


def test_transport_uses_only_official_https_and_does_not_follow_redirect(monkeypatch):
    connections = []
    class Response:
        status = 302
        fp = None
        def getheader(self, name, default=None):
            return {'Location': 'https://not-the-provider.invalid/', 'Content-Length': '0'}.get(name, default)
        def read1(self, size):
            return b''
    class Connection:
        sock = None
        def __init__(self, host, timeout):
            self.host, self.closed = host, False
            connections.append(self)
        def request(self, method, target, headers):
            from urllib.parse import parse_qs, urlsplit
            parsed = urlsplit(target)
            assert method == 'GET'
            assert parsed.path == kapt.ENDPOINTS['basic']
            assert parse_qs(parsed.query) == {'serviceKey': [KEY], 'kaptCode': ['A001']}
            assert headers['Accept-Encoding'] == 'identity'
        def getresponse(self):
            return Response()
        def close(self):
            self.closed = True
    monkeypatch.setattr(kapt.http.client, 'HTTPSConnection', Connection)
    result = kapt.fetch_official(KEY, 'basic', {'kaptCode': 'A001'}, max_bytes=1000)
    assert result.status == 302 and len(connections) == 1
    assert connections[0].host == 'apis.data.go.kr' and connections[0].closed


@pytest.mark.parametrize('declared_length', ['1000', None])
def test_transport_respects_body_limit_and_closes_connection(monkeypatch, declared_length):
    state = {'read': 0, 'closed': False}
    class Response:
        status = 200
        fp = None
        def getheader(self, name, default=None):
            return {'Content-Length': declared_length}.get(name, default)
        def read1(self, size):
            state['read'] += size
            return b'x' * size
    class Connection:
        sock = None
        def __init__(self, *args, **kwargs):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            state['closed'] = True
    monkeypatch.setattr(kapt.http.client, 'HTTPSConnection', Connection)
    with pytest.raises(kapt.KaptError, match='response_size_limit') as caught:
        kapt.fetch_official(KEY, 'basic', {'kaptCode': 'A001'}, max_bytes=100)
    assert state['read'] == (0 if declared_length else 100)
    assert caught.value.received_bytes == state['read']
    assert state['closed']


def test_raw_hash_path_from_checkpoint_is_validated(tmp_path):
    checkpoint = kapt.Checkpoint(tmp_path)
    with pytest.raises(kapt.KaptError, match='invalid_raw_hash'):
        checkpoint.raw('../../outside')
    checkpoint.close()
