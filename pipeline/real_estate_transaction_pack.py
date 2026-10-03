"""Pack audited monthly transaction assets without deleting the original release.

The derived release keeps V1 partition reference lists, but a reference may point
at a bounded ``property-transaction-pack`` containing several contract months.
Clients must unwrap their requested month before parsing the existing row schema.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from .real_estate import RealEstateError, canonical_bytes, sha256, _reject_links
from .real_estate_complex_summary import _checked_publication
from .real_estate_publish import checked_read, MAX_ASSET

POLICY = 'property-transaction-month-pack-v1'
TARGET_ASSET = 4 * 1024**2


def build(publication_path, output_root, *, reserve_bytes=30 * 1024**3, target_bytes=TARGET_ASSET):
    if type(reserve_bytes) is not int or reserve_bytes < 0 or type(target_bytes) is not int or not 1024 <= target_bytes <= TARGET_ASSET:
        raise RealEstateError('transaction_pack_invalid_budget')
    source_root, source, files, receipt_hash = _checked_publication(publication_path)
    old_release = source['release_id']
    identity = {'policy': POLICY, 'publication_sha256': receipt_hash}
    if target_bytes != TARGET_ASSET:
        identity['target_bytes'] = target_bytes
    release = 'property-' + sha256(canonical_bytes(identity))[:16]
    output = Path(output_root).absolute(); _reject_links(output)
    if any(part.lower() in ('public', 'dist') for part in output.parts): raise RealEstateError('public_output_forbidden')
    output.mkdir(parents=True, exist_ok=True); final = output / release
    if final.exists():
        result = json.loads((final / 'publication.json').read_bytes())
        for row in result['files']:
            checked_read(final, {'path': row['path'], 'sha256': row['sha256'], 'bytes': row['byte_length']}, MAX_ASSET)
        return result
    if shutil.disk_usage(output).free < reserve_bytes + 5 * 1024**3:
        raise RealEstateError('disk_reserve')
    stage = Path(tempfile.mkdtemp(prefix='.transaction-pack-incomplete-', dir=output))
    old_prefix = 'data/property/' + old_release + '/'; prefix = 'data/property/' + release + '/'
    assets = []; verified = set()

    def read(name):
        if name not in files: raise RealEstateError('transaction_pack_missing_reference')
        verified.add(name)
        return json.loads(checked_read(source_root, files[name], MAX_ASSET))

    def emit(name, value):
        raw = canonical_bytes(value)
        if len(raw) > (target_bytes if name.startswith('transaction-packs/') else MAX_ASSET) or len(assets) >= 18_000: raise RealEstateError('transaction_pack_asset_budget')
        if shutil.disk_usage(stage).free - len(raw) - 4096 < reserve_bytes: raise RealEstateError('disk_reserve')
        path = prefix + name; target = stage / path; target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream: stream.write(raw)
        digest = sha256(raw); assets.append({'path': path, 'sha256': digest, 'byte_length': len(raw)})
        return {'url': '/' + path, 'sha256': digest, 'bytes': len(raw)}

    manifest = read(old_prefix + 'manifest.json')
    regions_body = read(manifest['regions']['url'].lstrip('/'))
    region_results = []; source_count = packed_count = 0
    input_ids = hashlib.sha256(); packed_ids = hashlib.sha256()
    for summary in regions_body['regions']:
        code = summary['lawd_code']
        region = read(summary['index']['url'].lstrip('/'))
        if region['lawd_code'] != code or region['release_id'] != old_release:
            raise RealEstateError('transaction_pack_region_mismatch')
        patterns = re.compile(r'^' + re.escape(old_prefix) + r'transactions/' + code + r'/(\d{6})-\d{3}\.json$')
        monthly = defaultdict(list); seen = set()
        for name in sorted(files):
            match = patterns.fullmatch(name)
            if not match: continue
            body = read(name)
            if body['kind'] != 'property-transactions' or body['deal_month'] != match[1] or body['lawd_code'] != code or body['release_id'] != old_release:
                raise RealEstateError('transaction_pack_partition_mismatch')
            for row in body['transactions']:
                identity = row.get('id')
                if not isinstance(identity, str) or not identity or identity in seen:
                    raise RealEstateError('transaction_pack_duplicate_identity')
                if row.get('lawd_code') != code: raise RealEstateError('transaction_pack_partition_mismatch')
                seen.add(identity); monthly[match[1]].append(row); source_count += 1
                input_ids.update(identity.encode('utf-8') + b'\n')
        refs = defaultdict(list); pack = defaultdict(list); size = 0; index = 0
        envelope = {'schema_version': 1, 'kind': 'property-transaction-pack',
                    'release_id': release, 'lawd_code': code, 'months': []}
        base_size = len(canonical_bytes(envelope))

        def flush():
            nonlocal pack, size, index, packed_count
            if not pack: return
            if index >= 10_000: raise RealEstateError('transaction_pack_asset_budget')
            months = [{'deal_month': month, 'transactions': values} for month, values in sorted(pack.items())]
            ref = emit(f'transaction-packs/{code}/{index:04d}.json', {**envelope, 'months': months})
            for month, values in sorted(pack.items()):
                trades = {row['trade_type'] for row in values}
                for trade in trades: refs[(month, trade)].append(ref)
                for row in values:
                    packed_ids.update(row['id'].encode('utf-8') + b'\n'); packed_count += 1
            pack = defaultdict(list); size = 0; index += 1

        for month, rows in sorted(monthly.items()):
            # Preserve source file + row order for a full identity digest check.
            for row in rows:
                row_size = len(canonical_bytes(row))
                overhead = (len(canonical_bytes({'deal_month': month, 'transactions': []})) + (1 if pack else 0)) if month not in pack else 1
                if base_size + size + overhead + row_size > target_bytes:
                    flush(); overhead = len(canonical_bytes({'deal_month': month, 'transactions': []}))
                if base_size + overhead + row_size > target_bytes: raise RealEstateError('transaction_pack_single_row_budget')
                pack[month].append(row); size += overhead + row_size
            # Normal empty tasks require no pack; their empty status remains.
        flush()
        partitions = []
        for partition in region['partitions']:
            projected = {**partition, 'transactions': refs[(partition['deal_month'], partition['trade_type'])]}
            if partition['status'] not in ('complete', 'empty') and projected['transactions']:
                raise RealEstateError('transaction_pack_noncomplete_rows')
            partitions.append(projected)
        expected = sum(partition['source_rows'] or 0 for partition in region['partitions'])
        if expected != len(seen): raise RealEstateError('transaction_pack_source_count_mismatch')
        complex_ref = region.get('complexes')
        if complex_ref:
            complex_body = read(complex_ref['url'].lstrip('/'))
            complex_ref = emit(f'complexes/{code}.json', {**complex_body, 'release_id': release})
        index_ref = emit(f'regions/{code}.json', {**region, 'release_id': release, 'partitions': partitions, 'complexes': complex_ref})
        region_results.append({**summary, 'index': index_ref})
    if source_count != source['audit']['source_rows'] or source_count != packed_count or input_ids.hexdigest() != packed_ids.hexdigest():
        raise RealEstateError('transaction_pack_identity_mismatch')
    for name, descriptor in files.items():
        if name not in verified: checked_read(source_root, descriptor, MAX_ASSET)
    region_ref = emit('regions.json', {**regions_body, 'release_id': release, 'regions': region_results})
    manifest_ref = emit('manifest.json', {**manifest, 'release_id': release, 'regions': region_ref,
        'transaction_assets': {'layout': 'bounded-month-packs-v1', 'target_bytes': target_bytes,
            'legacy_row_schema': True, 'parent_property_release_id': old_release}})
    result = {'schema_version': 1, 'kind': 'property-publication', 'release_id': release,
        'property_release': {'path': manifest_ref['url'].lstrip('/'), 'sha256': manifest_ref['sha256'], 'release_id': release},
        'files': sorted(assets, key=lambda row: row['path']),
        'audit': {**source['audit'], 'policy': POLICY, 'parent_property_release_id': old_release,
            'parent_publication_sha256': receipt_hash, 'source_files_verified': len(files),
            'all_transaction_ids_preserved': True, 'source_transaction_identity_sha256': input_ids.hexdigest(),
            'source_rows': source_count, 'files': len(assets), 'bytes': sum(row['byte_length'] for row in assets),
            'source_calls': 0, 'ledger_writes': 0, 'public_release': False}}
    with (stage / 'publication.json').open('xb') as stream: stream.write(canonical_bytes(result))
    os.rename(stage, final)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publication', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = build(args.publication, args.output)
        print(json.dumps({'status': 'verified', 'property_release': result['property_release'], **result['audit']}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, 'real_estate_transaction_pack: ' + (error.code if isinstance(error, RealEstateError) else 'invalid_input') + '\n')


if __name__ == '__main__': main()
