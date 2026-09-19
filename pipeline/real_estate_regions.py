"""Verified MOIS legal-dong registry, distinct from SGIS census geometry."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import re
import zipfile

from .real_estate import RealEstateError, canonical_bytes, sha256, utc_instant, _reject_links

SOURCE_URL = 'https://www.code.go.kr/etc/codeFullDown.do?codeseId=00002'
SOURCE_PAGE = 'https://www.code.go.kr/stdcodesrch/codeAllDownloadL.do'


def registry_from_zip(raw: bytes, *, retrieved_at: str):
    utc_instant(retrieved_at)
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= 16 * 1024**2:
        raise RealEstateError('legal_codes_size_limit')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if (len(entries) != 1 or entries[0].filename != '법정동코드 전체자료.txt'
                    or entries[0].file_size > 16 * 1024**2 or entries[0].flag_bits & 1
                    or (entries[0].external_attr >> 16) & 0o170000 == 0o120000):
                raise RealEstateError('invalid_legal_codes_archive')
            text = archive.read(entries[0]).decode('cp949')
    except (ValueError, UnicodeError, zipfile.BadZipFile, OSError):
        raise RealEstateError('invalid_legal_codes_archive') from None
    lines = text.splitlines()
    if not lines or lines[0] != '법정동코드\t법정동명\t폐지여부':
        raise RealEstateError('invalid_legal_codes_header')
    rows = {}
    for line in lines[1:]:
        parts = line.split('\t')
        if (len(parts) != 3 or not re.fullmatch(r'[0-9]{10}', parts[0])
                or not 1 <= len(parts[1]) <= 120 or parts[2] not in ('존재', '폐지')
                or parts[0] in rows or any(ord(c) < 32 for c in parts[1])):
            raise RealEstateError('invalid_legal_code_row')
        rows[parts[0]] = {'code': parts[0], 'name': parts[1],
                         'status': 'active' if parts[2] == '존재' else 'retired'}
    # Include only prefixes containing actual active town/dong/village rows. A parent
    # city with district children has no such rows of its own and is not double-counted.
    prefixes = sorted({c[:5] for c, r in rows.items()
        if r['status'] == 'active' and c[5:] != '00000'})
    regions = []
    for prefix in prefixes:
        top = rows.get(prefix + '00000')
        if top is None or top['status'] != 'active':
            raise RealEstateError('missing_legal_region_parent')
        regions.append({'lawd_code': prefix, 'name': top['name'], 'status': 'active',
                        'legal_code': top['code']})
    if not regions or len(regions) > 1000:
        raise RealEstateError('invalid_legal_region_count')
    # The download gives present/abolished status, not effective date intervals.
    # Preserve old prefixes as a visible gap instead of fabricating historical coverage.
    retired = sorted({c[:5] for c, r in rows.items() if r['status'] == 'retired'
                      and c[5:] != '00000'} - set(prefixes))
    return {'schema_version': 1, 'kind': 'molit-legal-region-registry',
        'source': {'id': 'mois-legal-dong-codes', 'page_url': SOURCE_PAGE,
                   'download_url': SOURCE_URL, 'sha256': sha256(raw), 'bytes': len(raw)},
        'retrieved_at': retrieved_at, 'regions': regions,
        'legal_dongs': sorted(rows.values(), key=lambda r: r['code']),
        'historical_coverage': 'current_codes_only_pending_effective_date_crosswalk',
        'retired_region_prefixes': retired,
        'audit': {'source_rows': len(rows), 'active_regions': len(regions),
                  'active_codes': sum(r['status'] == 'active' for r in rows.values()),
                  'retired_codes': sum(r['status'] == 'retired' for r in rows.values())}}


def load_registry(path):
    path = Path(path).absolute()
    _reject_links(path)
    if path.stat().st_size > 16 * 1024**2:
        raise RealEstateError('legal_registry_size_limit')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (value.get('schema_version') != 1 or value.get('kind') != 'molit-legal-region-registry'
            or value.get('source', {}).get('download_url') != SOURCE_URL
            or not re.fullmatch(r'[a-f0-9]{64}', value.get('source', {}).get('sha256', ''))
            or not isinstance(value.get('regions'), list) or not value['regions']):
        raise RealEstateError('invalid_legal_registry')
    utc_instant(value['retrieved_at'])
    codes = []
    for row in value['regions']:
        if (not re.fullmatch(r'[0-9]{5}', row.get('lawd_code', ''))
                or row.get('legal_code') != row['lawd_code'] + '00000'
                or row.get('status') != 'active' or not isinstance(row.get('name'), str)):
            raise RealEstateError('invalid_legal_registry')
        codes.append(row['lawd_code'])
    if len(codes) != len(set(codes)):
        raise RealEstateError('duplicate_legal_region')
    return value


def resolve_legal_dong(registry, lawd_code, source_code, source_name):
    """Validate a supplied code; name resolution is bounded to official region, never GPS."""
    candidates = [row for row in registry['legal_dongs'] if row['status'] == 'active'
        and row['code'].startswith(lawd_code) and row['code'][5:] != '00000'
        and row['code'].endswith('00')]
    if source_code:
        found = [row for row in candidates if row['code'] == source_code]
        return found[0]['code'] if len(found) == 1 else None
    if not source_name:
        return None
    found = [row for row in candidates if row['name'].endswith(' ' + source_name)]
    return found[0]['code'] if len(found) == 1 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip', required=True, type=Path)
    parser.add_argument('--retrieved-at', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        _reject_links(args.zip.absolute()); _reject_links(args.output.absolute())
        value = registry_from_zip(args.zip.read_bytes(), retrieved_at=args.retrieved_at)
        payload = canonical_bytes(value)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('xb') as handle:
            handle.write(payload)
        print(json.dumps({'status': 'verified', **value['audit'], 'sha256': sha256(payload)}))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"real_estate_regions: {error.code if isinstance(error, RealEstateError) else 'invalid_input'}\n")


if __name__ == '__main__':
    main()
