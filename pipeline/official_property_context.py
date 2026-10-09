"""Deterministic public school locations and Seoul fee statements. No name joins."""
import argparse
import csv
import hashlib
import io
import json
import re
from pathlib import Path

SCHOOL_SOURCE = 'https://www.data.go.kr/data/15021148/standard.do'
FEE_SOURCE = 'https://data.seoul.go.kr/dataList/OA-15822/S/1/datasetView.do'
REGIONS = ('서울특별시 ', '인천광역시 ', '경기도 ', '대전광역시 ', '세종특별자치시 ',
           '충청남도 천안시 ', '충청남도 아산시 ', '충청북도 청주시 ', '부산광역시 ')
LEVELS = {'초등학교': 'elementary', '중학교': 'middle', '고등학교': 'high'}


def schools(pages):
    rows, seen = [], set()
    for row in pages:
        school_id = row['SCHOOL_ID']
        if not re.fullmatch(r'B[0-9]{9}', school_id) or school_id in seen:
            raise ValueError('school_identity')
        seen.add(school_id)
        address = row['RDNMADR'] or row['LNMADR']
        if not address.startswith(REGIONS):
            continue
        lon, lat = float(row['LONGITUDE']), float(row['LATITUDE'])
        if not (124 <= lon <= 132 and 32 <= lat <= 39.5) or row['OPER_STTUS'] != '운영':
            raise ValueError('school_location')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['REFERENCE_DATE']):
            raise ValueError('school_reference_date')
        rows.append([school_id, row['SCHOOL_NM'], LEVELS[row['SCHOOL_SE']], address,
                     lon, lat, row['REFERENCE_DATE']])
    return sorted(rows)


def fees(body, month):
    reader = csv.reader(io.StringIO(body.decode('cp949'), newline=''))
    if next(reader) != ['아파트명', '아파트코드', '비용명', '년월일', '금액']:
        raise ValueError('fee_schema')
    grouped = {}
    for row in reader:
        if not row:
            continue
        if len(row) not in (5, 6) or len(row) == 6 and row[5] != '':
            raise ValueError('fee_schema')
        name, code, label, period, amount = row[:5]
        if not re.fullmatch(r'[AB]\d{8,12}', code) or period != month or not re.fullmatch(r'-?\d+', amount):
            raise ValueError('fee_value')
        value = int(amount)
        if abs(value) > 9_007_199_254_740_991:
            raise ValueError('fee_amount')
        group = grouped.setdefault(code, {'name': name, 'items': {}})
        if group['name'] != name or label in group['items']:
            raise ValueError('fee_duplicate')
        group['items'][label] = value  # Preserve zeros and signed adjustments.
    return [[code, group['name'], sorted(group['items'].items())] for code, group in sorted(grouped.items())]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--school-pages', nargs='+', type=Path, required=True)
    parser.add_argument('--fees', type=Path, required=True)
    parser.add_argument('--month', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'\d{4}(0[1-9]|1[0-2])', args.month):
        parser.error('invalid month')
    pages = [p.read_bytes() for p in args.school_pages]
    body = args.fees.read_bytes()
    output = {
        'schools': {'schema_version': 1, 'source': SCHOOL_SOURCE, 'license': '이용허락범위 제한 없음',
                    'source_sha256': [hashlib.sha256(page).hexdigest() for page in pages],
                    'source_rows': sum(len(json.loads(page)) for page in pages),
                    'rows': schools([row for page in pages for row in json.loads(page)])},
        'fees': {'schema_version': 1, 'source': FEE_SOURCE, 'license': '공공누리 제1유형',
                 'source_sha256': hashlib.sha256(body).hexdigest(), 'month': args.month,
                 'unit': 'KRW', 'semantics': 'reported_complex_line_items', 'rows': fees(body, args.month)},
    }
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in output.items():
        target = args.output / f'official-{name}-20261010.json'
        payload = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        target.write_bytes(payload)
        print(json.dumps({'asset': target.name, 'rows': len(value['rows']), 'bytes': len(payload),
                          'sha256': hashlib.sha256(payload).hexdigest()}))


if __name__ == '__main__':
    main()
