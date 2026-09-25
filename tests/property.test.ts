import {describe,it,expect} from 'vitest';
import {createHash} from 'node:crypto';
import {parsePropertyRelease,parsePropertyRegions,parsePropertyRegionDetail,parsePropertyTransactions,
  parsePropertyComplexes,parsePropertyAssetBytes,summarizePropertyTransactions,
  eligiblePropertyTransactions,
  type PropertyTransaction,type RegionMetric} from '../shared/property';

const release='property-0123456789abcdef',stamp='2026-09-20T00:00:00Z';
const asset={url:`/data/property/${release}/regions.json`,sha256:'a'.repeat(64),bytes:10};
const coverage={expected:2,complete:1,empty:0,failed:0,pending:1,partial:0,
  historical_coverage:'current_codes_only_pending_effective_date_crosswalk'};
const period={from:'202609',to:'202609',latest_complete_month:'202609'};
const saleMetric:RegionMetric={lawd_code:'11110',deal_month:'202609',trade_type:'sale',status:'complete',
  source_rows:1,eligible_rows:1,cancelled_rows:0,invalid_rows:0,complex_count:1,retrieved_at:stamp,
  statistics_excluded_rows:0,median_price_per_m2_krw:1_000_000,statistic:'reported-row-median-price-per-m2',cancellation_policy:'exclude_cancelled_and_unknown'};
const pendingMetric:RegionMetric={...saleMetric,trade_type:'rent',status:'pending',source_rows:null,eligible_rows:null,
  cancelled_rows:null,invalid_rows:null,statistics_excluded_rows:null,complex_count:null,retrieved_at:null,median_price_per_m2_krw:null,cancellation_policy:'source_not_provided'};
function row(overrides:Partial<PropertyTransaction>={}):PropertyTransaction{
  return {id:`molit-sale:${'1'.repeat(64)}:1`,trade_type:'sale',complex_id:'molit-apt:11110:11110-1',
    complex_name:'검증용 가상단지',lawd_code:'11110',source_lawd_code:'11110',legal_dong_code:'1111010100',legal_dong_name:'검증동',
    lot_number:'1-2',area_m2:'84.99',floor:2,build_year:2000,contract_date:'2026-09-01',price_krw:100_000_000,
    deposit_krw:null,monthly_rent_krw:null,previous_deposit_krw:null,previous_monthly_rent_krw:null,
    contract_term:null,contract_type:null,renewal_right:null,registration_date:null,reported_at:null,source_updated_at:null,
    cancellation:'not_reported',cancellation_date:null,quality:'valid',issues:[],statistics_eligible:true,source_id:'molit-apt-sale-detail',
    source_input_sha256:'a'.repeat(64),retrieved_at:stamp,evidence_type:'official_report',observed_at:null,...overrides};
}
function packet(transactions:PropertyTransaction[]){return {schema_version:1,kind:'property-transactions',release_id:release,lawd_code:'11110',deal_month:'202609',transactions};}
function manifest(){return {schema_version:1,kind:'property-release',release_id:release,generated_at:stamp,period,coverage,
  sources:[{id:'molit-apt-sale-detail',dataset_id:'15126468',label:'공식 매매 신고',page_url:'https://www.data.go.kr/data/15126468/openapi.do',evidence_type:'official_report'},
    {id:'molit-apt-rent',dataset_id:'15126474',label:'공식 전월세 신고',page_url:'https://www.data.go.kr/data/15126474/openapi.do',evidence_type:'official_report'}],
  code_registry:{source_url:'https://www.code.go.kr/stdcodesrch/codeAllDownloadL.do',retrieved_at:stamp,sha256:'b'.repeat(64),current_region_count:1},
  regions:asset,coordinates:{verified_complexes:0,unresolved_complexes:1,name_only_join:false},caveats:['실거래 신고 기록이며 현재 호가가 아닙니다.']};}

