import {expect,it} from 'vitest';
import {historyMonths,historyDay,historySummary,historyRangeLabel} from '../shared/property-history';
import {historySourceStart,historySourceCoverage} from '../shared/property-source-period';
import {readPropertyView} from '../shared/property-view';
import {loadPropertyHistory,type HistoryResult} from '../src/property-history-loader';
import {loadComparisonHistory} from '../src/property-comparison-loader';
import type {AtlasContent} from '../src/useAtlas';
import type {PropertyComplex,PropertySource} from '../shared/property';
import type {PropertyPartition,PropertyRegionDetail,PropertyTransaction} from '../shared/property';

const stamp='2026-09-20T00:00:00Z',release='property-0123456789abcdef',complex='molit-apt:11110:test';
const sources:PropertySource[]=[{id:'molit-apt-sale-detail',dataset_id:'15126468',label:'매매',page_url:'https://www.data.go.kr/data/15126468/openapi.do',evidence_type:'official_report'},
  {id:'molit-apt-rent',dataset_id:'15126474',label:'전월세',page_url:'https://www.data.go.kr/data/15126474/openapi.do',evidence_type:'official_report'}];
function row(overrides:Partial<PropertyTransaction>={}):PropertyTransaction{return {
  id:`molit-sale:${'1'.repeat(64)}:1`,trade_type:'sale',complex_id:complex,complex_name:'테스트 단지',lawd_code:'11110',source_lawd_code:'11110',
  legal_dong_code:null,legal_dong_name:null,lot_number:null,area_m2:'84.99',floor:2,build_year:2000,contract_date:'2026-08-01',price_krw:100000000,
  deposit_krw:null,monthly_rent_krw:null,previous_deposit_krw:null,previous_monthly_rent_krw:null,contract_term:null,contract_type:null,renewal_right:null,
  registration_date:null,reported_at:null,source_updated_at:null,cancellation:'not_reported',cancellation_date:null,quality:'valid',issues:[],statistics_eligible:true,
  source_id:'molit-apt-sale-detail',source_input_sha256:'a'.repeat(64),retrieved_at:stamp,evidence_type:'official_report',observed_at:null,...overrides};}
function partition(month='202608',overrides:Partial<PropertyPartition>={}):PropertyPartition{return {lawd_code:'11110',deal_month:month,trade_type:'sale',status:'complete',source_rows:1,eligible_rows:1,retrieved_at:stamp,error_code:null,transactions:[{url:`/data/property/${release}/${month}.json`,sha256:'b'.repeat(64),bytes:1000}],...overrides};}
function detail(partitions:PropertyPartition[]):PropertyRegionDetail{return {schema_version:1,kind:'property-region',release_id:release,lawd_code:'11110',name:'테스트 지역',period:{from:'202606',to:'202609',latest_complete_month:'202608'},coverage:{expected:partitions.length,complete:partitions.length,empty:0,pending:0,partial:0,failed:0,historical_coverage:'current_codes_only_pending_effective_date_crosswalk'},metrics:[],partitions,complexes:null};}
function packet(transactions:PropertyTransaction[],deal_month='202608'){return {schema_version:1,kind:'property-transactions',release_id:release,lawd_code:'11110',deal_month,transactions};}
it('counts one bounded shared pack against the download budget for several months',async()=>{
  const shared={url:`/data/property/${release}/transaction-packs/11110/0000.json`,sha256:'b'.repeat(64),bytes:1000};
  const source=detail(['202607','202608'].map(month=>partition(month,{transactions:[shared]})));
  const results:HistoryResult[]=[],budget={remaining:1000,requestsRemaining:1};
  await loadPropertyHistory({detail:source,end:'202608',count:3,trade:'sale',complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:r=>results.push(r),budget,fetchJson:async()=>({schema_version:1,kind:'property-transaction-pack',release_id:release,lawd_code:'11110',months:['202607','202608'].map((month,i)=>({deal_month:month,transactions:[row({id:`molit-sale:${'1'.repeat(64)}:${i+1}`,contract_date:`${month.slice(0,4)}-${month.slice(4)}-01`})]}))})});
  expect(results.filter(r=>r.status==='ready')).toHaveLength(2);expect(budget).toEqual({remaining:0,requestsRemaining:0,rowsRemaining:19998});
});
function publishedDetail(partitions:PropertyPartition[]):PropertyRegionDetail {
  const value=detail(partitions);
  value.metrics=partitions.map(partition=>({lawd_code:partition.lawd_code,deal_month:partition.deal_month,trade_type:'sale',status:'complete',source_rows:partition.source_rows,eligible_rows:partition.eligible_rows,cancelled_rows:0,invalid_rows:0,statistics_excluded_rows:0,complex_count:1,retrieved_at:stamp,median_price_per_m2_krw:1000000,statistic:'reported-row-median-price-per-m2',cancellation_policy:'exclude_cancelled_and_unknown'}));
  return value;
}

