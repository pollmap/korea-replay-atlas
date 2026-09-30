"""Audited monthly complex summaries derived from one pinned property release.

No source requests, collection ledger writes or public pointer changes occur.
Missing months retain their publication status; a summary never substitutes a
different month or silently assigns a source row to a same-name complex.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .real_estate import RealEstateError, canonical_bytes, sha256, _reject_links
from .real_estate_publish import checked_read, median, MAX_ASSET

POLICY = 'property-complex-monthly-summary-v2-bounded-complex-packs'
TARGET_ASSET = 4 * 1024**2
COMPLEX_PACK = 512 * 1024
MAX_FILES = 18_000
MAX_SOURCE_FILES = 100_000  # private monthly input before free-publication packing
PYEONG_M2 = Decimal(400) / Decimal(121)
RELEASE = re.compile(r'^property-[a-f0-9]{16}$')
STATES = {'complete', 'empty', 'pending', 'partial', 'failed', 'source_unavailable'}


def _checked_publication(path):
    path = Path(path).absolute()
    _reject_links(path)
    body = path.read_bytes()
    value = json.loads(body)
    release = value.get('release_id')
    if (value.get('schema_version') != 1 or value.get('kind') != 'property-publication'
            or not isinstance(release, str) or not RELEASE.fullmatch(release)
            or value.get('audit', {}).get('raw_pages_reparsed') is not True
            or value.get('audit', {}).get('source_hashes_verified') is not True):
        raise RealEstateError('summary_source_not_audited')
    files = value.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_SOURCE_FILES:
        raise RealEstateError('summary_source_files_invalid')
    prefix = 'data/property/' + release + '/'
    indexed = {}
    for row in files:
        name = row.get('path') if isinstance(row, dict) else None
        if not isinstance(name, str) or not name.startswith(prefix) or name in indexed:
            raise RealEstateError('summary_source_path_invalid')
        indexed[name] = {'path': name, 'sha256': row.get('sha256'), 'bytes': row.get('byte_length')}
    return path.parent, value, indexed, sha256(body)


def _area(value):
    if not isinstance(value, str) or len(value) > 24:
        raise RealEstateError('summary_invalid_area')
    try:
        area = Decimal(value)
    except InvalidOperation:
        raise RealEstateError('summary_invalid_area') from None
    if not area.is_finite() or not 0 < area <= 100_000:
        raise RealEstateError('summary_invalid_area')
    return format(area.normalize(), 'f'), area


def _money(value, *, required=False):
    if value is None and not required:
        return None
    if type(value) is not int or not 0 <= value <= 9_007_199_254_740_991:
        raise RealEstateError('summary_invalid_money')
    return value


def _rounded(value):
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_EVEN))


def summarize_rows(rows):
    """Group exact exclusive areas; repeated but separately identified reports count."""
    groups = defaultdict(list)
    audit = {'source_rows': 0, 'eligible_rows': 0, 'grouped_rows': 0,
             'unlinked_eligible_rows': 0, 'excluded_rows': 0, 'unknown_rent_rows': 0}
    for row in rows:
        audit['source_rows'] += 1
        if row.get('statistics_eligible') is not True:
            audit['excluded_rows'] += 1
            continue
        audit['eligible_rows'] += 1
        identity = row.get('complex_id')
        if identity is None:
            audit['unlinked_eligible_rows'] += 1
            continue
        if (not isinstance(identity, str) or not re.fullmatch(r'molit-apt:\d{5}:[A-Za-z0-9_-]{1,100}', identity)
                or row.get('trade_type') not in ('sale', 'rent')
                or not isinstance(row.get('contract_date'), str)
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['contract_date'])):
            raise RealEstateError('summary_invalid_identity')
        area, _ = _area(row.get('area_m2'))
        month = row['contract_date'][:7].replace('-', '')
        if row['trade_type'] == 'sale':
            kind = 'sale'
            _money(row.get('price_krw'), required=True)
        else:
            deposit, rent = row.get('deposit_krw'), row.get('monthly_rent_krw')
            if deposit is None or rent is None:
                audit['unknown_rent_rows'] += 1
                continue
            _money(deposit, required=True); _money(rent, required=True)
            kind = 'jeonse' if rent == 0 else 'monthly'
        groups[(month, identity, row['trade_type'], kind, area)].append(row)
        audit['grouped_rows'] += 1
    result = []
    for (month, identity, trade, kind, area), records in sorted(groups.items()):
        records.sort(key=lambda r: (r['contract_date'], r['retrieved_at'], r['id']))
        last = records[-1]
        prices = [r['price_krw'] for r in records] if trade == 'sale' else []
        deposits = [r['deposit_krw'] for r in records] if trade == 'rent' else []
        rents = [r['monthly_rent_krw'] for r in records] if trade == 'rent' else []
        basis = prices if trade == 'sale' else deposits
        divisor = Decimal(area)
        units = [_rounded(Decimal(price) / divisor) for price in basis]
        pyeong_units = [_rounded(Decimal(price) * PYEONG_M2 / divisor) for price in basis]
        result.append({'complex_id': identity, 'deal_month': month, 'trade_type': trade,
            'rent_kind': kind, 'area_m2': area, 'transaction_count': len(records),
            'latest_transaction_id': last['id'], 'latest_contract_date': last['contract_date'],
            'latest_retrieved_at': last['retrieved_at'],
            'latest_price_krw': last['price_krw'] if trade == 'sale' else None,
            'latest_deposit_krw': last['deposit_krw'] if trade == 'rent' else None,
            'latest_monthly_rent_krw': last['monthly_rent_krw'] if trade == 'rent' else None,
            'median_price_krw': median(prices), 'min_price_krw': min(prices) if prices else None,
            'max_price_krw': max(prices) if prices else None,
            'median_deposit_krw': median(deposits), 'median_monthly_rent_krw': median(rents),
            'price_basis': 'sale_price' if trade == 'sale' else 'deposit',
            'median_price_per_m2_krw': median(units),
            'median_price_per_pyeong_krw': median(pyeong_units)})
    if sum(row['transaction_count'] for row in result) != audit['grouped_rows']:
        raise RealEstateError('summary_group_count_mismatch')
    return result, audit


def build(publication_path, output_root, *, reserve_bytes=30 * 1024**3, target_bytes=TARGET_ASSET, max_files=MAX_FILES):
    if type(reserve_bytes) is not int or reserve_bytes < 0 or type(target_bytes) is not int or not 1024 <= target_bytes <= TARGET_ASSET:
        raise RealEstateError('summary_invalid_budget')
    if type(max_files) is not int or not MAX_FILES <= max_files <= 100_000:
        raise RealEstateError('summary_invalid_budget')
    source_root, publication, files, receipt_hash = _checked_publication(publication_path)
    release = publication['release_id']
    output = Path(output_root).absolute(); _reject_links(output)
    if any(part.lower() in ('public', 'dist') for part in output.parts):
        raise RealEstateError('public_output_forbidden')
    output.mkdir(parents=True, exist_ok=True)
    identity = {'policy': POLICY, 'publication_sha256': receipt_hash}
    if target_bytes != TARGET_ASSET:
        identity['target_bytes'] = target_bytes
    summary_id = 'summary-' + sha256(canonical_bytes(identity))[:16]
    final = output / summary_id
    if final.exists():
        prior = json.loads((final / 'publication.json').read_bytes())
        if not isinstance(prior.get('files'), list) or len(prior['files']) > max_files:
            raise RealEstateError('summary_asset_budget')
        for row in prior['files']:
            checked_read(final, {'path': row['path'], 'sha256': row['sha256'], 'bytes': row['byte_length']}, MAX_ASSET)
        return prior
    if shutil.disk_usage(output).free < reserve_bytes + 512 * 1024**2:
        raise RealEstateError('disk_reserve')
    stage = Path(tempfile.mkdtemp(prefix='.summary-incomplete-', dir=output))
    prefix = 'data/property-summary/' + summary_id
    assets = []

    def read(name):
        if name not in files:
            raise RealEstateError('summary_source_reference_missing')
        return json.loads(checked_read(source_root, files[name], MAX_ASSET))

    def emit(name, value):
        raw = canonical_bytes(value)
        if len(raw) > target_bytes or len(assets) >= max_files:
            raise RealEstateError('summary_asset_budget')
        if shutil.disk_usage(stage).free - len(raw) - 4096 < reserve_bytes:
            raise RealEstateError('disk_reserve')
        path = prefix + '/' + name
        dest = stage / path; dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('xb') as stream: stream.write(raw)
        digest = sha256(raw)
        assets.append({'path': path, 'sha256': digest, 'byte_length': len(raw)})
        return {'url': '/' + path, 'sha256': digest, 'bytes': len(raw)}

    source_prefix = 'data/property/' + release + '/'
    manifest = read(source_prefix + 'manifest.json')
    if manifest.get('release_id') != release or manifest.get('kind') != 'property-release':
        raise RealEstateError('summary_source_manifest_invalid')
    regions_asset = manifest['regions']
    regions = read(regions_asset['url'].lstrip('/'))['regions']
    indexed_regions = []; total_audit = defaultdict(int); identity_hash = hashlib.sha256()
    verified_files = set()
    for region in regions:
        code = region['lawd_code']
        if not isinstance(code, str) or not re.fullmatch(r'\d{5}', code):
            raise RealEstateError('summary_invalid_region')
        region_body = read(region['index']['url'].lstrip('/'))
        if region_body.get('release_id') != release or region_body.get('lawd_code') != code:
            raise RealEstateError('summary_source_region_invalid')
        partitions = []
        for partition in region_body['partitions']:
            if partition['status'] not in STATES:
                raise RealEstateError('summary_source_state_invalid')
            projected = {key: partition.get(key) for key in ('deal_month', 'trade_type', 'status', 'source_rows', 'eligible_rows', 'retrieved_at')}
            if 'refresh' in partition: projected['refresh'] = partition['refresh']
            partitions.append(projected)
        pattern = re.compile(r'^' + re.escape(source_prefix) + r'transactions/' + code + r'/(\d{6})-\d{3}\.json$')
        packed_pattern = re.compile(r'^' + re.escape(source_prefix) + r'transaction-packs/' + code + r'/\d{4}\.json$')
        by_month = defaultdict(list); seen = set()
        for name in sorted(files):
            match = pattern.fullmatch(name)
            packed_match = packed_pattern.fullmatch(name)
            if not match and not packed_match: continue
            body = read(name); verified_files.add(name)
            if body.get('release_id') != release or body.get('lawd_code') != code:
                raise RealEstateError('summary_source_partition_invalid')
            if match:
                if body.get('kind') != 'property-transactions' or body.get('deal_month') != match[1]:
                    raise RealEstateError('summary_source_partition_invalid')
                body_months = [{'deal_month': match[1], 'transactions': body['transactions']}]
            else:
                if body.get('kind') != 'property-transaction-pack' or not isinstance(body.get('months'), list):
                    raise RealEstateError('summary_source_partition_invalid')
                body_months = body['months']
            body_seen = set()
            for monthly in body_months:
                month = monthly.get('deal_month')
                if not isinstance(month, str) or not re.fullmatch(r'\d{6}', month) or month in body_seen:
                    raise RealEstateError('summary_source_partition_invalid')
                body_seen.add(month)
                for row in monthly['transactions']:
                    identity = row.get('id')
                    if not isinstance(identity, str) or not identity or len(identity) > 160 or identity in seen:
                        raise RealEstateError('summary_source_duplicate_identity')
                    seen.add(identity); identity_hash.update(identity.encode('utf-8') + b'\n')
                    if row.get('lawd_code') != code or row.get('trade_type') not in ('sale', 'rent'):
                        raise RealEstateError('summary_source_partition_invalid')
                    if row.get('statistics_eligible') is True and row['contract_date'][:7].replace('-', '') != month:
                        raise RealEstateError('summary_contract_month_mismatch')
                    by_month[month].append(row)
        references = []; complex_rows = []
        region_audit = defaultdict(int)
        for month, records in sorted(by_month.items()):
            rows, audit = summarize_rows(records)
            complex_rows.extend(rows)
            for key, count in audit.items(): region_audit[key] += count; total_audit[key] += count
            chunk = []; index = 0
            envelope = {'schema_version': 1, 'kind': 'property-complex-summaries',
                'property_release_id': release, 'summary_release_id': summary_id,
                'lawd_code': code, 'deal_month': month, 'rows': []}
            base_size = len(canonical_bytes(envelope)); chunk_size = base_size
            for row in rows:
                row_size = len(canonical_bytes(row)) + (1 if chunk else 0)
                if chunk_size + row_size > target_bytes:
                    if not chunk: raise RealEstateError('summary_single_row_budget')
                    references.append({'deal_month': month, **emit(f'rows/{code}/{month}-{index:03d}.json', {**envelope, 'rows': chunk})})
                    index += 1; chunk = []; chunk_size = base_size; row_size -= 1
                    if chunk_size + row_size > target_bytes: raise RealEstateError('summary_single_row_budget')
                chunk.append(row)
                chunk_size += row_size
            if chunk:
                references.append({'deal_month': month, **emit(f'rows/{code}/{month}-{index:03d}.json', {
                    'schema_version': 1, 'kind': 'property-complex-summaries', 'property_release_id': release,
                    'summary_release_id': summary_id, 'lawd_code': code, 'deal_month': month, 'rows': chunk})})
        if sum(p['source_rows'] or 0 for p in partitions) != region_audit['source_rows']:
            raise RealEstateError('summary_source_count_mismatch')
        expected_eligible = sum(p['eligible_rows'] or 0 for p in partitions)
        if expected_eligible != region_audit['eligible_rows']:
            raise RealEstateError('summary_eligible_count_mismatch')
        # An apartment's history should not require the unrelated apartments in
        # every monthly regional tile. Pack by complex ID, preserve all areas,
        # and reference a small bounded pack from each participating complex.
        complex_rows.sort(key=lambda row: (row['complex_id'], row['deal_month'], row['trade_type'], row['rent_kind'], row['area_m2']))
        complex_chunks = defaultdict(list); pack = []; pack_size = 0; pack_index = 0
        pack_limit = min(target_bytes, COMPLEX_PACK)
        pack_envelope = {'schema_version': 1, 'kind': 'property-complex-summary-pack',
            'property_release_id': release, 'summary_release_id': summary_id,
            'lawd_code': code, 'from_month': None, 'to_month': None, 'rows': []}
        pack_base = len(canonical_bytes(pack_envelope)) + 12  # null -> two 6-digit strings

        def flush_pack():
            nonlocal pack, pack_size, pack_index
            if not pack: return
            if pack_index >= 10_000: raise RealEstateError('summary_asset_budget')
            first_month = min(row['deal_month'] for row in pack)
            last_month = max(row['deal_month'] for row in pack)
            asset = emit(f'complexes/{code}/{pack_index:04d}.json', {**pack_envelope,
                'from_month': first_month, 'to_month': last_month, 'rows': pack})
            if asset['bytes'] > pack_limit: raise RealEstateError('summary_complex_pack_budget')
            for identity in sorted({row['complex_id'] for row in pack}):
                selected = [row['deal_month'] for row in pack if row['complex_id'] == identity]
                complex_chunks[identity].append({'from_month': min(selected), 'to_month': max(selected), **asset})
            pack_index += 1; pack = []; pack_size = 0

        for row in complex_rows:
            size = len(canonical_bytes(row)) + (1 if pack else 0)
            if pack_base + pack_size + size > pack_limit:
                flush_pack(); size = len(canonical_bytes(row))
            if pack_base + size > pack_limit: raise RealEstateError('summary_single_row_budget')
            pack.append(row); pack_size += size
        flush_pack()
        ref = emit(f'regions/{code}.json', {'schema_version': 1, 'kind': 'property-complex-summary-region',
            'property_release_id': release, 'summary_release_id': summary_id, 'lawd_code': code,
            'partitions': partitions, 'summaries': references, 'complex_chunks': dict(complex_chunks),
            'audit': dict(region_audit)})
        indexed_regions.append({'lawd_code': code, **ref})
    if total_audit['source_rows'] != publication['audit']['source_rows']:
        raise RealEstateError('summary_source_count_mismatch')
    # Audit all input files, including non-transaction catalog and identity files.
    for name, descriptor in files.items():
        if name not in verified_files: checked_read(source_root, descriptor, MAX_ASSET)
    audit = {**dict(total_audit), 'source_files_verified': len(files),
             'source_transaction_identity_sha256': identity_hash.hexdigest(),
             'source_publication_sha256': receipt_hash,
             'formula': 'exclusive_reported_area; integer_krw; half_even_unit_rounding; row_median',
             'unknown_rent_policy': 'not_grouped_without_both_reported_money_fields',
             'map_position_join': False, 'source_calls': 0, 'ledger_writes': 0, 'public_release': False}
    entry = emit('manifest.json', {'schema_version': 1, 'kind': 'property-complex-summary-release',
        'summary_release_id': summary_id, 'property_release_id': release, 'source_publication_sha256': receipt_hash,
        'period': manifest['period'], 'regions': indexed_regions, 'audit': audit})
    result = {'schema_version': 1, 'kind': 'property-complex-summary-publication',
        'summary_release_id': summary_id, 'property_release_id': release,
        'summary_release': {'path': entry['url'].lstrip('/'), 'sha256': entry['sha256'], 'summary_release_id': summary_id},
        'files': sorted(assets, key=lambda row: row['path']),
        'audit': {**audit, 'files': len(assets), 'bytes': sum(row['byte_length'] for row in assets)}}
    with (stage / 'publication.json').open('xb') as stream: stream.write(canonical_bytes(result))
    os.rename(stage, final)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--private-packing-input', action='store_true',
                        help='Allow up to 100000 private intermediate files; the packed publication still requires its normal deployment file audit.')
    args = parser.parse_args()
    try:
        result = build(args.publication, args.output, max_files=100_000 if args.private_packing_input else MAX_FILES)
        print(json.dumps({'status': 'verified', 'summary_release': result['summary_release'],
                          'property_release_id': result['property_release_id'], **result['audit']}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, 'real_estate_complex_summary: ' + (error.code if isinstance(error, RealEstateError) else 'invalid_input') + '\n')


if __name__ == '__main__': main()
