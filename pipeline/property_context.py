"""Keyless MOIS population/household sidecar. No property-release or map joins.

Three bounded read requests discover the published month and download the official
national/province and municipality CSVs. Raw bytes and normalized observations
remain separate; a new private candidate never overwrites an existing release.
"""
from __future__ import annotations

import argparse
import calendar
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
from html.parser import HTMLParser
import http.client
import io
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode

SOURCE_URL = 'https://jumin.mois.go.kr/statMonth.do'
DOWNLOAD_URL = 'https://jumin.mois.go.kr/downloadCsv.do'
POLICY_URL = 'https://mois.go.kr/frt/sub/a08/copyrightPolicy/screen.do'
MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 10_000
METRICS = ('총인구수', '세대수', '세대당 인구', '남자 인구수', '여자 인구수', '남여 비율')
COUNTS = ('registered_population', 'households', 'male_population', 'female_population')


class PropertyContextError(ValueError):
    """Fixed diagnostic code; no downloaded page or arbitrary error text is echoed."""


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_month(value):
    if not isinstance(value, str) or not re.fullmatch(r'20[0-9]{2}(?:0[1-9]|1[0-2])', value):
        raise PropertyContextError('invalid_reference_month')
    if value < '200801':
        raise PropertyContextError('unsupported_reference_month')
    return value


class _DownloadForm(HTMLParser):
    def __init__(self):
        super().__init__(); self.active = False; self.values = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'form':
            self.active = values.get('id') == 'formXlsDown'
        elif self.active and tag == 'input' and values.get('type') == 'hidden':
            name = values.get('name')
            if name in self.values:
                raise PropertyContextError('duplicate_download_form_field')
            self.values[name] = values.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.active = False


def published_month(raw):
    if len(raw) > MAX_BYTES:
        raise PropertyContextError('response_size_limit')
    try:
        parser = _DownloadForm(); parser.feed(raw.decode('utf-8'))
    except UnicodeError:
        raise PropertyContextError('invalid_page_encoding') from None
    fields = parser.values
    start = fields.get('searchYearStart', '') + fields.get('searchMonthStart', '')
    end = fields.get('searchYearEnd', '') + fields.get('searchMonthEnd', '')
    if start != end or fields.get('sltOrgLvl1') != 'A' or fields.get('sltUndefType') != '' or fields.get('category') != 'month':
        raise PropertyContextError('unexpected_default_population_scope')
    return valid_month(end)


def _integer(value):
    value = value.strip()
    if not re.fullmatch(r'(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)', value):
        raise PropertyContextError('invalid_integer_metric')
    result = int(value.replace(',', ''))
    if result > 1_000_000_000:
        raise PropertyContextError('metric_out_of_range')
    return result


def _decimal(value):
    value = value.strip()
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,2})?', value):
        raise PropertyContextError('invalid_ratio_metric')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise PropertyContextError('invalid_ratio_metric') from None
    if result > 1000:
        raise PropertyContextError('metric_out_of_range')
    return value  # Keep the provider's displayed precision; do not invent extra decimals.