it('places different months on an elapsed-day axis and handles year/leap boundaries',()=>{
  expect(historyMonths('202601',3)).toEqual(['202511','202512','202601']);
  expect(historyDay('2024-03-01')-historyDay('2024-02-28')).toBe(2);
  expect(historyDay('2026-08-01')-historyDay('2026-07-01')).toBe(31);
  expect(()=>historyMonths('202613',3)).toThrow();
});
it('restores bounded history range in old/new shared URLs',()=>{
  const period={from:'202109',to:'202609',latest_complete_month:'202608'};
  expect(readPropertyView('#historyMonths=12',period).historyMonths).toBe(12);
  expect(readPropertyView('#historyMonths=120',period).historyMonths).toBe(120);
  expect(readPropertyView('#historyMonths=240',period).historyMonths).toBe(240);
  expect(readPropertyView('#historyMonths=241',period).historyMonths).toBe(3);
  expect(readPropertyView('#historyMonths=999999',period).historyMonths).toBe(3);
  expect(readPropertyView('',period).historyMonths).toBe(36);
});
it('offers 240 completed contract months and keeps the current provisional month separate',()=>{
  const months=historyMonths('202608',240);
  expect(months).toHaveLength(240);expect(months[0]).toBe('200609');expect(months.at(-1)).toBe('202608');expect(months).not.toContain('202609');
  expect(historyRangeLabel(240)).toBe('20년');
  expect(historyMonths('202609',1)).toEqual(['202609']);
  expect(historySourceCoverage(months,historySourceStart('sale',sources))).toEqual({before:0,eligible:240});
  expect(historySourceCoverage(months,historySourceStart('rent',sources))).toEqual({before:52,eligible:188});
  expect(historySourceStart('rent',[])).toBeUndefined();
  expect(historySourceStart('rent',[{...sources[1],page_url:'https://example.com'}])).toBeUndefined();
});
it('skips source-unavailable rental months without conflating later uncollected months with empty reports',async()=>{
  let calls=0;const results:HistoryResult[]=[];
  await loadPropertyHistory({detail:detail([partition('201012',{trade_type:'rent'})]),end:'202608',count:240,trade:'rent',complex,origin:'https://example.com',sources,signal:new AbortController().signal,onMonth:r=>results.push(r),fetchJson:async()=>{calls++;return packet([]);}});
  expect(calls).toBe(0);expect(results).toHaveLength(240);
  expect(results.filter(r=>r.reason==='before_source')).toHaveLength(52);
  expect(results.filter(r=>r.reason==='outside_release')).toHaveLength(188);
  expect(results.every(r=>r.status==='missing')).toBe(true);
});
it('keeps 240-month requests bounded and marks budget-skipped months as unknown',async()=>{
  const months=historyMonths('202608',240),results:HistoryResult[]=[];let calls=0,active=0,peak=0;
  await loadPropertyHistory({detail:detail(months.map(m=>partition(m))),end:'202608',count:240,trade:'sale',complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:r=>results.push(r),budget:{remaining:24*1024*1024,requestsRemaining:4},fetchJson:async ref=>{
    calls++;peak=Math.max(peak,++active);await new Promise(resolve=>setTimeout(resolve,0));active--;
    const month=ref.url!.split('/').pop()!.slice(0,6);return packet([row()],month);
  }});
  expect(calls).toBe(4);expect(peak).toBe(2);expect(results).toHaveLength(240);
  expect(results.filter(r=>r.status==='ready')).toHaveLength(4);
  expect(results.filter(r=>r.reason==='request_budget')).toHaveLength(236);
});
it('does not publish a partially retained month when the selected-row budget is exhausted',async()=>{
  const results:HistoryResult[]=[];
  await loadPropertyHistory({detail:detail([partition('202608',{source_rows:2,eligible_rows:2})]),end:'202608',count:240,trade:'sale',complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:r=>results.push(r),budget:{remaining:24*1024*1024,rowsRemaining:1},fetchJson:async()=>packet([row(),row({id:`molit-sale:${'1'.repeat(64)}:2`})])});
  expect(results.find(r=>r.month==='202608')).toMatchObject({status:'missing',reason:'retention_budget',rows:[]});
});
it('can read all 240 published months within the existing byte budget and stops a long run on cancellation',async()=>{
  const months=historyMonths('202608',240),results:HistoryResult[]=[],controller=new AbortController();let calls=0;
  const input={detail:detail(months.map(m=>partition(m))),end:'202608',count:240 as const,trade:'sale' as const,complex,origin:'https://example.com',sources,signal:controller.signal,onMonth:(r:HistoryResult)=>results.push(r)};
  await loadPropertyHistory({...input,fetchJson:async ref=>{calls++;const month=ref.url!.split('/').pop()!.slice(0,6);return packet([row({contract_date:`${month.slice(0,4)}-${month.slice(4)}-01`})],month);}});
  expect(calls).toBe(240);expect(results).toHaveLength(240);expect(results.every(r=>r.status==='ready')).toBe(true);
  expect(results.flatMap(r=>r.rows)).toHaveLength(240);expect(results.at(-1)?.month).toBe('200609');
  calls=0;results.length=0;
  await loadPropertyHistory({...input,fetchJson:async ref=>{calls++;controller.abort();return packet([row()],ref.url!.split('/').pop()!.slice(0,6));}});
  expect(calls).toBeLessThanOrEqual(2);expect(results).toHaveLength(0);
});
it('retains a valid zero deposit and deterministically selects latest dated record',()=>{
  const rental=row({trade_type:'rent',deposit_krw:0,monthly_rent_krw:1000000});
  expect(historySummary([rental])).toMatchObject({min:0,max:0,latest:rental});
  expect(historySummary([row(),row({id:'b',contract_date:'2026-08-20',price_krw:200000000})]).latest?.id).toBe('b');
});
it('reads a shared sale/rent month file and retains only the requested trade and complex',async()=>{
  const rental=row({id:`molit-rent:${'2'.repeat(64)}:1`,trade_type:'rent',source_id:'molit-apt-rent',price_krw:null,deposit_krw:0,monthly_rent_krw:1000000,cancellation:'not_provided'});
  const results:HistoryResult[]=[];
  await loadPropertyHistory({detail:detail([partition()]),end:'202608',count:3,trade:'sale',complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:r=>results.push(r),fetchJson:async()=>packet([row(),rental])});
  expect(results.find(r=>r.month==='202608')).toMatchObject({status:'ready',rows:[row()]});
  expect(results.filter(r=>r.status==='missing')).toHaveLength(2);
});
it('does not report failed, partial, pending or mismatched sources as zero',async()=>{
  for(const bad of [packet([], '202607'),packet([]),{...packet([row()]),release_id:'property-ffffffffffffffff'}]){
    const results:HistoryResult[]=[];
    await loadPropertyHistory({detail:detail([partition()]),end:'202608',count:1,trade:'sale',complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:r=>results.push(r),fetchJson:async()=>bad});
    expect(results[0].status).toBe('error');
  }
  for(const status of ['partial','pending','failed'] as const){let calls=0;const results:HistoryResult[]=[];
    await loadPropertyHistory({detail:detail([partition('202608',{status})]),end:'202608',count:1,trade:'sale',complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:r=>results.push(r),fetchJson:async()=>{calls++;return packet([]);}});
    expect(results[0].status).toBe('missing');expect(calls).toBe(0);
  }
});
it('keeps confirmed empty separate and abort prevents late state publication',async()=>{
  const results:HistoryResult[]=[],controller=new AbortController();
  await loadPropertyHistory({detail:detail([partition('202608',{status:'empty',source_rows:0,transactions:[]})]),end:'202608',count:1,trade:'sale',complex,origin:'https://example.com',signal:controller.signal,onMonth:r=>results.push(r)});
  expect(results[0]).toEqual({month:'202608',status:'ready',rows:[]});results.length=0;
  await loadPropertyHistory({detail:detail([partition()]),end:'202608',count:1,trade:'sale',complex,origin:'https://example.com',signal:controller.signal,onMonth:r=>results.push(r),fetchJson:async()=>{controller.abort();return packet([row()]);}});
  expect(results).toEqual([]);
});
it('limits concurrent month jobs and preserves recent results when older files exceed the budget',async()=>{
  let active=0,peak=0,calls=0;
  const make=()=>({detail:detail(['202606','202607','202608'].map(m=>partition(m))),end:'202608',count:3 as const,trade:'sale' as const,complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:()=>undefined});
  await loadPropertyHistory({...make(),fetchJson:async ref=>{calls++;peak=Math.max(peak,++active);await new Promise(resolve=>setTimeout(resolve,1));active--;const month=ref.url!.split('/').pop()!.slice(0,6);return packet([row({contract_date:`${month.slice(0,4)}-${month.slice(4)}-01`})],month);}});
  expect(calls).toBe(3);expect(peak).toBe(2);
  const input=make();input.detail.partitions[0].transactions[0].bytes=25*1024*1024;
  const results:HistoryResult[]=[];
  await loadPropertyHistory({...input,onMonth:r=>results.push(r),fetchJson:async ref=>{const month=ref.url!.split('/').pop()!.slice(0,6);expect(month).not.toBe('202606');return packet([row()],month);}});
  expect(results.filter(r=>r.status==='ready')).toHaveLength(2);
  expect(results.find(r=>r.month==='202606')).toMatchObject({status:'missing',reason:'download_budget',rows:[]});
});

