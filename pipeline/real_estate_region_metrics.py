"""Small, exact region statistics for the published PC map filter presets.

Derived only from a hash-verified publication. No source calls, ledger writes,
median-of-medians, or guesses for missing months. Each region is processed alone.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
from .real_estate import RealEstateError, canonical_bytes, sha256
from .real_estate_publish import checked_read, median, MAX_ASSET
from .real_estate_complex_summary import _checked_publication, _area, _money, _rounded

RANGES=(1,3,6,12,36,60,120,240)
AREAS=('', '84-band')
KINDS=('sale','rent','jeonse','monthly')

def months(end, length):
    last=int(end[:4])*12+int(end[4:])-1
    return [f'{n//12:04d}{n%12+1:02d}' for n in range(last-length+1,last+1)]

def observations(rows):
    groups=defaultdict(list)
    for row in rows:
        if row.get('statistics_eligible') is not True: continue
        _, area=_area(row.get('area_m2'))
        trade=row['trade_type']
        if trade=='sale':
            amount=_money(row.get('price_krw'),required=True); kinds=('sale',)
        elif trade=='rent':
            deposit=_money(row.get('deposit_krw')); rent=_money(row.get('monthly_rent_krw'))
            if deposit is None or rent is None: continue
            amount=deposit; kinds=('rent','jeonse' if rent==0 else 'monthly')
        else: raise RealEstateError('region_invalid_trade')
        unit=_rounded(Decimal(amount)/area)
        for band in (('', '84-band') if 84<=area<85 else ('',)):
            for kind in kinds: groups[(band,kind)].append(unit)
    return groups

def aggregate(by_month, partitions, end, length, area, kind):
    selected=months(end,length); trade='sale' if kind=='sale' else 'rent'
    states=[partitions.get((month,trade),'pending') for month in selected]
    complete=sum(s in ('complete','empty') for s in states)
    unavailable=states.count('source_unavailable')
    values=[v for month,state in zip(selected,states) if state in ('complete','empty') for v in by_month.get(month,{}).get((area,kind),[])]
    if complete==length: status='complete'
    elif unavailable==length: status='source_unavailable'
    elif complete: status='partial'
    elif 'failed' in states: status='failed'
    else: status='pending'
    return {'status':status,'count':len(values) if complete else None,
            'median_per_m2':median(values),'covered':complete,'expected':length,'unavailable':unavailable}

def build(publication_path):
    root,pub,files,receipt_hash=_checked_publication(publication_path)
    release=pub['release_id']; prefix=f'data/property/{release}/'
    def read(name):
        if name not in files:raise RealEstateError('region_missing_source_ref')
        return json.loads(checked_read(root,files[name],MAX_ASSET))
    manifest=read(prefix+'manifest.json'); listing=read(prefix+'regions.json')
    ends=sorted(set([manifest['period']['latest_complete_month'],manifest['period']['to']]))
    result={}; total=0; verified=0
    for region in listing['regions']:
        code=region['lawd_code']; index=read(region['index']['url'].lstrip('/'))
        if index['release_id']!=release or index['lawd_code']!=code:raise RealEstateError('region_identity_mismatch')
        partitions={(p['deal_month'],p['trade_type']):p['status'] for p in index['partitions']}
        by_month=defaultdict(lambda:defaultdict(list));seen=set(); nrows=0
        for name in sorted(files):
            if not name.startswith(prefix+'transaction-packs/'+code+'/') and not name.startswith(prefix+'transactions/'+code+'/'):continue
            body=read(name); verified+=1
            if body['release_id']!=release or body['lawd_code']!=code:raise RealEstateError('region_partition_mismatch')
            entries=body['months'] if body.get('kind')=='property-transaction-pack' else [{'deal_month':body['deal_month'],'transactions':body['transactions']}]
            for entry in entries:
                month=entry['deal_month'];rows=entry['transactions']
                for row in rows:
                    if row['id'] in seen:raise RealEstateError('region_duplicate_id')
                    seen.add(row['id']);nrows+=1
                    if row['lawd_code']!=code:raise RealEstateError('region_wrong_row')
                    if row.get('statistics_eligible') and row['contract_date'][:7].replace('-','')!=month:raise RealEstateError('region_wrong_month')
                for key,values in observations(rows).items():by_month[month][key].extend(values)
        if nrows!=sum(p.get('source_rows') or 0 for p in index['partitions']):raise RealEstateError('region_row_count_mismatch')
        result[code]={f'{end}|{length}|{area}|{kind}':aggregate(by_month,partitions,end,length,area,kind) for end in ends for length in RANGES for area in AREAS for kind in KINDS}
        total+=nrows
        print(json.dumps({'region':code,'source_rows':nrows}),flush=True)
    return {'schema_version':1,'kind':'property-region-filter-metrics','property_release_id':release,
        'source_publication_sha256':receipt_hash,'formula':'eligible-report-row-median-exclusive-per-m2-half-even',
        'period':manifest['period'],'regions':result,'audit':{'source_rows':total,'verified_transaction_files':verified,'source_calls':0,'ledger_writes':0}}

def main():
    p=argparse.ArgumentParser();p.add_argument('--publication',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    data=canonical_bytes(build(a.publication))
    if len(data)>8*1024**2:raise RealEstateError('region_metric_budget')
    if a.output.exists() and a.output.read_bytes()!=data:raise RealEstateError('immutable_output_changed')
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(data)
    print(json.dumps({'bytes':len(data),'sha256':sha256(data),'output':str(a.output)}))
if __name__=='__main__':main()