import {expect,it} from 'vitest';
import {historyMonths,historyDay,historySummary} from '../shared/property-history';
import {readPropertyView} from '../shared/property-view';
import {loadPropertyHistory,type HistoryResult} from '../src/property-history-loader';
import type {PropertyPartition,PropertyRegionDetail,PropertyTransaction} from '../shared/property';

const stamp='2026-09-20T00:00:00Z',release='property-0123456789abcdef',complex='molit-apt:11110:test';
function row(overrides:Partial<PropertyTransaction>={}):PropertyTransaction{return {
  id:`molit-sale:${'1'.repeat(64)}:1`,trade_type:'sale',complex_id:complex,complex_name:'테스트 단지',lawd_code:'11110',source_lawd_code:'11110',
  legal_dong_code:null,legal_dong_name:null,lot_number:null,area_m2:'84.99',floor:2,build_year:2000,contract_date:'2026-08-01',price_krw:100000000,
  deposit_krw:null,monthly_rent_krw:null,previous_deposit_krw:null,previous_monthly_rent_krw:null,contract_term:null,contract_type:null,renewal_right:null,
  registration_date:null,reported_at:null,source_updated_at:null,cancellation:'not_reported',cancellation_date:null,quality:'valid',issues:[],statistics_eligible:true,
  source_id:'molit-apt-sale-detail',source_input_sha256:'a'.repeat(64),retrieved_at:stamp,evidence_type:'official_report',observed_at:null,...overrides};}
function partition(month='202608',overrides:Partial<PropertyPartition>={}):PropertyPartition{return {lawd_code:'11110',deal_month:month,trade_type:'sale',status:'complete',source_rows:1,eligible_rows:1,retrieved_at:stamp,error_code:null,transactions:[{url:`/data/property/${release}/${month}.json`,sha256:'b'.repeat(64),bytes:1000}],...overrides};}
function detail(partitions:PropertyPartition[]):PropertyRegionDetail{return {schema_version:1,kind:'property-region',release_id:release,lawd_code:'11110',name:'테스트 지역',period:{from:'202606',to:'202609',latest_complete_month:'202608'},coverage:{expected:partitions.length,complete:partitions.length,empty:0,pending:0,partial:0,failed:0,historical_coverage:'current_codes_only_pending_effective_date_crosswalk'},metrics:[],partitions,complexes:null};}
function packet(transactions:PropertyTransaction[],deal_month='202608'){return {schema_version:1,kind:'property-transactions',release_id:release,lawd_code:'11110',deal_month,transactions};}

it('places different months on an elapsed-day axis and handles year/leap boundaries',()=>{
  expect(historyMonths('202601',3)).toEqual(['202511','202512','202601']);
  expect(historyDay('2024-03-01')-historyDay('2024-02-28')).toBe(2);
  expect(historyDay('2026-08-01')-historyDay('2026-07-01')).toBe(31);
  expect(()=>historyMonths('202613',3)).toThrow();
});
it('restores bounded history range in old/new shared URLs',()=>{
  const period={from:'202109',to:'202609',latest_complete_month:'202608'};
  expect(readPropertyView('#historyMonths=12',period).historyMonths).toBe(12);
  expect(readPropertyView('#historyMonths=999999',period).historyMonths).toBe(3);
  expect(readPropertyView('',period).historyMonths).toBe(3);
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
it('limits concurrent month jobs and rejects the byte budget before downloading',async()=>{
  let active=0,peak=0,calls=0;
  const make=()=>({detail:detail(['202606','202607','202608'].map(m=>partition(m))),end:'202608',count:3 as const,trade:'sale' as const,complex,origin:'https://example.com',signal:new AbortController().signal,onMonth:()=>undefined});
  await loadPropertyHistory({...make(),fetchJson:async ref=>{calls++;peak=Math.max(peak,++active);await new Promise(resolve=>setTimeout(resolve,1));active--;const month=ref.url!.split('/').pop()!.slice(0,6);return packet([row({contract_date:`${month.slice(0,4)}-${month.slice(4)}-01`})],month);}});
  expect(calls).toBe(3);expect(peak).toBe(2);
  const input=make();input.detail.partitions[0].transactions[0].bytes=25*1024*1024;
  await expect(loadPropertyHistory({...input,fetchJson:async()=>{throw new Error('must_not_download');}})).rejects.toThrow('24MiB');
});