it('loads a district once for several compared complexes and retains distinct identical reports',async()=>{
  const other='molit-apt:11110:other',ignored='molit-apt:11110:ignored';
  const source=publishedDetail([partition('202608',{source_rows:4,eligible_rows:4})]);
  const atlas={origin:'https://example.com',property:{release_id:release},regions:{regions:[{lawd_code:'11110',index:{url:'/region.json',sha256:'a'.repeat(64),bytes:100}}]}} as AtlasContent;
  const items=[complex,other].map(id=>({id,lawd_code:'11110'} as PropertyComplex));
  const urls:string[]=[];
  const data=await loadComparisonHistory({atlas,items,month:'202608',range:3,trade:'sale',signal:new AbortController().signal,fetchJson:async ref=>{
    urls.push(ref.url!);
    return ref.url==='/region.json'?source:packet([row(),row({id:`molit-sale:${'1'.repeat(64)}:2`}),row({id:`molit-sale:${'2'.repeat(64)}:1`,complex_id:other}),row({id:`molit-sale:${'3'.repeat(64)}:1`,complex_id:ignored})]);
  }});
  expect(urls).toHaveLength(2);
  expect(data['11110'].find(result=>result.month==='202608')?.rows.map(value=>value.complex_id)).toEqual([complex,complex,other]);
  expect(data['11110'].filter(result=>result.status==='missing')).toHaveLength(2);
});
it('keeps pre-source comparison months separate even if the district lookup fails',async()=>{
  const atlas={origin:'https://example.com',property:{release_id:release,sources},regions:{regions:[{lawd_code:'11110',index:{url:'/region.json',sha256:'a'.repeat(64),bytes:100}}]}} as AtlasContent;
  const items=[{id:complex,lawd_code:'11110'}] as PropertyComplex[];let calls=0;
  const fetchJson=async()=>{calls++;throw new Error('offline');};
  const earlier=await loadComparisonHistory({atlas,items,month:'201012',range:3,trade:'rent',signal:new AbortController().signal,fetchJson});
  expect(calls).toBe(0);expect(earlier['11110'].every(r=>r.reason==='before_source')).toBe(true);
  const current=await loadComparisonHistory({atlas,items,month:'202608',range:240,trade:'rent',signal:new AbortController().signal,fetchJson});
  expect(current['11110'].filter(r=>r.reason==='before_source')).toHaveLength(52);
  expect(current['11110'].filter(r=>r.status==='error')).toHaveLength(188);
});

