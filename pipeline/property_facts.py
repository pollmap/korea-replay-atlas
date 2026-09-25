"""Build public apartment facts from hash-checked Seoul records and audited IDs."""
from __future__ import annotations
import argparse
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
from .property_point_join import _seoul_rows
from .seoul_apartments import SOURCE, LICENSE, canonical

TEXT_FIELDS = {'road_address': 'APT_RDN_ADDR', 'heating': 'MN_MTHD',
               'corridor': 'ROAD_TYPE', 'management': 'MNG_MTHD', 'builder': 'BLDR'}
COUNT_FIELDS = {'households': 'TNOHSH', 'buildings': 'WHOL_DONG_CNT', 'parking': 'PRK_CNTOM'}

def text_value(value):
    if value is None or value == '': return None
    if not isinstance(value, str) or len(value) > 300 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid_fact_text')
    return value.strip() or None

def count_value(value):
    if value is None or value == '': return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value != int(value) or not 0 <= value <= 1_000_000:
        return None
    return int(value)

def day_value(value):
    if not value: return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2}\.0)?', value): return None
    try: return date.fromisoformat(value[:10]).isoformat()
    except ValueError: return None

def normalize(row):
    # Public field allowlist intentionally omits phone, fax and contact records.
    return {**{key: text_value(row.get(field)) for key, field in TEXT_FIELDS.items()},
            **{key: count_value(row.get(field)) for key, field in COUNT_FIELDS.items()},
            'approved_on': day_value(row.get('USE_APRV_YMD')),
            'provider_updated_on': day_value(row.get('MDFCN_YMD'))}

def build(collection: Path, navigation: Path, manifest: Path):
    rows, hashes = _seoul_rows(collection)
    raw = navigation.read_bytes()
    reference = json.loads(manifest.read_text(encoding='utf-8'))
    if len(raw) != reference['bytes'] or hashlib.sha256(raw).hexdigest() != reference['sha256']:
        raise ValueError('navigation_hash_mismatch')
    index = json.loads(raw)
    if index['property_release_id'] != reference['release_id'] or index['source_sha256'] != reference['source_sha256']:
        raise ValueError('navigation_release_mismatch')
    receipt = json.loads((collection/'collection-receipt.json').read_text(encoding='utf-8'))
    by_code = {row['APT_CD']: row for row in rows}
    if len(by_code) != len(rows): raise ValueError('duplicate_source_id')
    result, seen, codes = [], set(), set()
    for identity, code, _, _ in index['points']:
        if identity in seen or code in codes: raise ValueError('duplicate_identity')
        seen.add(identity); codes.add(code)
        row = by_code.get(code)
        if not row: raise ValueError('missing_identity_source')
        if row.get('CMPX_CLSF') not in ('아파트', '주상복합') or row.get('USE_YN') != 'Y': continue
        result.append({'complex_id': identity, 'kapt_code': code, 'classification': row['CMPX_CLSF'], **normalize(row)})
    result.sort(key=lambda row: row['complex_id'])
    return {'schema_version': 1, 'property_release_id': reference['release_id'],
            'source': SOURCE, 'license': LICENSE, 'retrieved_at': receipt['retrieved_at'],
            'source_page_sha256': hashes, 'identity_source_sha256': reference['source_sha256'],
            'identity_method': 'existing_unique_official_road_address_and_name',
            'coordinate_verification': 'not_performed', 'rows': result}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, required=True)
    parser.add_argument('--navigation', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    result=build(args.collection,args.navigation,args.manifest)
    body=canonical(result)+b'\n'
    if args.output.exists() and args.output.read_bytes()!=body: raise ValueError('existing_output_differs')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if not args.output.exists(): args.output.write_bytes(body)
    print(json.dumps({'rows':len(result['rows']),'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}))

if __name__=='__main__': main()
