"""Bounded map-price presets from audited monthly summaries and pinned point IDs."""
from pathlib import Path
import argparse,json,hashlib,re
from .real_estate import canonical_bytes,RealEstateError
from .real_estate_publish import checked_read,MAX_ASSET
from .real_estate_region_metrics import months,RANGES,AREAS,KINDS

def choose(rows,wanted,area,kind):
    selected={}
    for row in rows:
        if row['deal_month'] not in wanted:continue
        if area=='84-band' and not 84<=float(row['area_m2'])<85:continue
        if kind=='sale' and row['trade_type']!='sale':continue
        if kind=='rent' and row['trade_type']!='rent':continue
        if kind in ('jeonse','monthly') and row['rent_kind']!=kind:continue
        old=selected.get(row['complex_id'])
        if old is None or row['latest_contract_date']>old['latest_contract_date'] or row['latest_contract_date']==old['latest_contract_date'] and row['latest_transaction_id']<old['latest_transaction_id']:
            selected[row['complex_id']]=row
    return [selected[k] for k in sorted(selected)]

def build(publication, navigation, output, *, source_root=None):
    source=Path(publication);pub=json.loads(source.read_bytes()); root=Path(source_root) if source_root is not None else source.parent
    nav=json.loads(Path(navigation).read_bytes()); release=pub['property_release_id']
    if nav['property_release_id']!=release:raise RealEstateError('map_preset_release_mismatch')
    files={f['path']:{'path':f['path'],'bytes':f['byte_length'],'sha256':f['sha256'],
                     **({'transport':f['transport']} if 'transport' in f else {})} for f in pub['files']}
    if len(files)!=len(pub['files']):raise RealEstateError('map_preset_duplicate_ref')
    def read(url):
        name=url.lstrip('/')
        if name not in files:raise RealEstateError('map_preset_missing_ref')
        return json.loads(checked_read(root,files[name],MAX_ASSET))
    manifest=read(pub['summary_release']['path'])
    if manifest['property_release_id']!=release:raise RealEstateError('map_preset_manifest_mismatch')
    ends=sorted(set([manifest['period']['latest_complete_month'],manifest['period']['to']]))
    by_region={}
    for point in nav['points']:by_region.setdefault(point[0].split(':')[1],set()).add(point[0])
    output=Path(output);output.mkdir(parents=True,exist_ok=True);results=[]
    fields=('complex_id','deal_month','trade_type','rent_kind','area_m2','transaction_count','latest_transaction_id','latest_contract_date','latest_price_krw','latest_deposit_krw','latest_monthly_rent_krw')
    for ref in manifest['regions']:
        code=ref['lawd_code'];ids=by_region.get(code)
        if not ids:continue
        index=read(ref['url']);rows=[];seen=set();identities=set()
        if index.get('property_release_id')!=release or index.get('lawd_code')!=code:
            raise RealEstateError('map_preset_partition_mismatch')
        complete={(p['deal_month'],p['trade_type']) for p in index['partitions'] if p['status'] in ('complete','empty')}
        # Prefer bounded per-complex packs. A map needs linked point IDs, not
        # every apartment's regional twenty-year monthly summary.
        chunks=index.get('complex_chunks')
        assets=[asset for identity in sorted(ids) for asset in chunks.get(identity,[])] if isinstance(chunks,dict) else index['summaries']
        for asset in assets:
            url=asset['url']
            if url in seen:continue
            seen.add(url);body=read(url)
            if body['property_release_id']!=release or body['lawd_code']!=code:raise RealEstateError('map_preset_partition_mismatch')
            if body['kind'] not in ('property-complex-summary-month-pack','property-complex-summary-pack','property-complex-summaries'):
                raise RealEstateError('map_preset_partition_mismatch')
            batches=body['months'] if body['kind']=='property-complex-summary-month-pack' else [body]
            for part in batches:
                for row in part['rows']:
                    if row['complex_id'] not in ids:continue
                    identity=(row['complex_id'],row['deal_month'],row['trade_type'],row['rent_kind'],row['area_m2'])
                    if identity in identities:raise RealEstateError('map_preset_duplicate_row')
                    identities.add(identity)
                    if (row['deal_month'],row['trade_type']) in complete:rows.append({k:row[k] for k in fields})
        pool=[];intern={};views={}
        for end in ends:
            for length in RANGES:
                wanted=set(months(end,length))
                for area in AREAS:
                    for kind in KINDS:
                        indices=[]
                        for row in choose(rows,wanted,area,kind):
                            key=canonical_bytes(row)
                            if key not in intern:intern[key]=len(pool);pool.append(row)
                            indices.append(intern[key])
                        views[f'{end}|{length}|{area}|{kind}']=indices
        value={'schema_version':1,'kind':'property-map-price-presets','property_release_id':release,'lawd_code':code,
            'point_ids':sorted(ids),'rows':pool,'views':views,'partitions':[{k:p[k] for k in ('deal_month','trade_type','status')} for p in index['partitions']],
            'source_publication_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'navigation_sha256':hashlib.sha256(Path(navigation).read_bytes()).hexdigest()}
        raw=canonical_bytes(value)
        if len(raw)>1024*1024:raise RealEstateError('map_preset_file_budget')
        if not re.fullmatch(r'property-[a-f0-9]{16}',release) or not re.fullmatch(r'[0-9]{5}',code):
            raise RealEstateError('map_preset_identity')
        target=output/(f'property-map-price-{release[9:]}-{code}.json')
        if target.exists() and target.read_bytes()!=raw:raise RealEstateError('immutable_output_changed')
        target.write_bytes(raw);results.append({'code':code,'bytes':len(raw),'source_rows':len(rows),'unique_selected_rows':len(pool),'points':len(ids)})
        print(json.dumps(results[-1]),flush=True)
    print(json.dumps({'regions':len(results),'bytes':sum(x['bytes'] for x in results),'source_calls':0,'source_rows':sum(x['source_rows'] for x in results)}))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--publication',required=True);p.add_argument('--navigation',required=True);p.add_argument('--output',required=True);p.add_argument('--source-root');a=p.parse_args();build(a.publication,a.navigation,a.output,source_root=a.source_root)