it('retains other comparison regions when one region lookup fails and does not publish an aborted comparison',async()=>{
  const source=publishedDetail([partition()]);
  const atlas={origin:'https://example.com',property:{release_id:release},regions:{regions:['11110','11140'].map(code=>({lawd_code:code,index:{url:`/${code}.json`,sha256:'a'.repeat(64),bytes:100}}))}} as AtlasContent;
  const items=[{id:complex,lawd_code:'11110'},{id:'molit-apt:11140:other',lawd_code:'11140'}] as PropertyComplex[];
  const data=await loadComparisonHistory({atlas,items,month:'202608',range:1,trade:'sale',signal:new AbortController().signal,fetchJson:async ref=>{
    if(ref.url==='/11140.json')throw new Error('offline');
    return ref.url==='/11110.json'?source:packet([row()]);
  }});
  expect(data['11110'][0].status).toBe('ready');expect(data['11140'][0]).toMatchObject({status:'error',reason:'offline',rows:[]});
  const controller=new AbortController();
  const aborted=await loadComparisonHistory({atlas,items:items.slice(0,1),month:'202608',range:1,trade:'sale',signal:controller.signal,fetchJson:async()=>{controller.abort();return source;}});
  expect(aborted).toEqual({});
});


it('reuses validated selected-complex months after detail tabs remount without rereading regional packets',async()=>{
  let calls=0;const fetchJson=async()=>{calls++;return packet([row(),row({id:`molit-sale:${'2'.repeat(64)}:1`,complex_id:'molit-apt:11110:other'})]);};
  const results:HistoryResult[]=[];
  const args={detail:detail([partition('202608',{source_rows:2,eligible_rows:2})]),end:'202608',count:1 as const,trade:'sale' as const,complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:(r:HistoryResult)=>results.push(r),fetchJson};
  await loadPropertyHistory(args);await loadPropertyHistory(args);
  expect(calls).toBe(1);expect(results).toHaveLength(2);expect(results[1]).toEqual(results[0]);
  expect(results[1].rows).toEqual([row()]);
  results[1].rows.length=0;await loadPropertyHistory(args);expect(results[2].rows).toEqual([row()]);
  expect(()=>{results[2].rows[0].issues.push({field:'test',code:'changed'});}).toThrow();
});
it('never reuses cached months across origins, releases, selected complexes or source hashes',async()=>{
  let calls=0;const fetchJson=async()=>{calls++;return packet([row()]);};
  const args={detail:detail([partition()]),end:'202608',count:1 as const,trade:'sale' as const,complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:()=>{},fetchJson};
  await loadPropertyHistory(args);
  await loadPropertyHistory({...args,origin:'https://other.example'});
  await loadPropertyHistory({...args,complex:'molit-apt:11110:other'});
  await loadPropertyHistory({...args,detail:{...args.detail,release_id:'property-ffffffffffffffff'}});
  await loadPropertyHistory({...args,detail:detail([partition('202608',{transactions:[{...partition().transactions[0],sha256:'c'.repeat(64)}]})])});
  expect(calls).toBe(5);
});
it('does not promote failed or cancelled reads into a cached successful month',async()=>{
  let calls=0,fail=true;const fetchJson=async()=>{calls++;if(fail)throw new Error('offline');return packet([row()]);};
  const results:HistoryResult[]=[];
  const args={detail:detail([partition()]),end:'202608',count:1 as const,trade:'sale' as const,complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:(r:HistoryResult)=>results.push(r),fetchJson};
  await loadPropertyHistory(args);fail=false;await loadPropertyHistory(args);await loadPropertyHistory(args);
  expect(calls).toBe(2);expect(results.map(r=>r.status)).toEqual(['error','ready','ready']);
  const controller=new AbortController();controller.abort();await loadPropertyHistory({...args,signal:controller.signal});
  expect(results).toHaveLength(3);
  let cancelledCalls=0;const cancelled=new AbortController();
  const cancelledFetch=async()=>{cancelledCalls++;if(cancelledCalls===1)cancelled.abort();return packet([row()]);};
  await loadPropertyHistory({...args,fetchJson:cancelledFetch,signal:cancelled.signal});
  await loadPropertyHistory({...args,fetchJson:cancelledFetch});expect(cancelledCalls).toBe(2);
});
it('applies the same source and retained-row budgets on warm and cold history requests',async()=>{
  let calls=0;const fetchJson=async()=>{calls++;return packet([row(),row({id:`molit-sale:${'2'.repeat(64)}:1`})]);};
  const results:HistoryResult[]=[];
  const args={detail:detail([partition('202608',{source_rows:2,eligible_rows:2})]),end:'202608',count:1 as const,trade:'sale' as const,complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:(r:HistoryResult)=>results.push(r),fetchJson};
  await loadPropertyHistory(args);
  await loadPropertyHistory({...args,budget:{remaining:1000,rowsRemaining:1}});
  await loadPropertyHistory({...args,budget:{remaining:999}});
  await loadPropertyHistory({...args,budget:{remaining:1000,requestsRemaining:0}});
  expect(calls).toBe(1);expect(results.slice(1).map(r=>r.reason)).toEqual(['retention_budget','download_budget','request_budget']);
  expect(results.slice(1).every(r=>r.status==='missing'&&r.rows.length===0)).toBe(true);
});