def normalize_csv(raw, month):
    month = valid_month(month)
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise PropertyContextError('response_size_limit')
    text = None; encoding = None
    for candidate in ('utf-8-sig', 'cp949'):
        try:
            text = raw.decode(candidate); encoding = candidate; break
        except UnicodeError:
            pass
    if text is None or '\x00' in text:
        raise PropertyContextError('invalid_csv_encoding')
    prefix = f'{month[:4]}년{month[4:]}월_'
    reader = csv.reader(io.StringIO(text), strict=True)
    try:
        header = next(reader)
        if header != ['행정구역', *[prefix + metric for metric in METRICS]]:
            raise PropertyContextError('unexpected_columns_or_reference_month')
        records = []; seen = set()
        for line, cells in enumerate(reader, 2):
            if len(cells) != 7 or len(records) >= MAX_ROWS:
                raise PropertyContextError('row_shape_or_count_limit')
            identity = re.fullmatch(r'\s*(.+?)\s*\(([0-9]{10})\)\s*', cells[0])
            if not identity:
                raise PropertyContextError('missing_official_region_code')
            name = ' '.join(identity[1].split()); code = identity[2]
            if code in seen or len(name) > 200:
                raise PropertyContextError('duplicate_or_invalid_region')
            seen.add(code)
            population, households, male, female = [_integer(cells[index]) for index in (1, 2, 4, 5)]
            per_household, sex_ratio = _decimal(cells[3]), _decimal(cells[6])
            if male + female != population:
                raise PropertyContextError('population_sex_sum_mismatch')
            if (households == 0 and (population or Decimal(per_household) != 0)
                    or households and abs(Decimal(population) / households - Decimal(per_household)) > Decimal('0.011')):
                raise PropertyContextError('household_ratio_mismatch')
            if female and abs(Decimal(male) / female - Decimal(sex_ratio)) > Decimal('0.011'):
                raise PropertyContextError('sex_ratio_mismatch')
            if female == 0 and (male or Decimal(sex_ratio) != 0):
                raise PropertyContextError('undefined_sex_ratio')
            national = code == '1000000000' and name == '전국'
            if code == '1000000000' and not national:
                raise PropertyContextError('national_identity_mismatch')
            records.append({'region_code': code, 'region_name': name,
                'level': 'national' if national else 'province' if code.endswith('00000000') else 'municipality-or-district',
                'registered_population': population, 'households': households,
                'male_population': male, 'female_population': female,
                'persons_per_household_display': per_household, 'male_female_ratio_display': sex_ratio,
                'source_row': line})
    except (StopIteration, csv.Error):
        raise PropertyContextError('invalid_csv') from None
    if not records:
        raise PropertyContextError('empty_source')
    return {'encoding': encoding, 'rows': records, 'sha256': digest(raw), 'byte_length': len(raw)}


