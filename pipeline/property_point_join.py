"""Audited Seoul apartment point to MOLIT complex bridge.

Only a one-to-one district/road/building-number match corroborated by the
normalized complex name is published. The Seoul provider's point CRS and point
semantics remain unverified; this bridge establishes identity, not geometry.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import date

from .seoul_apartments import SOURCE, canonical

OLD_POINTS_SHA = '8360eb2d88be0ab4259b5d92e5a98d25372e6bf19ad739dfbad6c26622debe82'
ROAD_NUMBER = re.compile(r'(\d+)(?:-(\d+))?\Z')
COMPLEX_ID = re.compile(r'molit-apt:(\d{5}):([A-Za-z0-9_-]{1,64})\Z')


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _read_hash(path: Path, expected: str, maximum: int) -> bytes:
    if not re.fullmatch('[a-f0-9]{64}', expected) or path.is_symlink() or not path.is_file():
        raise ValueError('invalid_source_file')
    if not 0 < path.stat().st_size <= maximum:
        raise ValueError('invalid_source_size')
    body = path.read_bytes()
    if _sha(body) != expected:
        raise ValueError('source_hash_mismatch')
    return body


def _name(value: str) -> str:
    return re.sub(r'[\s()·・-]', '', value or '').removesuffix('아파트').casefold()


def _seoul_key(row: dict, district_codes: dict[str, str]):
    district = (row.get('SGG_ADDR') or '').strip()
    road = (row.get('RDN_ADDR') or '').strip()
    number = ROAD_NUMBER.fullmatch((row.get('ROAD_DADDR') or '').strip())
    code = district_codes.get(district)
    if not code or not road or not number or row.get('CTPV_ADDR') != '서울':
        return None
    # The independent full-address column must corroborate the component columns.
    full = (row.get('APT_RDN_ADDR') or '').strip()
    if full != f'서울특별시 {district} {road} {number.group()}':
        return None
    return code, road, int(number[1]), int(number[2] or 0)


def _molit_key(row: dict, lawd_code: str):
    main = (row.get('roadNmBonbun') or '').strip()
    sub = (row.get('roadNmBubun') or '').strip()
    road = (row.get('roadNm') or '').strip()
    seq = (row.get('aptSeq') or '').strip()
    if (row.get('sggCd') or '').strip() != lawd_code or (row.get('roadNmSggCd') or '').strip() != lawd_code:
        return None
    if not (main.isascii() and main.isdigit() and sub.isascii() and sub.isdigit() and road
            and seq and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', seq) and int(main) > 0):
        return None
    return lawd_code, road, int(main), int(sub)


def match_identities(seoul_rows: list[dict], molit_rows: list[tuple[str, dict]],
                     district_codes: dict[str, str], published_complexes: set[str]):
    """Return only independently corroborated one-to-one identities and audit."""
    seoul_by_key = defaultdict(list)
    molit_by_key = defaultdict(set)
    keys_by_seq = defaultdict(set)
    names_by_seq = defaultdict(set)
    seoul_unkeyed = molit_unkeyed = 0
    seen_codes = set()
    for row in seoul_rows:
        code = row.get('APT_CD')
        if not isinstance(code, str) or not re.fullmatch(r'[A-Z][0-9]{8,12}', code) or code in seen_codes:
            raise ValueError('duplicate_or_invalid_kapt_code')
        seen_codes.add(code)
        key = _seoul_key(row, district_codes)
        if key is None:
            seoul_unkeyed += 1
        else:
            seoul_by_key[key].append(row)
    for lawd_code, row in molit_rows:
        key = _molit_key(row, lawd_code)
        if key is None:
            molit_unkeyed += 1
            continue
        seq = row['aptSeq'].strip()
        molit_by_key[key].add(seq)
        keys_by_seq[(lawd_code, seq)].add(key)
        names_by_seq[(lawd_code, seq)].add((row.get('aptNm') or '').strip())
    matches = {}
    audit = Counter(seoul_unkeyed=seoul_unkeyed, molit_unkeyed=molit_unkeyed)
    for key, rows in seoul_by_key.items():
        if len(rows) != 1:
            audit['shared_seoul_address'] += len(rows)
            continue
        sequences = molit_by_key.get(key, set())
        if not sequences:
            audit['no_sale_address'] += 1
            continue
        if len(sequences) != 1:
            audit['shared_molit_address'] += 1
            continue
        seq = next(iter(sequences))
        if len(keys_by_seq[(key[0], seq)]) != 1:
            audit['changed_molit_address'] += 1
            continue
        row = rows[0]
        normalized_name = _name(row.get('APT_NM', ''))
        if not normalized_name or normalized_name not in {_name(name) for name in names_by_seq[(key[0], seq)]}:
            audit['name_conflict'] += 1
            continue
        complex_id = f'molit-apt:{key[0]}:{seq}'
        if complex_id not in published_complexes:
            audit['absent_from_property_release'] += 1
            continue
        matches[row['APT_CD']] = complex_id
        audit['matched'] += 1
    audit['seoul_rows'] = len(seoul_rows)
    audit['molit_rows'] = len(molit_rows)
    audit['matched_road_keys'] = len(molit_by_key)
    if (audit['matched'] + audit['shared_seoul_address'] + audit['no_sale_address']
            + audit['shared_molit_address'] + audit['changed_molit_address']
            + audit['name_conflict'] + audit['absent_from_property_release']
            + audit['seoul_unkeyed'] != len(seoul_rows)):
        raise ValueError('identity_audit_partition_mismatch')
    return matches, dict(audit)


def _seoul_rows(collection: Path):
    receipt = json.loads((collection / 'collection-receipt.json').read_text(encoding='utf-8'))
    if receipt.get('source') != SOURCE or receipt.get('dataset') != 'OpenAptInfo':
        raise ValueError('unexpected_seoul_source')
    rows, hashes = [], []
    for page in receipt['pages']:
        digest = page['sha256']
        body = _read_hash(collection / 'raw' / (digest + '.json'), digest, 8 * 1024**2)
        if len(body) != page['bytes']:
            raise ValueError('seoul_page_size_mismatch')
        table = json.loads(body)['OpenAptInfo']
        part = table['row']
        if table['RESULT']['CODE'] != 'INFO-000' or len(part) != page['rows']:
            raise ValueError('seoul_page_mismatch')
        rows.extend(part)
        hashes.append(digest)
    if len(rows) != receipt['declared_rows'] or len(rows) != receipt['collected_rows']:
        raise ValueError('seoul_total_mismatch')
    return rows, hashes


def _property_complexes(property_root: Path):
    release = property_root.name
    regions = json.loads((property_root / 'regions.json').read_text(encoding='utf-8'))
    if regions['release_id'] != release:
        raise ValueError('property_release_mismatch')
    codes = {}
    complexes = set()
    for region in regions['regions']:
        code = region['lawd_code']
        if not re.fullmatch(r'11\d{3}', code):
            continue
        name = region['name'].split()[-1]
        if name in codes or not name.endswith('구'):
            raise ValueError('duplicate_seoul_district')
        codes[name] = code
        index = property_root / 'regions' / (code + '.json')
        descriptor = region['index']
        body = _read_hash(index, descriptor['sha256'], 4 * 1024**2)
        if len(body) != descriptor['bytes']:
            raise ValueError('property_index_size_mismatch')
        detail = json.loads(body)
        ref = detail['complexes']
        path = property_root / 'complexes' / (code + '.json')
        body = _read_hash(path, ref['sha256'], 8 * 1024**2)
        if len(body) != ref['bytes']:
            raise ValueError('property_complex_size_mismatch')
        data = json.loads(body)
        if data['release_id'] != release or data['lawd_code'] != code:
            raise ValueError('property_complex_scope_mismatch')
        for row in data['complexes']:
            identity = row['id']
            match = COMPLEX_ID.fullmatch(identity)
            if not match or match[1] != code or identity in complexes:
                raise ValueError('property_complex_identity_invalid')
            complexes.add(identity)
    if len(codes) != 25:
        raise ValueError('incomplete_seoul_property_regions')
    return release, codes, complexes


def _twelve_months(end: str) -> tuple[str, ...]:
    if not isinstance(end, str) or not re.fullmatch(r'20\d{2}(?:0[1-9]|1[0-2])', end):
        raise ValueError('unexpected_property_release_window')
    last = int(end[:4]) * 12 + int(end[4:]) - 1
    return tuple(f'{month // 12:04d}{month % 12 + 1:02d}' for month in range(last - 11, last + 1))


def _release_sale_window(property_root: Path) -> tuple[str, ...]:
    manifest = json.loads((property_root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('kind') != 'property-release' or manifest.get('release_id') != property_root.name:
        raise ValueError('unexpected_property_release_window')
    period = manifest.get('period', {})
    months = _twelve_months(period.get('latest_complete_month'))
    for key in ('from', 'to'):
        _twelve_months(period.get(key))
    if not period['from'] <= months[0] <= months[-1] <= period['to']:
        raise ValueError('incomplete_sale_marker_window')
    return months


def _sale_rows(checkpoint: Path, *, start: str, end: str, district_codes: set[str]):
    months = _twelve_months(end)
    if start != months[0] or len(district_codes) != 25 or any(not re.fullmatch(r'11\d{3}', code) for code in district_codes):
        raise ValueError('incomplete_seoul_sale_window')
    db = sqlite3.connect((checkpoint / 'checkpoint.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        jobs = [dict(row) for row in db.execute(
            "SELECT lawd_code,deal_month,status,pages FROM jobs WHERE lawd_code LIKE '11%' "
            "AND trade_type='sale' AND deal_month BETWEEN ? AND ? ORDER BY lawd_code,deal_month", (start, end))]
    finally:
        db.close()
    expected = {(code, month) for code in district_codes for month in months}
    if (len(jobs) != len(expected) or any(job['status'] != 'complete' for job in jobs)
            or {(job['lawd_code'], job['deal_month']) for job in jobs} != expected):
        raise ValueError('incomplete_seoul_sale_window')
    rows, hashes = [], []
    for job in jobs:
        for page in json.loads(job['pages']):
            if page.get('path') != f"raw/sale/{job['lawd_code']}/{job['deal_month']}/{page.get('sha256')}.xml":
                raise ValueError('invalid_molit_page_path')
            body = _read_hash(checkpoint / page['path'], page['sha256'], 8 * 1024**2)
            if len(body) != page['bytes']:
                raise ValueError('molit_page_size_mismatch')
            root = ET.fromstring(body)
            if root.findtext('.//resultCode') != '000':
                raise ValueError('molit_page_not_successful')
            parts = root.findall('.//item')
            if page['page_no'] == 1 and int(root.findtext('.//totalCount') or -1) != page['total_count']:
                raise ValueError('molit_total_mismatch')
            rows.extend((job['lawd_code'], {child.tag: (child.text or '').strip() for child in item}) for item in parts)
            hashes.append(page['sha256'])
    return rows, hashes


def _money_label(won: int) -> str:
    eok, remainder = divmod(won, 100_000_000)
    man = remainder // 10_000
    if remainder % 10_000:
        raise ValueError('non_man_won_sale_price')
    return f'{eok}억 {man:,}만' if eok and man else f'{eok}억' if eok else f'{man:,}만'


def recent_sale_markers(property_root: Path, linked_complexes: set[str]) -> dict[str, dict]:
    """Read verified release partitions; label a single latest eligible report, never a valuation."""
    release = property_root.name
    root = json.loads((property_root / 'regions.json').read_text(encoding='utf-8'))
    months = _release_sale_window(property_root)
    start, end = months[0], months[-1]
    if root['release_id'] != release:
        raise ValueError('unexpected_property_release_window')
    best: dict[str, dict] = {}
    checked = 0
    seen_codes = set()
    for region in root['regions']:
        code = region['lawd_code']
        if not re.fullmatch(r'11\d{3}', code):
            continue
        if code in seen_codes:
            raise ValueError('duplicate_seoul_district')
        seen_codes.add(code)
        descriptor = region['index']
        body = _read_hash(property_root / 'regions' / f'{code}.json', descriptor['sha256'], 4 * 1024**2)
        index = json.loads(body)
        if (index.get('release_id') != release or index.get('lawd_code') != code
                or index['period']['latest_complete_month'] != end):
            raise ValueError('unexpected_property_release_window')
        parts = [part for part in index['partitions'] if part['trade_type'] == 'sale' and start <= part['deal_month'] <= end]
        if (len(parts) != 12 or {part['deal_month'] for part in parts} != set(months)
                or any(part['status'] != 'complete' for part in parts)):
            raise ValueError('incomplete_sale_marker_window')
        for part in parts:
            count = 0
            for page, ref in enumerate(part['transactions']):
                expected = f'/data/property/{release}/transactions/{code}/{part["deal_month"]}-{page:03}.json'
                if ref['url'] != expected:
                    raise ValueError('unexpected_sale_marker_path')
                data = json.loads(_read_hash(property_root / 'transactions' / code / f'{part["deal_month"]}-{page:03}.json', ref['sha256'], 8 * 1024**2))
                if data['release_id'] != release or data['lawd_code'] != code or data['deal_month'] != part['deal_month']:
                    raise ValueError('sale_marker_partition_mismatch')
                for row in data['transactions']:
                    if row['trade_type'] != 'sale':
                        continue
                    count += 1
                    identity = row['complex_id']
                    if identity not in linked_complexes or row['quality'] != 'valid' or row['statistics_eligible'] is not True or row['cancellation'] != 'not_reported':
                        continue
                    price = row['price_krw']
                    contract = row['contract_date']
                    if not isinstance(price, int) or price <= 0 or not re.fullmatch(r'20\d{2}-\d{2}-\d{2}', contract) or contract[:7].replace('-', '') != part['deal_month']:
                        raise ValueError('invalid_sale_marker_value')
                    date.fromisoformat(contract)
                    if identity not in best or (contract, row['id']) > (best[identity]['contract_date'], best[identity]['source_transaction_id']):
                        best[identity] = {'contract_date': contract, 'source_transaction_id': row['id'], 'price_krw': price, 'area_m2': row['area_m2']}
            if count != part['source_rows']:
                raise ValueError(f'sale_marker_row_count_mismatch:{code}:{part["deal_month"]}:{count}:{part["source_rows"]}')
            checked += 1
    if len(seen_codes) != 25 or checked != 300:
        raise ValueError('incomplete_seoul_sale_markers')
    return best


def build(collection: Path, checkpoint: Path, property_root: Path, old_points: Path):
    old = json.loads(_read_hash(old_points, OLD_POINTS_SHA, 1024**2))
    rows, seoul_hashes = _seoul_rows(collection)
    release, codes, complexes = _property_complexes(property_root)
    months = _release_sale_window(property_root)
    sales, sale_hashes = _sale_rows(checkpoint, start=months[0], end=months[-1], district_codes=set(codes.values()))
    matches, audit = match_identities(rows, sales, codes, complexes)
    recent = recent_sale_markers(property_root, set(matches.values()))
    seen = set()
    for feature in old['features']:
        code = feature['properties']['kapt_code']
        if code in seen:
            raise ValueError('duplicate_existing_point')
        seen.add(code)
        identity = matches.get(code)
        if identity:
            feature['properties']['property_complex_id'] = identity
            feature['properties']['property_aptseq_join'] = 'unique_official_road_address_and_name'
            feature['properties']['property_release_id'] = release
            sale = recent.get(identity)
            if sale:
                feature['properties']['recent_sale_price_krw'] = sale['price_krw']
                feature['properties']['recent_sale_area_m2'] = sale['area_m2']
                feature['properties']['recent_sale_contract_date'] = sale['contract_date']
                feature['properties']['recent_sale_label'] = f'최근 신고 {_money_label(sale["price_krw"])} · {sale["area_m2"]}㎡'
    old['metadata']['property_aptseq_join'] = 'unique_official_road_address_and_name_for_linked_points_only'
    old['metadata']['property_release_id'] = release
    old['metadata']['property_join_source_page_sha256'] = seoul_hashes
    old['metadata']['property_join_molit_page_sha256_digest'] = _sha(canonical(sale_hashes))
    old['metadata']['property_join_audit'] = audit
    audit['published_linked_points'] = sum('property_complex_id' in x['properties'] for x in old['features'])
    audit['published_recent_sale_markers'] = sum('recent_sale_label' in x['properties'] for x in old['features'])
    if audit['published_linked_points'] > audit['matched'] or audit['published_linked_points'] < 500:
        raise ValueError('unexpected_published_link_count')
    return old, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--property-root', type=Path, required=True)
    parser.add_argument('--points', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result, audit = build(args.collection, args.checkpoint, args.property_root, args.points)
    body = canonical(result)
    if args.output.exists() and args.output.read_bytes() != body:
        raise ValueError('immutable_output_changed')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists():
        args.output.write_bytes(body)
    print(json.dumps({**audit, 'bytes': len(body), 'sha256': _sha(body)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
