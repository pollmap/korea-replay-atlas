"""Pack audited summary months while retaining the existing complex lookup.

The original summary and property releases remain immutable. Monthly reference
lists retain ``deal_month`` and may share a bounded multi-month JSON asset.
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
from .real_estate_publish import checked_read, MAX_ASSET
from .property_transport import encode

POLICY = 'property-summary-month-pack-v1'
TARGET_ASSET = 4 * 1024**2


def build(publication_path, output_root, *, reserve_bytes=2 * 1024**3, target_bytes=TARGET_ASSET, compressed_assets=False):
    if type(reserve_bytes) is not int or reserve_bytes < 0 or type(target_bytes) is not int or not 1024 <= target_bytes <= TARGET_ASSET:
        raise RealEstateError('summary_pack_invalid_budget')
    publication_path = Path(publication_path).absolute(); _reject_links(publication_path)
    raw = publication_path.read_bytes(); source = json.loads(raw); source_root = publication_path.parent
    old_summary = source.get('summary_release_id'); release = source.get('property_release_id')
    if (source.get('kind') != 'property-complex-summary-publication' or source.get('schema_version') != 1
            or not isinstance(old_summary, str) or not re.fullmatch(r'summary-[a-f0-9]{16}', old_summary)
            or not isinstance(release, str) or not re.fullmatch(r'property-[a-f0-9]{16}', release)
            or not isinstance(source.get('files'), list) or not 1 <= len(source['files']) <= 100_000):
        raise RealEstateError('summary_pack_source_invalid')
    old_prefix = 'data/property-summary/' + old_summary + '/'
    files = {}
    for row in source['files']:
        name = row.get('path')
        if not isinstance(name, str) or not name.startswith(old_prefix) or name in files:
            raise RealEstateError('summary_pack_source_invalid')
        files[name] = {'path': name, 'sha256': row.get('sha256'), 'bytes': row.get('byte_length'), **({'transport':row['transport']} if 'transport' in row else {})}
    identity = {'policy': POLICY, 'parent_publication_sha256': sha256(raw)}
    if type(compressed_assets) is not bool:raise RealEstateError('summary_pack_invalid_transport')
    if compressed_assets:identity['transport']='explicit-gzip-summary-v1'
    if target_bytes != TARGET_ASSET:
        identity['target_bytes'] = target_bytes
    summary_id = 'summary-' + sha256(canonical_bytes(identity))[:16]
    output = Path(output_root).absolute(); _reject_links(output)
    if any(part.lower() in ('public', 'dist') for part in output.parts): raise RealEstateError('public_output_forbidden')
    output.mkdir(parents=True, exist_ok=True); final = output / summary_id
    if final.exists():
        result = json.loads((final / 'publication.json').read_bytes())
        for row in result['files']:
            checked_read(final, {'path': row['path'], 'sha256': row['sha256'], 'bytes': row['byte_length'], **({'transport':row['transport']} if 'transport' in row else {})}, MAX_ASSET)
        return result
    if shutil.disk_usage(output).free < reserve_bytes + 512 * 1024**2: raise RealEstateError('disk_reserve')
    stage = Path(tempfile.mkdtemp(prefix='.summary-pack-incomplete-', dir=output))
    prefix = 'data/property-summary/' + summary_id + '/'; assets = []; verified = set(); copied = {}

    def read(name):
        if name not in files: raise RealEstateError('summary_pack_missing_reference')
        verified.add(name)
        return json.loads(checked_read(source_root, files[name], MAX_ASSET))

    def emit(name, value):
        payload=canonical_bytes(value)
        if len(payload)>target_bytes or len(assets)>=18_000:raise RealEstateError('summary_pack_asset_budget')
        compressed=compressed_assets and name.startswith(('month-packs/','complexes/'))
        encoded=encode(payload) if compressed else payload
        if shutil.disk_usage(stage).free-len(encoded)-4096<reserve_bytes:raise RealEstateError('disk_reserve')
        path=prefix+name;target=stage/path;target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream:stream.write(encoded)
        entry={'path':path,'sha256':sha256(encoded),'byte_length':len(encoded)}
        reference={'url':'/'+path,'sha256':sha256(payload),'bytes':len(payload)}
        if compressed:
            entry['transport']={'encoding':'gzip','decoded_sha256':sha256(payload),'decoded_bytes':len(payload)}
            reference['transport']={'encoding':'gzip','sha256':sha256(encoded),'bytes':len(encoded)}
        assets.append(entry);return reference

    manifest = read(old_prefix + 'manifest.json')
    region_refs = []; total_count = 0; groups_count = 0; input_digest = hashlib.sha256(); output_digest = hashlib.sha256()
    for region_ref in manifest['regions']:
        code = region_ref['lawd_code']; region = read(region_ref['url'].lstrip('/'))
        if region['lawd_code'] != code or region['property_release_id'] != release or region['summary_release_id'] != old_summary:
            raise RealEstateError('summary_pack_region_mismatch')
        monthly = defaultdict(list); source_names = set(); seen_keys = set()
        for ref in region['summaries']:
            name = ref['url'].lstrip('/')
            if name in source_names: continue
            source_names.add(name); body = read(name)
            if body.get('kind') != 'property-complex-summaries' or body.get('deal_month') != ref['deal_month'] or body.get('lawd_code') != code or body.get('property_release_id') != release:
                raise RealEstateError('summary_pack_source_month_mismatch')
            for row in body['rows']:
                key = (row['complex_id'], row['deal_month'], row['trade_type'], row['rent_kind'], row['area_m2'])
                if key in seen_keys or row['deal_month'] != ref['deal_month'] or type(row['transaction_count']) is not int or row['transaction_count'] < 1:
                    raise RealEstateError('summary_pack_duplicate_or_invalid_row')
                seen_keys.add(key); monthly[ref['deal_month']].append(row)
        references = defaultdict(list); pack = defaultdict(list); size = 0; index = 0
        envelope = {'schema_version': 1, 'kind': 'property-complex-summary-month-pack',
            'summary_release_id': summary_id, 'property_release_id': release, 'lawd_code': code, 'months': []}
        base_size = len(canonical_bytes(envelope))

        def flush():
            nonlocal pack, size, index
            if not pack: return
            if index >= 10_000: raise RealEstateError('summary_pack_asset_budget')
            body = {**envelope, 'months': [{'deal_month': month, 'rows': rows} for month, rows in sorted(pack.items())]}
            ref = emit(f'month-packs/{code}/{index:04d}.json', body)
            for month, rows in sorted(pack.items()):
                references[month].append({'deal_month': month, **ref})
                for row in rows: output_digest.update(canonical_bytes(row) + b'\n')
            pack = defaultdict(list); size = 0; index += 1

        for month, rows in sorted(monthly.items()):
            for row in rows:
                row_bytes = canonical_bytes(row); input_digest.update(row_bytes + b'\n')
                total_count += row['transaction_count']; groups_count += 1
                overhead = len(canonical_bytes({'deal_month': month, 'rows': []})) + (1 if pack else 0) if month not in pack else 1
                if base_size + size + overhead + len(row_bytes) > target_bytes:
                    flush(); overhead = len(canonical_bytes({'deal_month': month, 'rows': []}))
                if base_size + overhead + len(row_bytes) > target_bytes: raise RealEstateError('summary_pack_single_row_budget')
                pack[month].append(row); size += overhead + len(row_bytes)
        flush()
        complex_refs = {}
        for identity, refs in region.get('complex_chunks', {}).items():
            rewritten = []
            for ref in refs:
                name = ref['url'].lstrip('/')
                if name not in copied:
                    body = read(name)
                    if body.get('kind') != 'property-complex-summary-pack' or body.get('property_release_id') != release or body.get('lawd_code') != code:
                        raise RealEstateError('summary_pack_source_complex_mismatch')
                    copied[name] = emit(name.removeprefix(old_prefix), {**body, 'summary_release_id': summary_id})
                rewritten.append({'from_month': ref['from_month'], 'to_month': ref['to_month'], **copied[name]})
            complex_refs[identity] = rewritten
        result_ref = emit(f'regions/{code}.json', {**region, 'summary_release_id': summary_id,
            'summaries': [ref for month in sorted(references) for ref in references[month]], 'complex_chunks': complex_refs})
        region_refs.append({'lawd_code': code, **result_ref})
    if total_count != source['audit']['grouped_rows'] or input_digest.hexdigest() != output_digest.hexdigest():
        raise RealEstateError('summary_pack_identity_mismatch')
    for name, descriptor in files.items():
        if name not in verified: checked_read(source_root, descriptor, MAX_ASSET)
    audit = {**source['audit'], 'parent_summary_release_id': old_summary,
        'parent_summary_publication_sha256': sha256(raw), 'summary_groups_preserved': groups_count,
        'summary_group_identity_sha256': input_digest.hexdigest(), 'source_calls': 0, 'ledger_writes': 0, 'public_release': False}
    entry = emit('manifest.json', {**manifest, 'summary_release_id': summary_id, 'regions': region_refs,
        'monthly_assets': {'layout': 'bounded-month-packs-v1', 'target_bytes': target_bytes}, 'audit': audit})
    result = {'schema_version': 1, 'kind': 'property-complex-summary-publication', 'summary_release_id': summary_id,
        'property_release_id': release, 'summary_release': {'path': entry['url'].lstrip('/'), 'sha256': entry['sha256'], 'summary_release_id': summary_id},
        'files': sorted(assets, key=lambda row: row['path']), 'audit': {**audit, 'files': len(assets), 'bytes': sum(row['byte_length'] for row in assets)}}
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
        print(json.dumps({'status': 'verified', 'summary_release': result['summary_release'], **result['audit']}, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, 'real_estate_summary_month_pack: ' + (error.code if isinstance(error, RealEstateError) else 'invalid_input') + '\n')


if __name__ == '__main__': main()
