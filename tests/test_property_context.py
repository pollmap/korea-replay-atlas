"""Synthetic observations only; no secret or downloaded user data in fixtures."""
from copy import deepcopy
import csv
import io
import json
from pathlib import Path

import pytest

from pipeline import property_context as context


MONTH = '202608'
STAMP = '2026-09-20T03:00:00Z'


def discovery(month=MONTH):
    fields = {'searchYearStart': month[:4], 'searchMonthStart': month[4:],
              'searchYearEnd': month[:4], 'searchMonthEnd': month[4:],
              'sltOrgLvl1': 'A', 'sltUndefType': '', 'category': 'month'}
    return ('<form id="other"><input type="hidden" name="category" value="other"></form>'
            '<form id="formXlsDown">'
            + ''.join(f'<input type="hidden" name="{key}" value="{value}">' for key, value in fields.items())
            + '</form>').encode('utf-8')


def row(name, code, population):
    # Deliberately synthetic even counts keep displayed ratios exact.
    return [f'{name}  ({code})', f'{population:,}', f'{population // 2:,}',
            '2.00', f'{population // 2:,}', f'{population // 2:,}', '1.00']


def csv_bytes(rows, month=MONTH, encoding='cp949'):
    out = io.StringIO(newline='')
    writer = csv.writer(out, quoting=csv.QUOTE_ALL)
    writer.writerow(['행정구역', *[f'{month[:4]}년{month[4:]}월_' + metric for metric in context.METRICS]])
    writer.writerows(rows)
    return out.getvalue().encode(encoding)


def source_rows():
    provinces = [row('서울특별시', '1100000000', 100), row('전남광주통합특별시', '1200000000', 80)]
    # City 60 and its child district 30 overlap. They must never be summed into
    # the national observation or silently deduplicated by names/code prefixes.
    municipalities = [row('서울특별시 검증구', '1111000000', 40),
                      row('전남광주통합특별시 검증시', '1211000000', 60),
                      row('전남광주통합특별시 검증시 가상구', '1211100000', 30)]
    return [row('전국', '1000000000', 180), *provinces], [*provinces, *municipalities]


def candidate():
    national, regions = source_rows()
    raw = {'discovery.html': discovery(), 'national.csv': csv_bytes(national), 'regions.csv': csv_bytes(regions)}
    return context.build_sidecar(raw['national.csv'], raw['regions.csv'], MONTH, STAMP), raw


def test_exact_source_month_codes_and_overlapping_hierarchy_survive():
    payload, raw = candidate()
    assert payload['reference_date'] == '2026-08-31'
    assert payload['reference_timezone'] == 'Asia/Seoul'
    assert payload['coverage']['province_rows'] == 2
    assert payload['coverage']['municipality_or_district_rows'] == 3
    assert len(payload['rows']) == 6
    assert payload['rows'][0]['registered_population'] == 180
    assert payload['rows'][2]['region_code'] == '1200000000'
    assert payload['rows'][2]['region_name'] == '전남광주통합특별시'
    assert {r['region_code'] for r in payload['rows']} >= {'1211000000', '1211100000'}
    assert payload['aggregation']['all_rows_are_disjoint'] is False
    assert payload['map_join']['automatic_legal_code_prefix_join'] is False
    assert payload['population_scope']['foreign_nationals'] is False
    assert payload['coverage']['price_index'] == payload['coverage']['housing_supply'] == 'not-connected'
    assert payload['source_files'][0]['encoding'] == 'cp949'
    assert payload['source_files'][1]['sha256'] == context.digest(raw['regions.csv'])
    assert candidate()[0] == payload  # Deterministic for the same bytes and timestamp.


def test_utf8_bom_and_cp949_both_preserve_provider_values():
    national, _ = source_rows()
    utf = context.normalize_csv(csv_bytes(national, encoding='utf-8-sig'), MONTH)
    cp = context.normalize_csv(csv_bytes(national), MONTH)
    assert utf['rows'] == cp['rows']
    assert utf['encoding'] == 'utf-8-sig' and cp['encoding'] == 'cp949'
    assert cp['rows'][0]['persons_per_household_display'] == '2.00'


