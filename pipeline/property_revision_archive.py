"""Preserve original public records superseded or absent in newer snapshots.

Records in this archive are not current observations and never enter price
statistics. Absence in a completed snapshot is not proof of cancellation.
"""
from collections import defaultdict
import json
from .property_continuity import _source, _at
from .real_estate import RealEstateError, canonical_bytes, sha256

METADATA = {'id', 'source_input_sha256', 'retrieved_at'}
REVISION_FIELDS = {'registration_date', 'cancellation', 'cancellation_date',
                   'statistics_eligible', 'quality', 'issues'}

def core(row):
    return canonical_bytes({k:v for k,v in row.items() if k not in METADATA | REVISION_FIELDS})

def review_partition(previous, candidate):
    old={row['id']:row for row in previous};new={row['id']:row for row in candidate}
    if len(old)!=len(previous) or len(new)!=len(candidate):
        raise RealEstateError('revision_duplicate_id')
    available=defaultdict(list)
    for identity,row in sorted(new.items()):
        if identity not in old:available[core(row)].append(row)
    entries=[]
    for identity,row in sorted(old.items()):
        if identity in new:continue
        candidates=available[core(row)]
        replacement=candidates.pop(0) if candidates else None
        changed=sorted(k for k in row.keys()|replacement.keys() if k not in METADATA and row.get(k)!=replacement.get(k)) if replacement else []
        entries.append({'previous':row,'state':'report_fields_updated' if replacement else 'not_in_latest_snapshot',
            'candidate_id':replacement['id'] if replacement else None,'changed_fields':changed,
            'identity_relation':'matching_report_attributes_not_permanent_trade_id' if replacement else None})
    return entries

def build(previous, candidate, *, regions, expected_missing):
    old,old_hash,read_old,old_regions=_source(previous)
    new,new_hash,read_new,new_regions=_source(candidate)
    results=[];seen=set()
    def read_region(read,region):
        index=read(region['index']['url']);jobs={(p['deal_month'],p['trade_type']):p for p in index['partitions']}
        rows=defaultdict(list)
        for name in sorted({a['url'] for p in index['partitions'] for a in p['transactions']}):
            body=read(name)
            if body['lawd_code']!=index['lawd_code']:raise RealEstateError('revision_scope')
            for part in (body['months'] if body['kind']=='property-transaction-pack' else [body]):
                for row in part['transactions']:rows[(part['deal_month'],row['trade_type'])].append(row)
        for key,job in jobs.items():
            if len(rows[key])!=(job['source_rows'] or 0):raise RealEstateError('revision_partition_count')
        return jobs,rows
    for code in sorted(set(regions)):
        before,old_rows=read_region(read_old,old_regions[code]);after,new_rows=read_region(read_new,new_regions[code])
        for key,rows in old_rows.items():
            revisions=review_partition(rows,new_rows.get(key,[]))
            if not revisions:continue
            original=before[key];current=after.get(key,{})
            if current.get('status') not in ('complete','empty') or not current.get('retrieved_at') or _at(current['retrieved_at'])<=_at(original['retrieved_at']):
                raise RealEstateError('revision_not_newer_complete_snapshot')
            for entry in revisions:
                identity=entry['previous']['id']
                if identity in seen:raise RealEstateError('revision_duplicate_id')
                seen.add(identity)
                entry.update(lawd_code=code,deal_month=key[0],trade_type=key[1],candidate_retrieved_at=current['retrieved_at'])
                results.append(entry)
        print(json.dumps({'revision_region':code,'preserved_previous_records':len(results)}),flush=True)
    if len(results)!=expected_missing:raise RealEstateError('revision_missing_count')
    return {'schema_version':1,'kind':'property-public-revision-archive','previous_release':old['release_id'],
        'property_release_id':new['release_id'],'previous_publication_sha256':old_hash,
        'candidate_publication_sha256':new_hash,'statistics_policy':'archive_excluded_from_current_statistics',
        'absence_policy':'not_in_latest_snapshot_is_not_cancellation','records':results,
        'audit':{'preserved_ids':len(results),'preserved_id_sha256':sha256(canonical_bytes(sorted(seen))),
                 'source_calls':0,'ledger_writes':0}}