describe('property release source and coverage contract',()=>{
  it('accepts a verified descriptor and refuses an external/path escape asset',()=>{
    expect(parsePropertyRelease(manifest()).coverage.pending).toBe(1);
    for(const url of ['https://example.org/data.json','/data/property/../secret.json','/data/property/property-ffffffffffffffff/regions.json']){
      expect(()=>parsePropertyRelease({...manifest(),regions:{...asset,url}})).toThrow();
    }
  });
  it('refuses a coverage sum or substituted source URL',()=>{
    expect(()=>parsePropertyRelease({...manifest(),coverage:{...coverage,expected:3}})).toThrow();
    const changed=manifest();changed.sources[0].page_url='https://example.org';
    expect(()=>parsePropertyRelease(changed)).toThrow();
  });
  it('keeps missing region metrics null and rejects fabricated zero coverage',()=>{
    const value={schema_version:1,kind:'property-regions',release_id:release,regions:[{lawd_code:'11110',legal_code:'1111000000',
      name:'검증 지역',index:asset,coverage,latest:{sale:saleMetric,rent:pendingMetric}}]};
    expect(parsePropertyRegions(value).regions[0].latest.rent.source_rows).toBeNull();
    value.regions[0].latest.rent={...pendingMetric,source_rows:0};
    expect(()=>parsePropertyRegions(value)).toThrow();
  });
  it('refuses published transaction assets for an incomplete query',()=>{
    const value={schema_version:1,kind:'property-region',release_id:release,lawd_code:'11110',name:'검증 지역',period,
      coverage:{...coverage,expected:1,complete:0},metrics:[pendingMetric],complexes:null,
      partitions:[{lawd_code:'11110',deal_month:'202609',trade_type:'rent',status:'pending',source_rows:null,
        eligible_rows:null,retrieved_at:null,transactions:[],error_code:null}]};
    expect(parsePropertyRegionDetail(value).partitions[0].status).toBe('pending');
    expect(()=>parsePropertyRegionDetail({...value,partitions:[{...value.partitions[0],transactions:[asset]}]})).toThrow();
    expect(()=>parsePropertyRegionDetail({...value,metrics:[{...pendingMetric,deal_month:'202608'}]})).toThrow();
    expect(()=>parsePropertyRegionDetail({...value,metrics:[{...pendingMetric,complex_count:0}]})).toThrow();
  });
  it('accepts ten years plus provisional data and continued monthly archive growth, with bounded scopes',()=>{
    const build=(count:number)=>{
      const metrics=Array.from({length:count},(_,index)=>{
        const date=new Date(Date.UTC(2016,8+index,1));
        const deal_month=`${date.getUTCFullYear()}${String(date.getUTCMonth()+1).padStart(2,'0')}`;
        return (['sale','rent'] as const).map(trade_type=>({...pendingMetric,deal_month,trade_type,cancellation_policy:trade_type==='sale'?'exclude_cancelled_and_unknown':'source_not_provided'}));
      }).flat();
      return {schema_version:1,kind:'property-region',release_id:release,lawd_code:'11110',name:'검증 지역',
        period:{from:'201609',to:metrics.at(-1)!.deal_month,latest_complete_month:metrics.at(-3)!.deal_month},
        coverage:{...coverage,expected:metrics.length,complete:0,pending:metrics.length},metrics,complexes:null,
        partitions:metrics.map(metric=>({lawd_code:'11110',deal_month:metric.deal_month,trade_type:metric.trade_type,status:'pending',source_rows:null,eligible_rows:null,retrieved_at:null,transactions:[],error_code:null}))};
    };
    expect(parsePropertyRegionDetail(build(121)).partitions).toHaveLength(242);
    expect(parsePropertyRegionDetail(build(122)).partitions).toHaveLength(244);
    const duplicate=build(122);duplicate.metrics[1]={...duplicate.metrics[0]};duplicate.partitions[1]={...duplicate.partitions[0]};
    expect(()=>parsePropertyRegionDetail(duplicate)).toThrow();
    const outside=build(122);outside.period.to='202608';
    expect(()=>parsePropertyRegionDetail(outside)).toThrow();
    const overlong=build(122);overlong.period.to='220001';
    expect(()=>parsePropertyRegionDetail(overlong)).toThrow();
    const latest=build(122);latest.period.latest_complete_month='220001';
    expect(()=>parsePropertyRegionDetail(latest)).toThrow();
  });
});

