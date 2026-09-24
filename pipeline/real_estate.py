"""Offline MOLIT apartment sale-report normalization; no fetch or publication.

One complete region/month page set is a snapshot, not a transaction event log.
The source has no transaction primary key: identical rows retain multiplicity.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET

SOURCE_ID = 'molit-apt-sale-detail'
SOURCE_URL = 'https://www.data.go.kr/data/15126468/openapi.do'
TRANSFORM_VERSION = 'apartment-sale-report-v2-opaque-apt-seq'
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_BATCH_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 100_000
MAX_SAFE_INTEGER = 9_007_199_254_740_991
KST = timezone(timedelta(hours=9))
FIELDS = frozenset(('sggCd umdCd landCd bonbun bubun roadNm roadNmSggCd roadNmCd '
    'roadNmSeq roadNmbCd roadNmBonbun roadNmBubun umdNm aptNm jibun excluUseAr '
    'dealYear dealMonth dealDay dealAmount floor buildYear aptSeq cdealType '
    'cdealDay dealingGbn estateAgentSggNm rgstDate aptDong slerGbn buyerGbn '
    'landLeaseholdGbn').split())
RENT_SOURCE_ID = 'molit-apt-rent'
RENT_SOURCE_URL = 'https://www.data.go.kr/data/15126474/openapi.do'
RENT_TRANSFORM_VERSION = 'apartment-rent-report-v2-opaque-apt-seq'
RENT_FIELDS = frozenset(('sggCd umdNm aptNm jibun excluUseAr dealYear dealMonth '
    'dealDay deposit monthlyRent floor buildYear contractTerm contractType '
    'useRRRight preDeposit preMonthlyRent aptSeq roadnm roadnmbcd roadnmbonbun '
    'roadnmbubun roadnmcd roadnmseq roadnmsggcd').split())


OFFICETEL_FIELDS = frozenset(('sggCd sggNm umdNm jibun offiNm excluUseAr dealYear '
    'dealMonth dealDay dealAmount floor buildYear cdealType cdealDay dealingGbn '
    'estateAgentSggNm slerGbn buyerGbn').split())
OFFICETEL_RENT_FIELDS = frozenset(('sggCd sggNm umdNm jibun offiNm excluUseAr dealYear '
    'dealMonth dealDay deposit monthlyRent floor buildYear contractTerm contractType '
    'useRRRight preDeposit preMonthlyRent').split())


def validate_property_type(value):
    if value not in ('apartment', 'officetel'):
        raise RealEstateError('unsupported_property_type')
    return value


def report_source(property_type, trade_type):
    validate_property_type(property_type)
    if trade_type not in ('sale', 'rent'):
        raise RealEstateError('invalid_trade_type')
    rent = trade_type == 'rent'
    if property_type == 'officetel':
        dataset = '15126475' if rent else '15126464'
        return {'id': f'molit-officetel-{trade_type}', 'dataset_id': dataset,
                'page_url': f'https://www.data.go.kr/data/{dataset}/openapi.do'}
    return {'id': RENT_SOURCE_ID if rent else SOURCE_ID,
            'dataset_id': '15126474' if rent else '15126468',
            'page_url': RENT_SOURCE_URL if rent else SOURCE_URL}


class RealEstateError(ValueError):
    """Only fixed codes are exposed; never echo source XML, URLs or credentials."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode('utf-8')


def sha256(value: bytes):
    return hashlib.sha256(value).hexdigest()


def validate_scope(lawd_code, deal_month):
    if not isinstance(lawd_code, str) or not re.fullmatch(r'[0-9]{5}', lawd_code) or lawd_code == '00000':
        raise RealEstateError('invalid_lawd_code')
    if not isinstance(deal_month, str) or not re.fullmatch(r'[0-9]{6}', deal_month):
        raise RealEstateError('invalid_deal_month')
    try:
        date(int(deal_month[:4]), int(deal_month[4:]), 1)
    except ValueError:
        raise RealEstateError('invalid_deal_month') from None


def utc_instant(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', value):
        raise RealEstateError('invalid_retrieved_at')
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        raise RealEstateError('invalid_retrieved_at') from None


def area_string(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,6})?', value.strip()):
        raise ValueError('invalid_format')
    number = Decimal(value.strip())
    if not 0 < number <= 10_000:
        raise ValueError('out_of_range')
    return format(number, 'f').rstrip('0').rstrip('.') if '.' in format(number, 'f') else format(number, 'f')