@pytest.mark.parametrize('change,error', [
    (lambda rows: rows.append(rows[0]), 'duplicate_or_invalid_region'),
    (lambda rows: rows[0].__setitem__(0, '서울특별시 (0)'), 'missing_official_region_code'),
    (lambda rows: rows[0].__setitem__(4, '99'), 'population_sex_sum_mismatch'),
    (lambda rows: rows[0].__setitem__(3, '8.88'), 'household_ratio_mismatch'),
    (lambda rows: rows[0].__setitem__(6, '8.88'), 'sex_ratio_mismatch'),
    (lambda rows: rows[0].__setitem__(1, '1,00'), 'invalid_integer_metric'),
])
def test_invalid_metrics_or_unofficial_codes_rejected(change, error):
    _, rows = source_rows()
    change(rows)
    with pytest.raises(context.PropertyContextError, match=error):
        context.normalize_csv(csv_bytes(rows), MONTH)


def test_reference_month_is_verified_from_header_and_form():
    national, _ = source_rows()
    with pytest.raises(context.PropertyContextError, match='unexpected_columns_or_reference_month'):
        context.normalize_csv(csv_bytes(national, '202607'), MONTH)
    assert context.published_month(discovery()) == MONTH
    with pytest.raises(context.PropertyContextError, match='unexpected_default_population_scope'):
        context.published_month(discovery().replace(b'name="sltOrgLvl1" value="A"', b'name="sltOrgLvl1" value="B"'))
    with pytest.raises(context.PropertyContextError, match='duplicate_download_form_field'):
        context.published_month(discovery().replace(b'</form>', b'<input type="hidden" name="category" value="month"></form>'))


@pytest.mark.parametrize('mode,error', [
    ('missing_province', 'province_coverage_mismatch'),
    ('changed_province', 'cross_download_mismatch'),
    ('bad_national', 'national_province_sum_mismatch'),
    ('orphan_municipality', 'unresolved_source_province'),
])
def test_two_downloads_are_reconciled_before_success(mode, error):
    national, regions = source_rows()
    if mode == 'missing_province':
        regions.pop(1)
    elif mode == 'changed_province':
        regions[0] = row('서울특별시', '1100000000', 102)
    elif mode == 'bad_national':
        national[0] = row('전국', '1000000000', 182)
    else:
        regions.append(row('미확인시', '9911000000', 10))
    with pytest.raises(context.PropertyContextError, match=error):
        context.build_sidecar(csv_bytes(national), csv_bytes(regions), MONTH, STAMP)


def test_three_bounded_read_requests_no_credentials_and_future_month_stops():
    _, raw = candidate()
    calls = []
    def fetcher(path, fields=None):
        calls.append((path, fields))
        return raw['discovery.html' if path == '/statMonth.do' else 'national.csv' if path.endswith('=1') else 'regions.csv']
    payload, returned_raw = context.collect(fetcher=fetcher)
    assert len(calls) == 3 and calls[0] == ('/statMonth.do', None)
    assert calls[1][1]['sltOrgLvl1'] == 'A' and calls[2][1]['state'] == '2'
    assert calls[1][1]['sltUndefType'] == ''
    assert not any('key' in key.lower() or 'auth' in key.lower() for key in calls[1][1])
    assert payload['reference_month'] == MONTH and returned_raw == raw
    calls.clear()
    with pytest.raises(context.PropertyContextError, match='reference_month_not_yet_published'):
        context.collect('202609', fetcher=fetcher)
    assert len(calls) == 1


def test_transport_does_not_follow_redirect_or_read_an_unapproved_source(monkeypatch):
    calls = []
    class Response:
        status = 302
    class Connection:
        sock = None
        def __init__(self, host, timeout):
            assert host == 'jumin.mois.go.kr'
        def request(self, method, path, body, headers):
            calls.append((method, path, body, headers))
        def getresponse(self):
            return Response()
        def close(self):
            calls.append('closed')
    monkeypatch.setattr(context.http.client, 'HTTPSConnection', Connection)
    with pytest.raises(context.PropertyContextError, match='unapproved_source_path'):
        context._fetch('https://unapproved.invalid/')
    assert calls == []
    with pytest.raises(context.PropertyContextError, match='upstream_http_302'):
        context._fetch('/statMonth.do')
    assert calls[-1] == 'closed' and len(calls) == 2
    assert set(calls[0][3]) == {'User-Agent', 'Accept-Encoding'}


