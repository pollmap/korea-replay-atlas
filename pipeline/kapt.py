"""Bounded, resumable K-apt observations; never joins K-apt IDs to MOLIT aptSeq.

Only the official HTTPS list V4 and basic-information V5 operations are allowed.
The private checkpoint ledger counts every reserved request, including failures.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import http.client
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

from .real_estate import RealEstateError, _reject_links
from .real_estate_fetch import assert_no_secret, read_key

MAX_REQUESTS = 100
MAX_BYTES = 64 * 1024**2
MAX_RESPONSE_BYTES = 2 * 1024**2
ENDPOINTS = {
    'list': '/1613000/AptListService4/getTotalAptList4',
    'basic': '/1613000/AptBasisInfoServiceV5/getAphusBassInfoV5',
}
SOURCES = {
    'list': 'https://www.data.go.kr/data/15057332/openapi.do',
    'basic': 'https://www.data.go.kr/data/15058453/openapi.do',
}


class KaptError(ValueError):
    """Only stable, secret-free machine codes may appear in public error text."""
    def __init__(self, code, *, provider_code=None, received_bytes=0):
        self.code = code
        self.provider_code = provider_code if isinstance(provider_code, str) and re.fullmatch(r'[A-Z0-9_-]{1,40}', provider_code) else None
        self.received_bytes = received_bytes
        super().__init__(code)


def instant():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def integer(value, name, maximum=1_000_000):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)):
        raise KaptError('invalid_' + name)
    result = int(value)
    if result > maximum:
        raise KaptError('invalid_' + name)
    return result


def kapt_code(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
        raise KaptError('invalid_kapt_code')
    return value


def params_for(kind, params):
    if kind == 'list' and set(params) == {'pageNo', 'numOfRows'}:
        page = integer(params['pageNo'], 'page_number', 100_000)
        size = integer(params['numOfRows'], 'page_size', 1000)
        if page < 1 or size < 1:
            raise KaptError('invalid_pagination')
        return {'pageNo': page, 'numOfRows': size}
    if kind == 'basic' and set(params) == {'kaptCode'}:
        return {'kaptCode': kapt_code(params['kaptCode'])}
    raise KaptError('invalid_operation')


@dataclass(frozen=True)
class HttpResult:
    status: int
    raw: bytes
    content_type: str


def fetch_official(key, kind, params, *, max_bytes, timeout=20):
    """One HTTPS GET, no redirects/retry/proxy and no URL in thrown errors."""
    params = params_for(kind, params)
    received = 0
    connection = http.client.HTTPSConnection('apis.data.go.kr', timeout=timeout)
    deadline = time.monotonic() + timeout
    try:
        query = urlencode({'serviceKey': key, **params})
        connection.request('GET', ENDPOINTS[kind] + '?' + query, headers={
            'Accept': 'application/json', 'Accept-Encoding': 'identity',
            'User-Agent': 'KoreaReplay-KaptObservation/1.0',
        })
        response = connection.getresponse()
        if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise KaptError('unexpected_content_encoding')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > max_bytes):
            raise KaptError('response_size_limit')
        chunks = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KaptError('upstream_timeout')
            if received == max_bytes:
                if length is not None and int(length) == received:
                    break
                raise KaptError('response_size_limit')
            socket = connection.sock or getattr(getattr(response.fp, 'raw', None), '_sock', None)
            if socket:
                socket.settimeout(remaining)
            chunk = response.read1(min(64 * 1024, max_bytes - received))
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
        raw = b''.join(chunks)
        try:
            assert_no_secret(raw, key)
        except RealEstateError:
            raise KaptError('secret_reflection') from None
        return HttpResult(response.status, raw, response.getheader('Content-Type', '')[:200])
    except KaptError as exc:
        exc.received_bytes = received
        raise
    except TimeoutError:
        raise KaptError('upstream_timeout', received_bytes=received) from None
    except (OSError, http.client.HTTPException, ValueError):
        raise KaptError('upstream_network', received_bytes=received) from None
    finally:
        connection.close()


def parse_body(result):
    """Parse only the documented envelope; error messages are never persisted."""
    code = None
    try:
        doc = json.loads(result.raw.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # The API gateway emits XML for authentication/quota errors.
        if b'<!DOCTYPE' not in result.raw and b'<!ENTITY' not in result.raw:
            try:
                xml = ET.fromstring(result.raw)
                code = xml.findtext('.//returnReasonCode') or xml.findtext('.//resultCode')
            except ET.ParseError:
                pass
        raise KaptError('upstream_auth' if code in ('30', '31', '32') or result.status in (401, 403)
                        else 'upstream_quota' if code == '22' or result.status == 429
                        else 'upstream_format', provider_code=code) from None
    if not isinstance(doc, dict):
        raise KaptError('upstream_schema')
    envelope = doc.get('response', doc)
    if not isinstance(envelope, dict) or not isinstance(envelope.get('header'), dict):
        raise KaptError('upstream_schema')
    code = str(envelope['header'].get('resultCode', ''))
    if result.status != 200 or code not in ('00', '0'):
        raise KaptError('upstream_auth' if code in ('30', '31', '32') or result.status in (401, 403)
                        else 'upstream_quota' if code == '22' or result.status == 429
                        else 'upstream_result', provider_code=code)
    if not isinstance(envelope.get('body'), dict):
        raise KaptError('upstream_schema')
    return envelope['body']


def optional_text(value, flags, field):
    if value is None or value == '':
        return None
    if not isinstance(value, str) or len(value) > 2000:
        flags.append('invalid_' + field)
        return None
    return value


def normalize_list(body, page, size):
    number = integer(body.get('pageNo'), 'page_number')
    returned_size = integer(body.get('numOfRows'), 'page_size', 1000)
    total = integer(body.get('totalCount'), 'total_count')
    if number != page or returned_size != size:
        raise KaptError('pagination_mismatch')
    items = body.get('items')
    if not isinstance(items, list) or len(items) > size:
        raise KaptError('invalid_list_items')
    expected = max(0, min(size, total - (page - 1) * size))
    if len(items) != expected:
        raise KaptError('incomplete_page')
    rows = []
    for item in items:
        if not isinstance(item, dict):
            raise KaptError('invalid_list_item')
        code = kapt_code(item.get('kaptCode'))
        flags = []
        legal = optional_text(item.get('bjdCode'), flags, 'legal_dong_code')
        if legal is not None and not re.fullmatch(r'\d{10}', legal):
            flags.append('invalid_legal_dong_code')
            legal = None
        rows.append({'kapt_code': code, 'kapt_name': optional_text(item.get('kaptName'), flags, 'name'),
                     'legal_dong_code': legal,
                     'source_region_names': [optional_text(item.get(name), flags, name) for name in ('as1', 'as2', 'as3', 'as4')],
                     'coordinate': None, 'property_complex_id': None, 'quality_flags': flags})
    if len({row['kapt_code'] for row in rows}) != len(rows):
        raise KaptError('duplicate_kapt_code')
    return {'total_count': total, 'page': page, 'page_size': size, 'rows': rows}


def source_number(value, flags, field, *, integral=False):
    if value is None or value == '':
        return None
    try:
        number = Decimal(str(value))
        if isinstance(value, bool) or not number.is_finite() or number < 0 or number > 10**12 or (integral and number != number.to_integral_value()):
            raise InvalidOperation
    except InvalidOperation:
        flags.append('invalid_' + field)
        return None
    return int(number) if integral else float(number)


def normalize_basic(body, requested_code):
    item = body.get('item')
    if not isinstance(item, dict) or kapt_code(item.get('kaptCode')) != requested_code:
        raise KaptError('basic_identity_mismatch')
    flags = []
    row = {'kapt_code': requested_code, 'coordinate': None, 'property_complex_id': None,
           'quality_flags': flags}
    for dest, source in {'kapt_name': 'kaptName', 'legal_address': 'kaptAddr', 'road_address': 'doroJuso',
                         'legal_dong_code': 'bjdCode', 'zip_code': 'zipcode', 'sale_type': 'codeSaleNm',
                         'heating_type': 'codeHeatNm', 'complex_type': 'codeAptNm', 'management_type': 'codeMgrNm',
                         'hall_type': 'codeHallNm', 'builder': 'kaptBcompany', 'developer': 'kaptAcompany'}.items():
        row[dest] = optional_text(item.get(source), flags, dest)
    legal = row['legal_dong_code']
    if legal is not None and not re.fullmatch(r'\d{10}', legal):
        flags.append('invalid_legal_dong_code')
        row['legal_dong_code'] = None
    for dest, source in {'household_count': 'kaptdaCnt', 'building_count': 'kaptDongCnt',
                         'unit_count': 'hoCnt', 'top_floor': 'kaptTopFloor',
                         'building_register_top_floor': 'ktownFlrNo', 'basement_floors': 'kaptBaseFloor',
                         'passenger_elevators': 'kaptdEcntp'}.items():
        row[dest] = source_number(item.get(source), flags, dest, integral=True)
    for dest, source in {'gross_floor_area_m2': 'kaptTarea', 'maintenance_area_m2': 'kaptMarea',
                         'exclusive_area_sum_m2': 'privArea'}.items():
        row[dest] = source_number(item.get(source), flags, dest)
    approved = optional_text(item.get('kaptUsedate'), flags, 'use_approval_date')
    row['source_use_approval_date'] = approved
    row['use_approval_date'] = None
    if approved:
        try:
            if not re.fullmatch(r'\d{8}', approved):
                raise ValueError
            row['use_approval_date'] = datetime.strptime(approved, '%Y%m%d').date().isoformat()
        except ValueError:
            flags.append('invalid_use_approval_date')
    return row


class Checkpoint:
    def __init__(self, root, *, max_requests=MAX_REQUESTS, max_bytes=MAX_BYTES):
        self.root = Path(root).absolute()
        _reject_links(self.root)
        if not isinstance(max_requests, int) or not 1 <= max_requests <= MAX_REQUESTS or not isinstance(max_bytes, int) or not 1 <= max_bytes <= MAX_BYTES:
            raise KaptError('invalid_budget')
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / 'calls.sqlite'
        _reject_links(path)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS configuration (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
        contract = canonical({'schema_version': 1, 'max_requests': max_requests, 'max_bytes': max_bytes, 'endpoints': ENDPOINTS}).decode()
        existing = self.db.execute('SELECT value FROM configuration WHERE id=1').fetchone()
        if existing and existing[0] != contract:
            self.db.close()
            raise KaptError('checkpoint_contract_mismatch')
        self.db.execute('INSERT OR IGNORE INTO configuration VALUES (1,?)', (contract,))
        self.db.execute('''CREATE TABLE IF NOT EXISTS calls (
            request_key TEXT PRIMARY KEY, kind TEXT NOT NULL, params TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, reserved_bytes INTEGER NOT NULL,
            received_bytes INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
            raw_sha256 TEXT, http_status INTEGER, content_type TEXT, error_code TEXT, provider_code TEXT)''')
        self.db.commit()
        self.max_requests, self.max_bytes = max_requests, max_bytes

    def close(self):
        self.db.close()

    def summary(self):
        count, spent, received = self.db.execute("SELECT COUNT(*),COALESCE(SUM(CASE WHEN state='reserved' THEN reserved_bytes ELSE received_bytes END),0),COALESCE(SUM(received_bytes),0) FROM calls").fetchone()
        return {'requests_reserved': count, 'budget_bytes_used_or_reserved': spent, 'response_bytes_received': received,
                'max_requests': self.max_requests, 'max_bytes': self.max_bytes}

    def raw(self, sha):
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise KaptError('invalid_raw_hash')
        path = self.root / 'raw' / (sha + '.json')
        _reject_links(path)
        content = path.read_bytes()
        if digest(content) != sha:
            raise KaptError('raw_hash_mismatch')
        return content

    def request(self, kind, params, key, *, fetcher=fetch_official, interval=0.3):
        params = params_for(kind, params)
        token = digest(canonical([kind, params]))
        existing = self.db.execute('SELECT state,raw_sha256,http_status,content_type,error_code,provider_code FROM calls WHERE request_key=?', (token,)).fetchone()
        if existing:
            if existing[0] != 'ok':
                raise KaptError(existing[4] or 'uncertain_previous_request', provider_code=existing[5])
            result = HttpResult(existing[2], self.raw(existing[1]), existing[3])
            return parse_body(result)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            usage = self.summary()
            available = self.max_bytes - usage['budget_bytes_used_or_reserved']
            if usage['requests_reserved'] >= self.max_requests:
                raise KaptError('request_budget_exhausted')
            if available <= 0:
                raise KaptError('byte_budget_exhausted')
            cap = min(MAX_RESPONSE_BYTES, available)
            self.db.execute('INSERT INTO calls(request_key,kind,params,started_at,reserved_bytes,state) VALUES(?,?,?,?,?,?)',
                            (token, kind, canonical(params).decode(), instant(), cap, 'reserved'))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        # A crash here remains a charged reserved call, never an automatic retry.
        received = 0
        try:
            if interval:
                time.sleep(interval)
            result = fetcher(key, kind, params, max_bytes=cap)
            received = len(result.raw)
            if received > cap:
                raise KaptError('response_size_limit', received_bytes=cap)
            try:
                assert_no_secret(result.raw, key)
            except RealEstateError:
                raise KaptError('secret_reflection', received_bytes=received) from None
            sha = digest(result.raw)
            immutable(self.root, 'raw/' + sha + '.json', result.raw)
            body = parse_body(result)
            self.db.execute("UPDATE calls SET state='ok',finished_at=?,received_bytes=?,raw_sha256=?,http_status=?,content_type=? WHERE request_key=?",
                            (instant(), received, sha, result.status, result.content_type, token))
            self.db.commit()
            return body
        except KaptError as exc:
            self.db.execute("UPDATE calls SET state='error',finished_at=?,received_bytes=?,error_code=?,provider_code=? WHERE request_key=?",
                            (instant(), max(received, exc.received_bytes), exc.code, exc.provider_code, token))
            self.db.commit()
            raise


def immutable(root, relative, data):
    path = root / relative
    _reject_links(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise KaptError('unsafe_output_path')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise KaptError('immutable_file_conflict')
        return
    with path.open('xb') as stream:
        stream.write(data)


def collect(checkpoint, key, *, page_size=1000, basic_samples=10, fetcher=fetch_official, interval=0.3):
    if not 0 <= basic_samples <= 10:
        raise KaptError('invalid_basic_samples')
    first = normalize_list(checkpoint.request('list', {'pageNo': 1, 'numOfRows': page_size}, key, fetcher=fetcher, interval=interval), 1, page_size)
    basics = []
    for row in first['rows'][:basic_samples]:
        basics.append(normalize_basic(checkpoint.request('basic', {'kaptCode': row['kapt_code']}, key, fetcher=fetcher, interval=interval), row['kapt_code']))
    pages = [first]
    count = math.ceil(first['total_count'] / page_size)
    for page in range(2, count + 1):
        current = normalize_list(checkpoint.request('list', {'pageNo': page, 'numOfRows': page_size}, key, fetcher=fetcher, interval=interval), page, page_size)
        if current['total_count'] != first['total_count']:
            raise KaptError('upstream_total_changed')
        pages.append(current)
    rows = [row for page in pages for row in page['rows']]
    if len(rows) != first['total_count'] or len({row['kapt_code'] for row in rows}) != len(rows):
        raise KaptError('national_coverage_mismatch')
    records = checkpoint.db.execute("SELECT kind,params,started_at,finished_at,raw_sha256,received_bytes FROM calls WHERE state='ok' ORDER BY started_at,request_key").fetchall()
    sources = [{'kind': r[0], 'parameters': json.loads(r[1]), 'requested_at': r[2], 'retrieved_at': r[3],
                'raw_sha256': r[4], 'byte_length': r[5]} for r in records]
    candidate = {'schema_version': 1, 'kind': 'kapt-context-sidecar',
                 'provider': '국토교통부 · 공동주택관리정보시스템(K-apt)',
                 'sources': [{'kind': kind, 'page': SOURCES[kind], 'endpoint': 'https://apis.data.go.kr' + endpoint} for kind, endpoint in ENDPOINTS.items()],
                 'reference_date': None, 'retrieved_from': min(r['requested_at'] for r in sources),
                 'retrieved_until': max(r['retrieved_at'] for r in sources),
                 'coverage': {'scope': 'K-apt registered complexes returned by the nationwide list API',
                              'provider_total_count': first['total_count'], 'list_rows': len(rows), 'list_complete': True,
                              'list_pages': len(pages), 'basic_sample_rows': len(basics),
                              'basic_sampling': 'first distinct complexes in provider page 1; not representative',
                              'all_korean_apartments_complete': False, 'provider_snapshot_is_atomic': False},
                 'identity': {'key': 'kapt_code', 'same_as_molit_aptSeq': False, 'automatic_property_join': False},
                 'coordinates': {'status': 'not-provided-by-these-operations', 'inferred': False},
                 'license': {'portal_marking': '이용허락범위 제한 없음', 'portal_checked_at': '2026-09-20',
                             'source_attribution_retained': True},
                 'raw_sources': sources, 'budget': checkpoint.summary(), 'complexes': rows, 'basic_samples': basics}
    payload = canonical(candidate)
    name = 'kapt-' + digest(payload)[:16]
    immutable(checkpoint.root, 'candidates/' + name + '/context.json', payload)
    return {'candidate_path': str(checkpoint.root / 'candidates' / name / 'context.json'), 'sha256': digest(payload),
            'byte_length': len(payload), 'coverage': candidate['coverage'], 'budget': candidate['budget']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--secret-file', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--page-size', type=int, default=1000)
    parser.add_argument('--basic-samples', type=int, default=10)
    args = parser.parse_args()
    checkpoint = None
    try:
        private = Path(__file__).resolve().parents[1] / '.local'
        _reject_links(args.root.absolute())
        if not args.root.resolve().is_relative_to(private.resolve()):
            raise KaptError('private_output_required')
        params_for('list', {'pageNo': 1, 'numOfRows': args.page_size})
        if not args.execute:
            print(json.dumps({'execute': False, 'max_requests': MAX_REQUESTS, 'max_bytes': MAX_BYTES,
                              'endpoints': ENDPOINTS, 'basic_samples': args.basic_samples}, ensure_ascii=False))
            return
        key = read_key(args.secret_file)
        checkpoint = Checkpoint(args.root)
        print(json.dumps(collect(checkpoint, key, page_size=args.page_size, basic_samples=args.basic_samples), ensure_ascii=False))
    except (KaptError, RealEstateError) as exc:
        print(json.dumps({'status': 'stopped', 'error': str(exc), 'provider_code': getattr(exc, 'provider_code', None),
                          'budget': checkpoint.summary() if checkpoint else None}, ensure_ascii=False))
        raise SystemExit(1) from None
    finally:
        if checkpoint:
            checkpoint.close()


if __name__ == '__main__':
    main()