describe('property reported transactions',()=>{
  it('decodes sale and rental without manufacturing a rental cancellation record',()=>{
    const rental=row({id:`molit-rent:${'2'.repeat(64)}:1`,trade_type:'rent',source_id:'molit-apt-rent',price_krw:null,
      deposit_krw:100_000_000,monthly_rent_krw:0,cancellation:'not_provided'});
    expect(parsePropertyTransactions(packet([row(),rental])).transactions).toHaveLength(2);
    expect(()=>parsePropertyTransactions(packet([{...rental,cancellation:'not_reported'}]))).toThrow();
  });
  it.each([NaN,Infinity,-1,1.5,Number.MAX_SAFE_INTEGER+1])('refuses invalid money %s',price=>{
    expect(()=>parsePropertyTransactions(packet([row({price_krw:price})]))).toThrow();
  });
  it('refuses invalid dates, noncanonical areas, duplicate row IDs and made-up sources',()=>{
    for(const changed of [row({contract_date:'2026-02-30'}),row({area_m2:'84.9900'}),row({source_id:'molit-apt-rent'}),
      row({quality:'valid',issues:[{field:'floor',code:'invalid_format'}]})]){
      expect(()=>parsePropertyTransactions(packet([changed]))).toThrow();
    }
    expect(()=>parsePropertyTransactions(packet([row(),row()]))).toThrow();
  });
  it('compares only an exact declared area, period and source complex ID',()=>{
    const values=[row(),row({id:`molit-sale:${'2'.repeat(64)}:1`,price_krw:200_000_000}),
      row({id:`molit-sale:${'3'.repeat(64)}:1`,price_krw:900_000_000,cancellation:'cancelled'}),
      row({id:`molit-sale:${'4'.repeat(64)}:1`,price_krw:800_000_000,area_m2:'59'})];
    const filter={complex_id:'molit-apt:11110:11110-1',trade_type:'sale' as const,area_m2:'84.99',from:'2026-09-01',to:'2026-09-30'};
    const summary=summarizePropertyTransactions(values,filter);
    expect(summary.count).toBe(2);expect(summary.median_price_krw).toBe(150_000_000);
    expect(()=>summarizePropertyTransactions([...values,values[0]],filter)).toThrow('duplicate_snapshot_records');
  });
  it('keeps positions null and refuses NaN or a name-only coordinate link',()=>{
    const complex={id:'molit-apt:11110:11110-1',source_complex_id:'11110-1',lawd_code:'11110',name:'검증용 가상단지',
      legal_dong_code:null,legal_dong_name:'검증동',lot_number:'1-2',build_year:2000,position:null,identity_status:'source_apt_seq',
      source_ids:['molit-apt-sale-detail'],source_input_sha256:['a'.repeat(64)],first_contract_month:'202609',last_contract_month:'202609',
      observed_name_variants:['검증용 가상단지'],address_conflict:false};
    const value={schema_version:1,kind:'property-complexes',release_id:release,lawd_code:'11110',complexes:[complex]};
    expect(parsePropertyComplexes(value).complexes[0].position).toBeNull();
    const position={longitude:NaN,latitude:37.5,crs:'EPSG:4326',evidence:{source_id:'fixture',source_record_id:'fixture',
      source_sha256:'a'.repeat(64),method:'official_complex_id',verified_at:stamp}};
    expect(()=>parsePropertyComplexes({...value,complexes:[{...complex,position}]})).toThrow();
    expect(()=>parsePropertyComplexes({...value,complexes:[{...complex,position:{...position,longitude:127,evidence:{...position.evidence,method:'name_match'}}}]})).toThrow();
  });
  it('separates nonstandard lot geometry from valid money, while excluding money errors and cancellation',()=>{
    const lot=row({lot_number:null,quality:'invalid',issues:[{field:'jibun',code:'invalid_format'}],statistics_eligible:true});
    const money=row({id:`molit-sale:${'2'.repeat(64)}:1`,price_krw:null,quality:'invalid',
      issues:[{field:'dealAmount',code:'invalid_format'}],statistics_eligible:false});
    const cancelled=row({id:`molit-sale:${'3'.repeat(64)}:1`,cancellation:'cancelled',statistics_eligible:false});
    const parsed=parsePropertyTransactions(packet([lot,money,cancelled]));
    expect(eligiblePropertyTransactions(parsed.transactions)).toEqual([lot]);
    expect(()=>parsePropertyTransactions(packet([{...money,statistics_eligible:true}]))).toThrow();
  });
  it('verifies byte count and SHA before exposing JSON to the caller',async()=>{
    const bytes=new TextEncoder().encode(JSON.stringify(packet([row()])));
    const descriptor={...asset,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')};
    expect((await parsePropertyAssetBytes(bytes,descriptor,parsePropertyTransactions)).transactions).toHaveLength(1);
    await expect(parsePropertyAssetBytes(bytes,{...descriptor,sha256:'0'.repeat(64)},parsePropertyTransactions)).rejects.toThrow('hash');
    await expect(parsePropertyAssetBytes(bytes.slice(1),descriptor,parsePropertyTransactions)).rejects.toThrow('size');
  });
});
