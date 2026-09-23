"""Publish a small, attribution-preserving Seoul K-apt point candidate.

The official Seoul OpenAptInfo coordinates are joined to the independently
collected K-apt list by the same kaptCode. They are never joined to MOLIT aptSeq.
The provider page does not declare a CRS or point semantics, so output remains
explicitly provisional and must not be used as a building footprint or address.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re

SOURCE = 'https://data.seoul.go.kr/dataList/OA-15818/A/1/datasetView.do'
LICENSE = '공공누리 제1유형: 출처표시, 상업적 이용 및 변경 가능'


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def points(receipt: dict, raw_root: Path, kapt_context: dict) -> tuple[dict, dict]:
    if receipt.get('source') != SOURCE or receipt.get('dataset') != 'OpenAptInfo':
        raise ValueError('unexpected_source')
    pages = receipt.get('pages')
    if not isinstance(pages, list) or not 1 <= len(pages) <= 10:
        raise ValueError('invalid_pages')
    kapt_codes = {row['kapt_code'] for row in kapt_context['complexes']}
    if len(kapt_codes) != len(kapt_context['complexes']):
        raise ValueError('duplicate_kapt_registry')
    rows, hashes = [], []
    for page in pages:
        digest = page['sha256']
        if not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('invalid_raw_hash')
        path = raw_root / (digest + '.json')
        if path.is_symlink() or not path.is_file():
            raise ValueError('missing_raw_page')
        body = path.read_bytes()
        if len(body) != page['bytes'] or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError('raw_page_hash_mismatch')
        table = json.loads(body)['OpenAptInfo']
        part = table['row']
        if table['RESULT']['CODE'] != 'INFO-000' or len(part) != page['rows']:
            raise ValueError('provider_page_mismatch')
        rows.extend(part)
        hashes.append(digest)
    if len(rows) != receipt['declared_rows'] or len(rows) != receipt['collected_rows']:
        raise ValueError('provider_total_mismatch')
    seen, features = set(), []
    missing = unmatched = invalid = 0
    for row in rows:
        code = row['APT_CD']
        if not isinstance(code, str) or not re.fullmatch(r'[A-Z][0-9]{8,12}', code) or code in seen:
            raise ValueError('invalid_or_duplicate_kapt_code')
        seen.add(code)
        if code not in kapt_codes:
            unmatched += 1
            continue
        try:
            lon, lat = float(row['XCRD']), float(row['YCRD'])
        except (TypeError, ValueError, KeyError):
            missing += 1
            continue
        if not (math.isfinite(lon) and math.isfinite(lat) and
                126.6 <= lon <= 127.4 and 37.3 <= lat <= 37.8):
            invalid += 1
            continue
        name = row.get('APT_NM')
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValueError('invalid_apartment_name')
        households = row.get('TNOHSH')
        households = (int(households) if isinstance(households, (int, float))
                      and math.isfinite(households) and households >= 0
                      and households == int(households) else None)
        features.append({'type': 'Feature', 'id': code,
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': {'kapt_code': code, 'name': name.strip(),
                'households': households, 'source_id': 'seoul-openaptinfo',
                'coordinate_status': 'provider_xy_crs_unconfirmed',
                'property_aptseq_join': 'not_established'}})
    features.sort(key=lambda row: row['id'])
    result = {'type': 'FeatureCollection', 'features': features,
        'metadata': {'source': SOURCE, 'license': LICENSE,
            'retrieved_at': receipt['retrieved_at'], 'raw_page_sha256': hashes,
            'kapt_list_retrieved_until': kapt_context['retrieved_until'],
            'identity': 'exact_kapt_code_in_both_official_sources',
            'coordinate_status': 'provider_xy_crs_unconfirmed',
            'point_semantics': 'not_declared_by_provider',
            'property_aptseq_join': 'not_established'}}
    audit = {'source_rows': len(rows), 'matched_points': len(features),
             'not_in_kapt_list': unmatched, 'matched_missing_coordinate': missing,
             'matched_invalid_coordinate': invalid,
             'source_record_ids_unique': len(seen) == len(rows)}
    if len(features) + unmatched + missing + invalid != len(rows):
        raise ValueError('row_partition_mismatch')
    return result, audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, required=True)
    parser.add_argument('--kapt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads((args.collection / 'collection-receipt.json').read_text(encoding='utf-8'))
    kapt = json.loads(args.kapt.read_text(encoding='utf-8'))
    result, audit = points(receipt, args.collection / 'raw', kapt)
    body = canonical(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        if args.output.read_bytes() != body:
            raise ValueError('immutable_output_changed')
    else:
        args.output.write_bytes(body)
    print(json.dumps({**audit, 'bytes': len(body),
                      'sha256': hashlib.sha256(body).hexdigest()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
