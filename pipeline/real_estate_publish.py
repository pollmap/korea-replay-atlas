"""Create an audited immutable property candidate; no deployment or public pointer writes."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_EVEN
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from .real_estate import (RealEstateError, canonical_bytes, sha256, _reject_links,
                         normalize_xml_page, build_partitions, utc_instant)
from .real_estate_regions import load_registry, SOURCE_PAGE

MAX_ASSET = 24*1024**2
TARGET_ASSET = 4*1024**2
SOURCES = [
    {'id':'molit-apt-sale-detail','dataset_id':'15126468','label':'국토교통부 아파트 매매 신고상세',
     'page_url':'https://www.data.go.kr/data/15126468/openapi.do','evidence_type':'official_report'},
    {'id':'molit-apt-rent','dataset_id':'15126474','label':'국토교통부 아파트 전월세 신고·확정일자 자료',
     'page_url':'https://www.data.go.kr/data/15126474/openapi.do','evidence_type':'official_report'},
]
POLICY = 'property-publication-v2-transaction-and-location-quality'


def coverage(jobs):
    counts=Counter(j['status'] for j in jobs)
    return {'expected':len(jobs),**{s:counts[s] for s in ('complete','empty','failed','pending','partial')},
            'historical_coverage':'current_codes_only_pending_effective_date_crosswalk'}


def eligible(row):
    # A nonstandard lot string blocks parcel linkage, not valid reported money.
    # Preserve the field issue and raw value; do not invent a parcel or repair it.
    if row['cancellation']['status'] not in ('not_reported','not_provided'):
        return False
    if any(i['code']!='missing' and i['field']!='jibun' for i in row['issues']):
        return False
    if row['area_m2'] is None or row['contract_date'] is None or row['lawd_code'] is None:
        return False
    if row['kind']=='apartment-sale-report':return row['price_krw'] is not None
    return row.get('deposit_krw') is not None and row.get('monthly_rent_krw') is not None


def median(values):
    ordered=sorted(values); n=len(ordered)
    return None if not n else ordered[n//2] if n%2 else (ordered[n//2-1]+ordered[n//2])//2


def metric(job, partition):
    result={'lawd_code':job['lawd_code'],'deal_month':job['deal_month'],'trade_type':job['trade_type'],
        'status':job['status'],'source_rows':None,'eligible_rows':None,'cancelled_rows':None,
        'invalid_rows':None,'statistics_excluded_rows':None,'complex_count':None,'retrieved_at':None,'median_price_per_m2_krw':None,
        'statistic':'reported-row-median-price-per-m2',
        'cancellation_policy':'source_not_provided' if job['trade_type']=='rent' else 'exclude_cancelled_and_unknown'}
    if partition is None:return result
    records=partition['records']; active=[r for r in records if eligible(r)]
    units=[]
    if job['trade_type']=='sale':
        for row in active:
            if row['price_krw'] is not None and row['area_m2'] is not None:
                units.append(int((Decimal(row['price_krw'])/Decimal(row['area_m2'])).quantize(Decimal('1'),rounding=ROUND_HALF_EVEN)))
    return {**result,'source_rows':len(records),'eligible_rows':len(active),
        'cancelled_rows':None if job['trade_type']=='rent' else sum(r['cancellation']['status']=='cancelled' for r in records),
        'invalid_rows':sum(r['quality']=='invalid' for r in records),
        'statistics_excluded_rows':len(records)-len(active),
        'complex_count':len({r['complex_id'] for r in active if r['complex_id']}),
        'retrieved_at':partition['retrieved_at'],'median_price_per_m2_krw':median(units)}


def public_row(row, job):
    names=('id','complex_id','complex_name','legal_dong_code','legal_dong_name','lot_number',
        'area_m2','floor','build_year','contract_date','price_krw','deposit_krw','monthly_rent_krw',
        'previous_deposit_krw','previous_monthly_rent_krw','contract_term','contract_type','renewal_right',
        'registration_date','reported_at','source_updated_at','quality','issues')
    return {**{name:row.get(name) for name in names},'trade_type':job['trade_type'],
        'lawd_code':job['lawd_code'],'source_lawd_code':row['lawd_code'],
        'statistics_eligible':eligible(row),
        'cancellation':row['cancellation']['status'],'cancellation_date':row['cancellation']['reason_date'],
        'source_id':row['provenance']['source_id'],'source_input_sha256':row['provenance']['input_sha256'],
        'retrieved_at':row['provenance']['retrieved_at'],'evidence_type':'official_report','observed_at':None}


def checked_read(root, descriptor, limit):
    name=descriptor.get('path')
    if (not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9/_-]+\.(?:json|xml)',name)
            or '..' in Path(name).parts or not isinstance(descriptor.get('bytes'),int)
            or not 0 < descriptor['bytes'] <= limit or not re.fullmatch(r'[a-f0-9]{64}',descriptor.get('sha256',''))):
        raise RealEstateError('invalid_checkpoint_descriptor')
    path=root/name; _reject_links(path.absolute())
    if not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size!=descriptor['bytes']:
        raise RealEstateError('checkpoint_size_mismatch')
    payload=path.read_bytes()
    if sha256(payload)!=descriptor['sha256']:raise RealEstateError('checkpoint_hash_mismatch')
    return payload


def verify_snapshot(root, job):
    if job['status'] not in ('complete','empty'):return None
    if not job['snapshot']:raise RealEstateError('missing_complete_snapshot')
    descriptor=json.loads(job['snapshot'])
    recorded=checked_read(root,descriptor,128*1024**2)
    sources=json.loads(job['pages'])
    if not 1<=len(sources)<=1000 or sum(s['bytes'] for s in sources)>64*1024**2:
        raise RealEstateError('invalid_snapshot_sources')
    pages=[normalize_xml_page(checked_read(root,s,8*1024**2),lawd_code=job['lawd_code'],
        deal_month=job['deal_month'],retrieved_at=s['retrieved_at'],trade_type=job['trade_type']) for s in sources]
    current=build_partitions(pages)[0]
    if canonical_bytes(current)!=recorded:raise RealEstateError('snapshot_not_reproducible')
    if (len(current['records'])==0)!=(job['status']=='empty'):raise RealEstateError('snapshot_status_mismatch')
    return current


def collect_complexes(records):
    grouped=defaultdict(list)
    for row in records:
        if row['complex_id'] and (row['quality']!='invalid' or row['statistics_eligible']):grouped[row['complex_id']].append(row)
    result=[]
    for identity,rows in sorted(grouped.items()):
        rows.sort(key=lambda r:(r['retrieved_at'],r['id']))
        row=rows[-1]
        names=sorted({r['complex_name'] for r in rows if r['complex_name']})
        if not names:continue
        addresses={(r['legal_dong_name'],r['lot_number']) for r in rows if r['lot_number']}
        legal_codes=sorted({r['legal_dong_code'] for r in rows if r['legal_dong_code']})
        dates=[r['contract_date'][:7].replace('-','') for r in rows if r['contract_date']]
        if not dates:continue
        # Rental reports may lack legal-dong code. A shared provider aptSeq establishes
        # identity only; a geometry point is never inferred from the name or an address.
        result.append({'id':identity,'source_complex_id':identity.split(':',2)[2],
            'lawd_code':row['lawd_code'],'name':row['complex_name'] or names[-1],
            'legal_dong_code':legal_codes[0] if len(legal_codes)==1 else None,
            'legal_dong_name':row['legal_dong_name'],'lot_number':row['lot_number'],
            'build_year':row['build_year'],'position':None,'identity_status':'source_apt_seq',
            'source_ids':sorted({r['source_id'] for r in rows}),
            'source_input_sha256':sorted({r['source_input_sha256'] for r in rows}),
            'first_contract_month':min(dates),'last_contract_month':max(dates),
            'observed_name_variants':names,'address_conflict':len(addresses)>1 or len(legal_codes)>1})
    return result


def publish(root, registry, output_root):
    root=Path(root).absolute(); output=Path(output_root).absolute()
    _reject_links(root);_reject_links(output)
    if any(p.lower() in ('public','dist') for p in output.parts):raise RealEstateError('public_output_forbidden')
    _reject_links(root/'checkpoint.sqlite')
    connection=sqlite3.connect((root/'checkpoint.sqlite').as_uri()+'?mode=ro',uri=True)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('BEGIN')
        meta=dict(connection.execute('SELECT key,value FROM meta'))
        jobs=[dict(r) for r in connection.execute('SELECT * FROM jobs ORDER BY lawd_code,deal_month,trade_type')]
    finally:connection.close()
    registry_hash=sha256(canonical_bytes(registry))
    if meta.get('registry_sha256')!=registry_hash:raise RealEstateError('registry_hash_mismatch')
    if not jobs or len(jobs)>122_000:raise RealEstateError('invalid_job_ledger')
    official={r['lawd_code']:r for r in registry['regions']}
    if any(j['lawd_code'] not in official for j in jobs):raise RealEstateError('unknown_legal_region')
    fingerprint={'policy':POLICY,'registry_sha256':registry_hash,
        'jobs':[{k:j[k] for k in ('id','status','snapshot','pages','error_code','updated_at')} for j in jobs]}
    release='property-'+sha256(canonical_bytes(fingerprint))[:16]
    months=sorted({j['deal_month'] for j in jobs});latest=int(months[-1][:4])*12+int(months[-1][4:])-2
    period={'from':months[0],'to':months[-1],'latest_complete_month':f'{latest//12:04d}{latest%12+1:02d}'}
    if len(months)==1:period['latest_complete_month']=months[0]
    generated=max([registry['retrieved_at']]+[j['updated_at'] for j in jobs if j['updated_at']])
    utc_instant(generated)
    output.mkdir(parents=True,exist_ok=True)
    final=output/release
    if final.exists():
        publication=json.loads((final/'publication.json').read_text(encoding='utf-8'))
        for f in publication['files']:
            checked_read(final,{'path':f['path'],'sha256':f['sha256'],'bytes':f['byte_length']},MAX_ASSET)
        return publication
    stage=Path(tempfile.mkdtemp(prefix='.property-incomplete-',dir=output))
    files=[];prefix=f'data/property/{release}'

    def emit(relative,value):
        payload=canonical_bytes(value)
        if len(payload)>MAX_ASSET:raise RealEstateError('publication_asset_size_limit')
        name=f'{prefix}/{relative}';path=stage/name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as handle:handle.write(payload)
        digest=sha256(payload)
        if sha256(path.read_bytes())!=digest:raise RealEstateError('publication_hash_mismatch')
        files.append({'path':name,'sha256':digest,'byte_length':len(payload)})
        return {'url':'/'+name,'sha256':digest,'bytes':len(payload)}

    regions=[];complex_total=0;source_rows=0;groups=defaultdict(list)
    for job in jobs:groups[job['lawd_code']].append(job)
    for code,region_jobs in sorted(groups.items()):
        stats=[];partitions=[];all_rows=[];by_month=defaultdict(list);by_job={}
        for job in region_jobs:
            partition=verify_snapshot(root,job);m=metric(job,partition);stats.append(m)
            references=[]
            item={'lawd_code':code,'deal_month':job['deal_month'],'trade_type':job['trade_type'],
                'status':job['status'],'source_rows':m['source_rows'],'eligible_rows':m['eligible_rows'],
                'retrieved_at':m['retrieved_at'],'transactions':references,'error_code':job['error_code']}
            partitions.append(item);by_job[(job['deal_month'],job['trade_type'])]=item
            if partition:
                rows=[public_row(r,job) for r in partition['records']]
                source_rows+=len(rows);all_rows.extend(rows);by_month[job['deal_month']].extend(rows)
        for month,rows in sorted(by_month.items()):
            rows.sort(key=lambda r:r['id']);chunks=[];chunk=[];size=0
            for row in rows:
                row_size=len(canonical_bytes(row))
                if chunk and size+row_size>TARGET_ASSET:chunks.append(chunk);chunk=[];size=0
                chunk.append(row);size+=row_size
            chunks.append(chunk)
            for i,chunk in enumerate(chunks):
                asset=emit(f'transactions/{code}/{month}-{i:03d}.json',{'schema_version':1,
                    'kind':'property-transactions','release_id':release,'lawd_code':code,
                    'deal_month':month,'transactions':chunk})
                types={r['trade_type'] for r in chunk} if chunk else {'sale','rent'}
                for trade in types:
                    if (month,trade) in by_job and by_job[(month,trade)]['status'] in ('complete','empty'):
                        by_job[(month,trade)]['transactions'].append(asset)
        complexes=collect_complexes(all_rows);complex_total+=len(complexes)
        complex_asset=emit(f'complexes/{code}.json',{'schema_version':1,'kind':'property-complexes',
            'release_id':release,'lawd_code':code,'complexes':complexes}) if complexes else None
        name=official[code]['name'];region_coverage=coverage(region_jobs)
        index=emit(f'regions/{code}.json',{'schema_version':1,'kind':'property-region','release_id':release,
            'lawd_code':code,'name':name,'period':period,'coverage':region_coverage,
            'metrics':stats,'partitions':partitions,'complexes':complex_asset})
        latest_stats={s['trade_type']:s for s in stats if s['deal_month']==period['latest_complete_month']}
        if len(latest_stats)!=2:raise RealEstateError('missing_latest_month_jobs')
        regions.append({'lawd_code':code,'name':name,'legal_code':official[code]['legal_code'],
            'index':index,'coverage':region_coverage,'latest':latest_stats})
    regions_asset=emit('regions.json',{'schema_version':1,'kind':'property-regions','release_id':release,'regions':regions})
    manifest={'schema_version':1,'kind':'property-release','release_id':release,'generated_at':generated,
        'period':period,'coverage':coverage(jobs),'sources':SOURCES,
        'code_registry':{'source_url':SOURCE_PAGE,'retrieved_at':registry['retrieved_at'],
                         'sha256':registry['source']['sha256'],'current_region_count':len(official)},
        'regions':regions_asset,'coordinates':{'verified_complexes':0,'unresolved_complexes':complex_total,'name_only_join':False},
        'caveats':['계약 신고·확정일자 기록이며 매물 호가·시세·실시간 관측이 아닙니다.',
            '신고 지연·해제·정정으로 과거 계약월도 바뀔 수 있습니다. 수집 완료는 거래 신고의 최종 확정이 아닙니다.',
            '전월세 원천은 취소 표식을 제공하지 않습니다. 취소 확인 완료로 해석하지 마세요.',
            '현행 법정코드 기준입니다. 폐지·변경 코드의 과거 효력 구간 대조는 아직 미완료입니다.',
            '공식 건물·단지 식별자와 좌표의 연결 검증 전에는 단지 위치를 생성하지 않습니다.',
            '비표준 지번은 위치 연결 검토 대상으로 남기되, 유효한 지역·계약일·면적·금액의 통계 적격성과 구분합니다.',
            '지역 제곱미터당 중앙값은 서로 다른 거래의 분포입니다. 같은 전용면적 단지 비교와 구별하세요.']}
    entry=emit('manifest.json',manifest)
    if len(files)>18_000:raise RealEstateError('publication_file_limit')
    publication={'schema_version':1,'kind':'property-publication','release_id':release,
        'property_release':{'path':entry['url'].lstrip('/'),'sha256':entry['sha256'],'release_id':release},
        'files':sorted(files,key=lambda f:f['path']),
        'audit':{'policy':POLICY,'source_rows':source_rows,'complexes':complex_total,
                 'raw_pages_reparsed':True,'source_hashes_verified':True,'position_policy':'no_unverified_coordinates',
                 'files':len(files),'bytes':sum(f['byte_length'] for f in files),'coverage':coverage(jobs)}}
    with (stage/'publication.json').open('xb') as handle:handle.write(canonical_bytes(publication))
    if final.exists():raise RealEstateError('output_exists')
    os.rename(stage,final)
    return publication


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--regions',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    try:
        value=publish(args.root,load_registry(args.regions),args.output)
        print(json.dumps({'status':'verified','release_id':value['release_id'],**value['audit']},ensure_ascii=False))
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
        parser.exit(1,f"real_estate_publish: {error.code if isinstance(error,RealEstateError) else 'invalid_input'}\n")


if __name__=='__main__':main()