def _children(element):
    result = {}
    for child in element:
        if not isinstance(child.tag, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,119}', child.tag):
            raise RealEstateError('invalid_xml_field')
        if child.tag in result:
            raise RealEstateError('duplicate_xml_field')
        result[child.tag] = child
    return result


def _text(element):
    if element is None:
        return ''
    if len(element):
        raise RealEstateError('nested_scalar')
    text = (element.text or '').strip()
    if len(text) > 512 or any(ord(c) < 32 for c in text):
        raise RealEstateError('invalid_scalar')
    return text


def _natural(value, *, minimum=0, maximum=MAX_RECORDS):
    if not re.fullmatch(r'[0-9]+', value):
        raise RealEstateError('invalid_page_metadata')
    number = int(value)
    if not minimum <= number <= maximum:
        raise RealEstateError('invalid_page_metadata')
    return number


def _provider_error(code):
    if code in ('20', '30', '31', 'SERVICE_ACCESS_DENIED_ERROR', 'SERVICE_KEY_IS_NOT_REGISTERED_ERROR'):
        return 'upstream_auth'
    if code in ('22', '23', 'LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR'):
        return 'upstream_quota'
    return 'upstream_error'


def _source_date(value, contract_date, retrieved_date):
    """A two-digit year is accepted only if its century is unique in the interval."""
    value = value.rstrip('.')
    if re.fullmatch(r'[0-9]{8}', value):
        parts = (value[:4], value[4:6], value[6:])
    else:
        match = re.fullmatch(r'([0-9]{4}|[0-9]{2})[-.]([0-9]{1,2})[-.]([0-9]{1,2})', value)
        if not match:
            raise ValueError('invalid_format')
        parts = match.groups()
    year, month, day = map(int, parts)
    if len(parts[0]) == 2:
        if contract_date is None:
            raise ValueError('ambiguous')
        candidates = []
        for century in range(contract_date.year // 100, retrieved_date.year // 100 + 1):
            try:
                candidate = date(century * 100 + year, month, day)
            except ValueError:
                continue
            if contract_date <= candidate <= retrieved_date:
                candidates.append(candidate)
        if len(candidates) != 1:
            raise ValueError('ambiguous')
        return candidates[0].isoformat()
    try:
        parsed = date(year, month, day)
    except ValueError:
        raise ValueError('invalid_format') from None
    if parsed > retrieved_date or contract_date is not None and parsed < contract_date:
        raise ValueError('out_of_range')
    return parsed.isoformat()


def normalize_record(raw, lawd_code, deal_month, retrieved_at, input_hash, *, trade_type='sale', property_type='apartment'):
    validate_property_type(property_type)
    office = property_type == 'officetel'
    if trade_type not in ('sale', 'rent'):
        raise RealEstateError('invalid_trade_type')
    rent = trade_type == 'rent'
    issues = []
    as_of = utc_instant(retrieved_at).astimezone(KST).date()

    def issue(field, code):
        issues.append({'field': field, 'code': code})

    def field(name, parse, *, optional=False):
        value = raw.get(name, '')
        if not value:
            if not optional:
                issue(name, 'missing')
            return None
        try:
            return parse(value)
        except (ValueError, OverflowError) as error:
            code = str(error)
            issue(name, code if code in ('invalid_format', 'out_of_range', 'ambiguous') else 'invalid_format')
            return None

    def pattern(value, expression):
        if not re.fullmatch(expression, value):
            raise ValueError('invalid_format')
        return value

    def integer(value, low, high):
        pattern(value, r'-?[0-9]+')
        number = int(value)
        if not low <= number <= high:
            raise ValueError('out_of_range')
        return number

    def money(value, *, allow_zero=False):
        pattern(value, r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)')
        result = int(value.replace(',', '')) * 10_000
        if not (0 <= result if allow_zero else 0 < result) or result > MAX_SAFE_INTEGER:
            raise ValueError('out_of_range')
        return result

    sgg = field('sggCd', lambda v: pattern(v, r'[0-9]{5}'))
    umd = field('umdCd', lambda v: pattern(v, r'[0-9]{5}'), optional=rent or office)
    if sgg is not None and sgg != lawd_code:
        issue('sggCd', 'scope_mismatch')
    name = field('offiNm' if office else 'aptNm', lambda v: pattern(v, r'.{1,120}'))
    umd_name = field('umdNm', lambda v: pattern(v, r'.{1,120}'))
    jibun = field('jibun', lambda v: pattern(v, r'(?:산\s*)?[0-9]{1,5}(?:-[0-9]{1,5})?'))
    apt_seq = None if office else field('aptSeq', lambda v: pattern(v, r'[A-Za-z0-9_-]{1,64}'), optional=rent or office)
    # aptSeq is the provider's opaque identity, not a current legal-code field.
    # Real responses retain older prefixes after administrative changes. Validate
    # the explicit sggCd instead; never rewrite or infer geography from aptSeq.
    complex_id = f'molit-apt:{lawd_code}:{apt_seq}' if apt_seq and sgg == lawd_code else None
    price = None if rent else field('dealAmount', money)
    deposit = field('deposit', lambda v: money(v, allow_zero=True)) if rent else None
    monthly_rent = field('monthlyRent', lambda v: money(v, allow_zero=True)) if rent else None
    previous_deposit = field('preDeposit', lambda v: money(v, allow_zero=True), optional=True) if rent else None
    previous_rent = field('preMonthlyRent', lambda v: money(v, allow_zero=True), optional=True) if rent else None
    if rent and deposit == 0 and monthly_rent == 0:
        issue('deposit', 'out_of_range')
    area = field('excluUseAr', area_string)
    floor = field('floor', lambda v: integer(v, -100, 1000))
    year = field('dealYear', lambda v: integer(v, 1, 9999))
    month = field('dealMonth', lambda v: integer(v, 1, 12))
    day = field('dealDay', lambda v: integer(v, 1, 31))
    contract = None
    if year is not None and month is not None and day is not None:
        try:
            contract = date(year, month, day)
        except ValueError:
            issue('contract_date', 'invalid_format')
        if contract:
            if contract.strftime('%Y%m') != deal_month:
                issue('contract_date', 'scope_mismatch')
            if contract > as_of:
                issue('contract_date', 'out_of_range')
    build_year = field('buildYear', lambda v: integer(v, 1, as_of.year))
    registered = field('rgstDate', lambda v: _source_date(v, contract, as_of), optional=True)
    cancelled_date = field('cdealDay', lambda v: _source_date(v, contract, as_of), optional=True)
    flag = raw.get('cdealType', '')
    if rent:
        cancellation = 'not_provided'
    elif flag in ('O', 'Y') or raw.get('cdealDay', ''):
        cancellation = 'cancelled'
        if flag not in ('', 'O', 'Y'):
            issue('cdealType', 'unknown_value')
    elif flag:
        cancellation = 'unknown'
        issue('cdealType', 'unknown_value')
    else:
        cancellation = 'not_reported'
    issues.sort(key=lambda i: (i['field'], i['code']))
    quality = 'invalid' if any(i['code'] != 'missing' for i in issues) else 'incomplete' if issues else 'valid'
    result = {
        'kind': f'{property_type}-{trade_type}-report',
        'complex_id': complex_id, 'source_complex_id': apt_seq, 'complex_name': name,
        'lawd_code': sgg, 'legal_dong_code': sgg + umd if sgg and umd else None,
        'legal_dong_name': umd_name, 'lot_number': jibun,
        'price_krw': price, 'source_price_unit': '10,000 KRW', 'area_m2': area,
        'floor': floor, 'build_year': build_year,
        'contract_date': contract.isoformat() if contract else None,
        'registration_date': registered, 'reported_at': None, 'source_updated_at': None,
        'cancellation': {'status': cancellation, 'reason_date': cancelled_date, 'source_flag': flag or None},
        'position': None, 'quality': quality, 'issues': issues,
        'source_fields': dict(sorted(raw.items())),
        'provenance': {'source_id': report_source(property_type, trade_type)['id'],
            'dataset_id': report_source(property_type, trade_type)['dataset_id'],
            'evidence_type': 'official_report', 'observed_at': None,
            'retrieved_at': retrieved_at, 'input_sha256': input_hash,
            'transform_version': f'officetel-{trade_type}-report-v1' if office else RENT_TRANSFORM_VERSION if rent else TRANSFORM_VERSION},
    }
    if rent:
        result.update(deposit_krw=deposit, monthly_rent_krw=monthly_rent,
            previous_deposit_krw=previous_deposit, previous_monthly_rent_krw=previous_rent,
            contract_term=raw.get('contractTerm') or None,
            contract_type=raw.get('contractType') or None,
            renewal_right=raw.get('useRRRight') or None)
    return result


def normalize_xml_page(raw_xml: bytes, *, lawd_code: str, deal_month: str, retrieved_at: str,
                       trade_type='sale', property_type='apartment'):
    validate_property_type(property_type)
    if trade_type not in ('sale', 'rent'):
        raise RealEstateError('invalid_trade_type')
    fields = (OFFICETEL_RENT_FIELDS if trade_type == 'rent' else OFFICETEL_FIELDS) if property_type == 'officetel' else (RENT_FIELDS if trade_type == 'rent' else FIELDS)
    validate_scope(lawd_code, deal_month)
    utc_instant(retrieved_at)
    if not isinstance(raw_xml, bytes) or not 0 < len(raw_xml) <= MAX_PAGE_BYTES:
        raise RealEstateError('page_size_limit')
    try:
        text = raw_xml.decode('utf-8-sig')
    except UnicodeError:
        raise RealEstateError('invalid_xml_encoding') from None
    if '\x00' in text:
        raise RealEstateError('invalid_xml_encoding')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.I):
        raise RealEstateError('unsafe_xml')
    encoding = re.search(r'<\?xml[^>]*encoding\s*=\s*[\'"]([^\'"]+)', text, re.I)
    if encoding and encoding.group(1).lower() not in ('utf-8', 'utf8'):
        raise RealEstateError('invalid_xml_encoding')
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        raise RealEstateError('invalid_xml') from None
    if root.tag == 'OpenAPI_ServiceResponse':
        raise RealEstateError(_provider_error((root.findtext('cmmMsgHeader/returnReasonCode') or '').strip()))
    if root.tag != 'response':
        raise RealEstateError('invalid_envelope')
    envelope = _children(root)
    if 'header' not in envelope:
        raise RealEstateError('missing_header')
    header = _children(envelope['header'])
    code = _text(header.get('resultCode'))
    if code not in ('00', '000', '0'):
        raise RealEstateError(_provider_error(code))
    if 'body' not in envelope:
        raise RealEstateError('missing_body')
    body = _children(envelope['body'])
    page_no = _natural(_text(body.get('pageNo')), minimum=1, maximum=1000)
    page_size = _natural(_text(body.get('numOfRows')), minimum=1, maximum=10_000)
    total = _natural(_text(body.get('totalCount')))
    items = body.get('items')
    rows = []
    unknown = set()
    if items is not None:
        if (items.text or '').strip():
            raise RealEstateError('invalid_items')
        for item in items:
            if item.tag != 'item':
                raise RealEstateError('invalid_item')
            values = _children(item)
            unknown.update(set(values) - fields)
            rows.append({key: _text(value) for key, value in values.items() if key in fields})
    expected = max(0, min(page_size, total - (page_no - 1) * page_size))
    if len(rows) != expected or page_no > max(1, (total + page_size - 1) // page_size):
        raise RealEstateError('page_count_mismatch')
    input_hash = sha256(raw_xml)
    return {**({'property_type': property_type} if property_type != 'apartment' else {}),
        'lawd_code': lawd_code, 'deal_month': deal_month, 'trade_type': trade_type,
        'page_no': page_no, 'page_size': page_size, 'total_count': total,
        'input_sha256': input_hash, 'input_bytes': len(raw_xml), 'retrieved_at': retrieved_at,
        'unknown_fields': sorted(unknown),
        'records': [normalize_record(row, lawd_code, deal_month, retrieved_at, input_hash,
                                    trade_type=trade_type, property_type=property_type) for row in rows]}


def build_partitions(pages):
    pages = list(pages)
    if not pages or sum(p['input_bytes'] for p in pages) > MAX_BATCH_BYTES:
        raise RealEstateError('batch_size_limit')
    groups = defaultdict(dict)
    for page in pages:
        property_type = validate_property_type(page.get('property_type', 'apartment'))
        scope = (page['lawd_code'], page['deal_month'], page.get('trade_type', 'sale'), property_type)
        previous = groups[scope].get(page['page_no'])
        if previous is not None:
            if canonical_bytes(previous) != canonical_bytes(page):
                raise RealEstateError('conflicting_page')
            continue  # Repeating a source page is not an additional reported trade.
        groups[scope][page['page_no']] = page
    partitions = []
    for (lawd_code, deal_month, trade_type, property_type), by_number in sorted(groups.items()):
        rent = trade_type == 'rent'
        ordered = [by_number[n] for n in sorted(by_number)]
        total, size = ordered[0]['total_count'], ordered[0]['page_size']
        if any(p['total_count'] != total or p['page_size'] != size for p in ordered):
            raise RealEstateError('inconsistent_pagination')
        if sorted(by_number) != list(range(1, max(1, (total + size - 1) // size) + 1)):
            raise RealEstateError('missing_page')
        instants = [utc_instant(p['retrieved_at']) for p in ordered]
        if (max(instants) - min(instants)).total_seconds() > 900:
            raise RealEstateError('mixed_snapshot_window')
        records = []
        occurrences = Counter()
        for page in ordered:
            for source in page['records']:
                if source.get('kind') != f'{property_type}-{trade_type}-report':
                    raise RealEstateError('mixed_property_record_type')
                row = {**source}
                fingerprint = sha256(canonical_bytes({'lawd_code': lawd_code, 'deal_month': deal_month,
                                                       'source_fields': row['source_fields']}))
                occurrences[fingerprint] += 1
                prefix = 'molit' if property_type == 'apartment' else 'molit-officetel'
                row['id'] = f'{prefix}-{trade_type}:{fingerprint}:{occurrences[fingerprint]}'
                records.append(row)
        if len(records) != total:
            raise RealEstateError('snapshot_count_mismatch')
        records.sort(key=lambda r: r['id'])
        issue_counts = Counter((i['field'], i['code']) for r in records for i in r['issues'])
        audit = {'source_rows': total, 'record_count': len(records), 'complete_pages': True,
            'identical_row_occurrences': sum(n - 1 for n in occurrences.values()),
            'quality_counts': {q: sum(r['quality'] == q for r in records) for q in ('valid', 'incomplete', 'invalid')},
            'cancellation_counts': {q: sum(r['cancellation']['status'] == q for r in records)
                                    for q in (('not_provided',) if rent else ('not_reported', 'cancelled', 'unknown'))},
            'issue_counts': [{'field': f, 'code': c, 'count': n} for (f, c), n in sorted(issue_counts.items())],
            'pages': [{key: p[key] for key in ('page_no', 'page_size', 'total_count', 'input_sha256',
                       'input_bytes', 'retrieved_at', 'unknown_fields')} for p in ordered]}
        partitions.append({'schema_version': 1, 'kind': f'{property_type}-{trade_type}-report-partition',
            'lawd_code': lawd_code, 'deal_month': deal_month,
            'source': report_source(property_type, trade_type),
            'retrieved_at': max(p['retrieved_at'] for p in ordered),
            'records': records, 'audit': audit})
    if sum(len(p['records']) for p in partitions) > MAX_RECORDS:
        raise RealEstateError('batch_record_limit')
    return partitions


def summarize_transactions(records, *, complex_id, area_m2, date_from, date_to):
    try:
        area = area_string(area_m2)
    except ValueError:
        raise RealEstateError('invalid_summary_filter') from None
    try:
        start, end = date.fromisoformat(date_from), date.fromisoformat(date_to)
    except (ValueError, TypeError):
        raise RealEstateError('invalid_summary_period') from None
    if (date_from != start.isoformat() or date_to != end.isoformat() or area_m2 != area
            or start > end or not isinstance(complex_id, str)
            or not re.fullmatch(r'molit-apt:[0-9]{5}:[A-Za-z0-9_-]{1,64}', complex_id)):
        raise RealEstateError('invalid_summary_filter')
    records = list(records)
    if len({r['id'] for r in records}) != len(records):
        raise RealEstateError('duplicate_snapshot_records')
    values = sorted(r['price_krw'] for r in records
        if r['complex_id'] == complex_id and r['area_m2'] == area
        and r['contract_date'] is not None and date_from <= r['contract_date'] <= date_to
        and r['price_krw'] is not None and r['quality'] != 'invalid'
        and r['cancellation']['status'] == 'not_reported')
    middle = len(values) // 2
    median = None if not values else values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) // 2
    return {'complex_id': complex_id, 'area_m2': area, 'date_from': start.isoformat(),
        'date_to': end.isoformat(), 'count': len(values), 'median_price_krw': median,
        'price_unit': 'KRW', 'cancellation_policy': 'exclude', 'statistic': 'reported-row-median'}


def _reject_links(path):
    for node in (path, *path.parents):
        if node.is_symlink() or hasattr(node, 'is_junction') and node.is_junction():
            raise RealEstateError('linked_path')


def write_dataset(partitions, output_dir):
    """Additive atomic folder publication in a non-public staging location only."""
    output = Path(output_dir).absolute()
    _reject_links(output)
    if any(p.lower() in ('public', 'dist') for p in output.parts):
        raise RealEstateError('public_output_forbidden')
    if output.exists():
        raise RealEstateError('output_exists')
    payloads = {}
    entries = []
    for partition in sorted(partitions, key=lambda p: (p['lawd_code'], p['deal_month'])):
        if partition.get('kind') != 'apartment-sale-report-partition':
            raise RealEstateError('use_property_release_publisher')
        validate_scope(partition['lawd_code'], partition['deal_month'])
        name = f"{partition['lawd_code']}/{partition['deal_month']}.json"
        if name in payloads:
            raise RealEstateError('duplicate_partition')
        payload = canonical_bytes(partition)
        payloads[name] = payload
        entries.append({'path': name, 'sha256': sha256(payload), 'bytes': len(payload),
                        'record_count': len(partition['records'])})
    if not payloads:
        raise RealEstateError('empty_dataset')
    manifest = {'schema_version': 1, 'kind': 'apartment-sale-report-manifest',
        'source': {'id': SOURCE_ID, 'dataset_id': '15126468', 'page_url': SOURCE_URL},
        'transform_version': TRANSFORM_VERSION, 'partitions': entries}
    payloads['manifest.json'] = canonical_bytes(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{output.name}-incomplete-', dir=output.parent))
    # Failed staging is intentionally retained for inspection; no existing data is removed.
    for name, payload in payloads.items():
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(payload)
        if sha256(target.read_bytes()) != sha256(payload):
            raise RealEstateError('output_hash_mismatch')
    if output.exists():
        raise RealEstateError('output_exists')
    os.rename(staging, output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        _reject_links(args.manifest.absolute())
        if args.manifest.stat().st_size > 1024 * 1024:
            raise RealEstateError('manifest_size_limit')
        descriptor = json.loads(args.manifest.read_text(encoding='utf-8'))
        if not isinstance(descriptor, dict) or set(descriptor) != {'schema_version', 'pages'} or descriptor['schema_version'] != 1:
            raise RealEstateError('invalid_input_manifest')
        if not isinstance(descriptor['pages'], list) or not 1 <= len(descriptor['pages']) <= 1000:
            raise RealEstateError('invalid_input_manifest')
        pages = []
        total_bytes = 0
        for entry in descriptor['pages']:
            if not isinstance(entry, dict) or set(entry) != {'path', 'lawd_code', 'deal_month', 'retrieved_at'}:
                raise RealEstateError('invalid_input_manifest')
            if not isinstance(entry['path'], str) or Path(entry['path']).is_absolute() or '..' in Path(entry['path']).parts:
                raise RealEstateError('invalid_source_path')
            path = args.manifest.parent / entry['path']
            _reject_links(path.absolute())
            if (not path.resolve().is_relative_to(args.manifest.parent.resolve())
                    or path.suffix.lower() != '.xml' or not path.is_file()):
                raise RealEstateError('invalid_source_path')
            total_bytes += path.stat().st_size
            if path.stat().st_size > MAX_PAGE_BYTES or total_bytes > MAX_BATCH_BYTES:
                raise RealEstateError('batch_size_limit')
            pages.append(normalize_xml_page(path.read_bytes(), lawd_code=entry['lawd_code'],
                deal_month=entry['deal_month'], retrieved_at=entry['retrieved_at']))
        manifest = write_dataset(build_partitions(pages), args.output)
        print(json.dumps({'status': 'verified', 'partitions': len(manifest['partitions']),
            'records': sum(p['record_count'] for p in manifest['partitions'])}))
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"real_estate: {error.code if isinstance(error, RealEstateError) else 'invalid_input'}\n")


if __name__ == '__main__':
    main()