def test_oversized_or_invalid_encoding_is_not_parsed(monkeypatch):
    with pytest.raises(context.PropertyContextError, match='invalid_csv_encoding'):
        context.normalize_csv(b'\x00bad', MONTH)
    monkeypatch.setattr(context, 'MAX_BYTES', 8)
    with pytest.raises(context.PropertyContextError, match='response_size_limit'):
        context.normalize_csv(b'x' * 9, MONTH)


def test_stream_byte_cap_and_timeout_close_connection(monkeypatch):
    state = {'closed': 0, 'read': 0}
    class Response:
        status = 200
        fp = None
        def getheader(self, name, default):
            return default
        def read1(self, size):
            state['read'] += 1
            return b'x' * size
    class Connection:
        sock = None
        def __init__(self, host, timeout):
            pass
        def request(self, *args):
            pass
        def getresponse(self):
            return Response()
        def close(self):
            state['closed'] += 1
    monkeypatch.setattr(context.http.client, 'HTTPSConnection', Connection)
    monkeypatch.setattr(context, 'MAX_BYTES', 8)
    with pytest.raises(context.PropertyContextError, match='response_size_limit'):
        context._fetch('/statMonth.do')
    assert state == {'closed': 1, 'read': 1}
    ticks = iter([100, 121])
    monkeypatch.setattr(context.time, 'monotonic', lambda: next(ticks))
    with pytest.raises(context.PropertyContextError, match='upstream_timeout'):
        context._fetch('/statMonth.do', timeout=20)
    assert state == {'closed': 2, 'read': 1}


def test_private_candidate_preserves_raw_hashes_and_never_overwrites(tmp_path):
    payload, raw = candidate()
    root = tmp_path / '.local' / 'property-context'
    output, receipt = context.write_candidate(payload, raw, root, project_root=tmp_path)
    assert receipt['complete'] and not receipt['remote_published']
    assert receipt['context_sha256'] == context.digest((output / 'context.json').read_bytes())
    assert json.loads((output / 'context.json').read_text(encoding='utf-8')) == payload
    before = {p.name: p.read_bytes() for p in (output / 'raw').iterdir()}
    assert before == raw
    with pytest.raises(FileExistsError):
        context.write_candidate(payload, raw, root, project_root=tmp_path)
    assert {p.name: p.read_bytes() for p in (output / 'raw').iterdir()} == before


@pytest.mark.parametrize('mode,error', [
    ('release_path', 'invalid_candidate_release_id'),
    ('changed_value', 'candidate_payload_mismatch'),
    ('raw_changed', 'population_sex_sum_mismatch'),
    ('raw_extra', 'invalid_raw_artifact'),
    ('outside_root', 'candidate_must_be_in_project_local'),
    ('parent_escape', 'candidate_path_escape'),
])
def test_bad_candidate_or_path_fails_before_creating_files(tmp_path, mode, error):
    payload, raw = candidate()
    payload = deepcopy(payload)
    root = tmp_path / '.local' / 'property-context'
    if mode == 'release_path':
        payload['release_id'] = '../../outside'
    elif mode == 'changed_value':
        payload['rows'][0]['registered_population'] += 1
    elif mode == 'raw_changed':
        raw['national.csv'] = raw['national.csv'].replace(b'"180"', b'"182"')
    elif mode == 'raw_extra':
        raw['../unexpected.txt'] = b'no'
    elif mode == 'outside_root':
        root = tmp_path / 'public'
    elif mode == 'parent_escape':
        root = tmp_path / '.local' / '..' / 'public'
    with pytest.raises(context.PropertyContextError, match=error):
        context.write_candidate(payload, raw, root, project_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_junction_ancestor_is_rejected_without_writing(monkeypatch, tmp_path):
    payload, raw = candidate()
    root = tmp_path / '.local' / 'junction' / 'context'
    original = Path.is_junction
    monkeypatch.setattr(Path, 'is_junction', lambda path: path.name == 'junction' or original(path))
    with pytest.raises(context.PropertyContextError, match='linked_candidate_path'):
        context.write_candidate(payload, raw, root, project_root=tmp_path)
    assert list(tmp_path.iterdir()) == []
