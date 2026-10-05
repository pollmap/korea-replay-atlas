"""Compare every previous public transaction ID with a pinned audited candidate.

Source revisions need review; added rows never compensate for missing IDs.
This read-only gate neither publishes nor modifies collection checkpoints.
"""
import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path

from .real_estate import RealEstateError, _reject_links, sha256
from .real_estate_complex_summary import _checked_publication
from .real_estate_publish import checked_read, MAX_ASSET
from .vps_runtime import write_json


def _source(filename):
    root, receipt, files, digest = _checked_publication(filename, allow_public_baseline=True)
    def read(name):
        name = name.lstrip('/')
        if name not in files:
            raise RealEstateError('continuity_missing_reference')
        value = json.loads(checked_read(root, files[name], MAX_ASSET))
        if value.get('release_id') != receipt['release_id']:
            raise RealEstateError('continuity_release_mismatch')
        return value
    manifest = read(receipt['property_release']['path'])
    if sha256((root / receipt['property_release']['path'].lstrip('/')).read_bytes()) != receipt['property_release']['sha256']:
        raise RealEstateError('continuity_manifest_hash')
    regions = read(manifest['regions']['url'])['regions']
    indexed = {r['lawd_code']: r for r in regions}
    if len(indexed) != len(regions):
        raise RealEstateError('continuity_duplicate_region')
    return receipt, digest, read, indexed


def _region(read, ref):
    code = ref['lawd_code']; body = read(ref['index']['url'])
    if body['lawd_code'] != code:
        raise RealEstateError('continuity_region_mismatch')
    jobs = {}; rows = defaultdict(dict); visited = set(); identities = set()
    for part in body['partitions']:
        key = (part['deal_month'], part['trade_type'])
        if key in jobs:
            raise RealEstateError('continuity_duplicate_partition')
        jobs[key] = part
        for asset in part['transactions']:
            name = asset['url']
            if name in visited: continue
            visited.add(name); document = read(name)
            if document.get('lawd_code') != code:
                raise RealEstateError('continuity_region_mismatch')
            if document['kind'] == 'property-transaction-pack':
                chunks = document['months']
            elif document['kind'] == 'property-transactions':
                chunks = [document]
            else:
                raise RealEstateError('continuity_transaction_kind')
            for chunk in chunks:
                for row in chunk['transactions']:
                    if row.get('lawd_code') != code or (row.get('contract_date') and row['contract_date'][:7].replace('-', '') != chunk['deal_month']):
                        raise RealEstateError('continuity_row_scope')
                    if row['id'] in identities:
                        raise RealEstateError('continuity_duplicate_identity')
                    identities.add(row['id'])
                    key = (chunk['deal_month'], row['trade_type'])
                    rows[key][row['id']] = row['source_input_sha256']
    for key, records in rows.items():
        if key not in jobs:
            raise RealEstateError('continuity_unindexed_rows')
    for key, part in jobs.items():
        count = part['source_rows']
        if (count is None and rows[key]) or (count is not None and count != len(rows[key])):
            raise RealEstateError('continuity_partition_count')
    return jobs, rows


def _at(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None: raise ValueError()
        return result
    except (AttributeError, TypeError, ValueError):
        raise RealEstateError('continuity_invalid_timestamp') from None


def compare(previous, candidate, *, progress=None):
    old, old_hash, read_old, old_regions = _source(previous)
    new, new_hash, read_new, new_regions = _source(candidate)
    result = {'schema_version': 1, 'previous': old['release_id'], 'candidate': new['release_id'],
              'previous_publication_sha256': old_hash, 'candidate_publication_sha256': new_hash,
              'old_rows': 0, 'new_rows': 0, 'retained_ids': 0, 'added_ids': 0, 'removed_ids': 0,
              'blockers': [], 'changed_jobs': [], 'public_release': False,
              'source_calls': 0, 'ledger_writes': 0}
    for code in sorted(old_regions.keys() | new_regions.keys()):
        before, old_rows = _region(read_old, old_regions[code]) if code in old_regions else ({}, {})
        after, new_rows = _region(read_new, new_regions[code]) if code in new_regions else ({}, {})
        result['new_rows'] += sum(len(rows) for rows in new_rows.values())
        if code in old_regions and code not in new_regions:
            result['blockers'].append({'region': code, 'reason': 'region_lost'})
        for key, job in before.items():
            old_ids = old_rows.get(key, {}); new_ids = new_rows.get(key, {})
            missing = old_ids.keys() - new_ids.keys(); added = new_ids.keys() - old_ids.keys()
            result['old_rows'] += len(old_ids)
            result['retained_ids'] += len(old_ids.keys() & new_ids.keys())
            result['removed_ids'] += len(missing)
            target = after.get(key, {}); reason = None
            if job['status'] in ('complete', 'empty'):
                if target.get('status') not in ('complete', 'empty'):
                    reason = 'completed_partition_lost'
                elif not target.get('retrieved_at') or _at(target['retrieved_at']) < _at(job['retrieved_at']):
                    reason = 'older_snapshot'
            if not reason and missing:
                if not target.get('retrieved_at') or _at(target['retrieved_at']) <= _at(job['retrieved_at']):
                    reason = 'missing_without_newer_snapshot'
                elif {old_ids[i] for i in missing} & set(new_ids.values()):
                    reason = 'same_source_lost_identity'
            if reason:
                result['blockers'].append({'region': code, 'month': key[0], 'trade': key[1], 'reason': reason})
            if missing:
                result['changed_jobs'].append({'region': code, 'month': key[0], 'trade': key[1],
                    'removed': len(missing), 'added': len(added), 'previous_at': job.get('retrieved_at'),
                    'candidate_at': target.get('retrieved_at'), 'requires_source_revision_review': not bool(reason)})
        if progress:
            progress({'region': code, 'retained_ids': result['retained_ids'], 'removed_ids': result['removed_ids'],
                      'blockers': len(result['blockers'])})
    if result['old_rows'] != old['audit']['source_rows'] or result['new_rows'] != new['audit']['source_rows']:
        raise RealEstateError('continuity_release_count')
    result['added_ids'] = result['new_rows'] - result['retained_ids']
    result['checked_all_previous_ids'] = True
    result['automatic_transition_eligible'] = not result['blockers'] and not result['removed_ids']
    result['requires_source_revision_review'] = bool(result['changed_jobs'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    try:
        _reject_links(args.report.absolute())
        if any(args.report.absolute().is_relative_to(p.absolute().parent) for p in (args.previous, args.candidate)):
            raise RealEstateError('continuity_report_overlaps_input')
        if any(part.lower() in ('public', 'dist') for part in args.report.absolute().parts):
            raise RealEstateError('public_output_forbidden')
        result = compare(args.previous, args.candidate, progress=lambda value: print(json.dumps(value), flush=True))
        write_json(args.report, result)
        print(json.dumps({k:v for k,v in result.items() if k not in ('changed_jobs','blockers')}))
        if not result['automatic_transition_eligible']: raise SystemExit(2)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, 'property_continuity: ' + (error.code if isinstance(error, RealEstateError) else 'invalid_input') + '\n')


if __name__ == '__main__': main()