def build_sidecar(national_raw, regions_raw, month, retrieved_at):
    month = valid_month(month)
    try:
        stamp = datetime.strptime(retrieved_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        raise PropertyContextError('invalid_retrieved_at') from None
    if stamp.year * 100 + stamp.month < int(month):
        raise PropertyContextError('future_reference_month')
    national = normalize_csv(national_raw, month); regions = normalize_csv(regions_raw, month)
    totals = [row for row in national['rows'] if row['level'] == 'national']
    provinces = [row for row in national['rows'] if row['level'] == 'province']
    if len(totals) != 1 or len(provinces) + 1 != len(national['rows']) or not 1 <= len(provinces) <= 30:
        raise PropertyContextError('invalid_national_scope')
    region_lookup = {row['region_code']: row for row in regions['rows']}
    province_codes = {row['region_code'] for row in provinces}
    if {row['region_code'] for row in regions['rows'] if row['level'] == 'province'} != province_codes:
        raise PropertyContextError('province_coverage_mismatch')
    for province in provinces:
        match = region_lookup.get(province['region_code'])
        if not match or any(match[key] != province[key] for key in (*COUNTS, 'region_name')):
            raise PropertyContextError('cross_download_mismatch')
    for metric in COUNTS:
        if sum(row[metric] for row in provinces) != totals[0][metric]:
            raise PropertyContextError('national_province_sum_mismatch')
    for row in regions['rows']:
        if row['level'] == 'national':
            raise PropertyContextError('unexpected_national_row_in_regions')
        if row['level'] != 'province' and row['region_code'][:2] + '00000000' not in province_codes:
            raise PropertyContextError('unresolved_source_province')
    reference = date(int(month[:4]), int(month[4:]), calendar.monthrange(int(month[:4]), int(month[4:]))[1]).isoformat()
    records = [totals[0], *regions['rows']]
    payload = {'schema_version': 1, 'kind': 'property-regional-context', 'reference_month': month,
        'reference_date': reference, 'reference_timezone': 'Asia/Seoul', 'retrieved_at': retrieved_at, 'frequency': 'monthly',
        'source': {'id': 'mois-resident-registration-population', 'title': '행정안전부 주민등록 인구 및 세대현황',
                   'url': SOURCE_URL, 'download_url': DOWNLOAD_URL, 'access': 'public-download-no-key',
                   'copyright_policy_url': POLICY_URL, 'resource_license_mark': 'not-present-in-csv',
                   'attribution': '행정안전부 주민등록 인구통계'},
        'population_scope': {'residents': True, 'residence_unknown': True, 'overseas_korean_registered': True,
                             'foreign_nationals': False},
        'region_code_system': 'MOIS administrative-agency code, 10 digits, as supplied for reference month',
        'map_join': {'status': 'unresolved', 'automatic_legal_code_prefix_join': False,
                     'reason': '행정기관코드와 법정동·지도 경계의 기준월별 공식 대응표를 별도로 검증해야 합니다.'},
        'units': {'registered_population': 'person', 'households': 'registered-household',
                  'male_population': 'person', 'female_population': 'person'},
        'aggregation': {'all_rows_are_disjoint': False, 'national_check_uses': 'province-only',
                        'warning': '상위 시·하위 구가 함께 있으므로 모든 행을 더하지 않습니다. 세대수는 주택 공급량이 아닙니다.'},
        'coverage': {'scope': 'nationwide-source-municipality-export', 'national_rows': 1,
                     'province_rows': len(provinces), 'municipality_or_district_rows': len(records) - len(provinces) - 1,
                     'independent_municipality_completeness': 'not-verified',
                     'price_index': 'not-connected', 'housing_supply': 'not-connected'},
        'validation': {'population_sex_sum': True, 'cross_download_provinces': True,
                       'national_province_sum': True, 'source_code_uniqueness': True},
        'source_files': [{'scope': label, **{key: item[key] for key in ('sha256', 'byte_length', 'encoding')}}
                         for label, item in [('national-and-provinces', national), ('municipalities', regions)]],
        'rows': records}
    payload['release_id'] = 'context-' + digest(canonical(payload))[:16]
    return payload


def _fetch(path, fields=None, *, timeout=20):
    if path not in ('/statMonth.do', '/downloadCsv.do?searchYearMonth=month&xlsStats=1', '/downloadCsv.do?searchYearMonth=month&xlsStats=2'):
        raise PropertyContextError('unapproved_source_path')
    connection = http.client.HTTPSConnection('jumin.mois.go.kr', timeout=timeout)
    deadline = time.monotonic() + timeout
    headers = {'User-Agent': 'KoreaReplay-public-statistics/1.0', 'Accept-Encoding': 'identity'}
    if fields is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    try:
        connection.request('GET' if fields is None else 'POST', path, None if fields is None else urlencode(fields), headers)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PropertyContextError('upstream_timeout')
        if connection.sock:
            connection.sock.settimeout(remaining)
        response = connection.getresponse()
        if response.status != 200:
            raise PropertyContextError('upstream_http_' + str(response.status))
        if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise PropertyContextError('unexpected_content_encoding')
        chunks = []; size = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PropertyContextError('upstream_timeout')
            socket = connection.sock or getattr(getattr(response.fp, 'raw', None), '_sock', None)
            if socket:
                socket.settimeout(remaining)
            piece = response.read1(min(65536, MAX_BYTES + 1 - size))
            if not piece:
                break
            chunks.append(piece); size += len(piece)
            if size > MAX_BYTES:
                raise PropertyContextError('response_size_limit')
        return b''.join(chunks)
    except PropertyContextError:
        raise
    except TimeoutError:
        raise PropertyContextError('upstream_timeout') from None
    except (OSError, http.client.HTTPException):
        raise PropertyContextError('upstream_transport') from None
    finally:
        connection.close()


def collect(month=None, *, fetcher=_fetch):
    page = fetcher('/statMonth.do'); latest = published_month(page)
    month = valid_month(month) if month else latest
    if month > latest:
        raise PropertyContextError('reference_month_not_yet_published')
    fields = {'sltOrgType': '1', 'sltOrgLvl1': 'A', 'sltOrgLvl2': '', 'gender': 'gender',
              'genderPer': 'genderPer', 'generation': 'generation', 'sltUndefType': '',
              'searchYearStart': month[:4], 'searchMonthStart': month[4:],
              'searchYearEnd': month[:4], 'searchMonthEnd': month[4:],
              'sltOrderType': '1', 'sltOrderValue': 'ASC', 'category': 'month'}
    national = fetcher('/downloadCsv.do?searchYearMonth=month&xlsStats=1', {**fields, 'state': '1'})
    regions = fetcher('/downloadCsv.do?searchYearMonth=month&xlsStats=2', {**fields, 'state': '2'})
    retrieved_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return build_sidecar(national, regions, month, retrieved_at), {'discovery.html': page, 'national.csv': national, 'regions.csv': regions}


def write_candidate(payload, raw_files, root, *, project_root=None):
    if (not isinstance(payload, dict)
            or not re.fullmatch(r'context-[0-9a-f]{16}', str(payload.get('release_id', '')))):
        raise PropertyContextError('invalid_candidate_release_id')
    if (not isinstance(raw_files, dict)
            or set(raw_files) != {'discovery.html', 'national.csv', 'regions.csv'}
            or any(not isinstance(value, bytes) or len(value) > MAX_BYTES for value in raw_files.values())):
        raise PropertyContextError('invalid_raw_artifact')
    # Rebuild from the preserved bytes before creating any directory. This checks
    # provenance, normalized observations and the self-derived release ID at once.
    expected = build_sidecar(raw_files['national.csv'], raw_files['regions.csv'],
                             payload.get('reference_month'), payload.get('retrieved_at'))
    if canonical(expected) != canonical(payload):
        raise PropertyContextError('candidate_payload_mismatch')
    if published_month(raw_files['discovery.html']) < payload['reference_month']:
        raise PropertyContextError('reference_month_not_yet_published')
    project = Path(project_root or Path.cwd()).resolve()
    root = Path(root).absolute()
    if not root.is_relative_to(project / '.local'):
        raise PropertyContextError('candidate_must_be_in_project_local')
    for parent in [root, *root.parents]:
        if parent.is_symlink() or parent.is_junction():
            raise PropertyContextError('linked_candidate_path')
        if parent == project:
            break
    if not root.resolve().is_relative_to(project / '.local'):
        raise PropertyContextError('candidate_path_escape')
    output = root / payload['release_id']
    output.mkdir(parents=True, exist_ok=False)
    raw_root = output / 'raw'; raw_root.mkdir()
    for name, value in raw_files.items():
        with (raw_root / name).open('xb') as handle:
            handle.write(value)
    body = canonical(payload)
    with (output / 'context.json').open('xb') as handle:
        handle.write(body)
    receipt = {'schema_version': 1, 'complete': True, 'release_id': payload['release_id'],
               'reference_month': payload['reference_month'], 'context_sha256': digest(body),
               'context_bytes': len(body), 'row_count': len(payload['rows']), 'remote_published': False,
               'raw_files': [{'path': 'raw/' + name, 'sha256': digest(raw), 'byte_length': len(raw)} for name, raw in raw_files.items()]}
    with (output / 'receipt.json').open('xb') as handle:
        handle.write(canonical(receipt))
    return output, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True); parser.add_argument('--month')
    parser.add_argument('--execute', action='store_true', help='Make exactly three official read requests and write a new private candidate')
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({'mode': 'plan', 'source': SOURCE_URL, 'key_required': False,
                          'max_requests': 3, 'max_response_bytes': MAX_BYTES, 'remote_publish': False}))
        return
    try:
        payload, raw = collect(args.month); output, receipt = write_candidate(payload, raw, args.output)
        print(json.dumps({'candidate': str(output), **receipt}, ensure_ascii=False))
    except (PropertyContextError, FileExistsError) as error:
        parser.exit(1, ('candidate_already_exists' if isinstance(error, FileExistsError) else str(error)) + '\n')


if __name__ == '__main__':
    main()
