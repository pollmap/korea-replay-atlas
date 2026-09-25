import {expect,it} from 'vitest';
import {readPropertyView,transactionCsv,moneyLabel,propertyRowsView,propertyAreaOptions,transactionRows} from '../shared/property-view';
import type {PropertyTransaction} from '../shared/property';
const period={from:'202109',to:'202609',latest_complete_month:'202608'};
it('restores a province search without inventing a district code',()=>{
  expect(readPropertyView('#regionQuery='+encodeURIComponent('서울특별시'),period)).toMatchObject({regionQuery:'서울특별시',region:''});
  expect(readPropertyView('#regionQuery='+encodeURIComponent('x'.repeat(81)),period).regionQuery).toBeUndefined();
  expect(readPropertyView('#regionQuery=%00서울',period).regionQuery).toBeUndefined();
});
it('restores valid filters and bounds malicious or unavailable share parameters',()=>{
  expect(readPropertyView('#regionCode=11110&month=202602&trade=rent&area=84.99&complex=molit-apt:11110:old-code',period)).toMatchObject({region:'11110',month:'202602',trade:'rent',area:'84.99',complex:'molit-apt:11110:old-code'});
  for(const month of ['202613','202010','hello','202699'])expect(readPropertyView('#month='+month,period).month).toBe('202608');
  expect(readPropertyView('#compareRegions=11110,11110,12345,23456,34567&area=NaN&complex=../../x',period)).toMatchObject({area:'',complex:'',compare:['11110','12345','23456']});
});
it('exports literal spreadsheet cells while retaining integer won',()=>{
  const text=transactionCsv([{complex_name:'=HYPERLINK("bad")',price_krw:123450000,contract_date:'2026-08-01'} as PropertyTransaction]);
  expect(text.startsWith('\uFEFF')).toBe(true);expect(text).toContain('"\'=HYPERLINK(""bad"")"');expect(text).toContain('"123450000"');
  expect(moneyLabel(123450000)).toBe('1억 2,345만');expect(moneyLabel(null)).toBe('자료 없음');
});

it.each(['pending','partial','failed',undefined] as const)('keeps %s partition counts unknown even if old rows exist',status=>{
  const view=propertyRowsView({status,loading:false,loaded:true,error:'',count:12});
  expect(view.state).toBe('unavailable');expect(view.count).toBeNull();expect(view.message).not.toMatch(/0건|거래가 없습니다/);
});

it('distinguishes completed zero from a loading, previous-query, or failed response',()=>{
  const input={status:'complete' as const,loading:false,loaded:true,error:'',count:0};
  expect(propertyRowsView(input)).toMatchObject({state:'ready',count:0});
  expect(propertyRowsView({...input,status:'empty'})).toMatchObject({state:'ready',count:0});
  expect(propertyRowsView({...input,loading:true})).toMatchObject({state:'loading',count:null});
  expect(propertyRowsView({...input,loaded:false,count:17})).toMatchObject({state:'loading',count:null});
  expect(propertyRowsView({...input,error:'offline'})).toMatchObject({state:'error',count:null});
});

it('keeps an absent pinned area explicit when a month changes and avoids claiming an uncollected area is empty',()=>{
  const restored=readPropertyView('#regionCode=11110&month=202607&area=108.55',period);
  expect(restored.area).toBe('108.55');
  expect(propertyAreaOptions(['84.99','84.99'],restored.area,true)).toEqual([
    {value:'84-band',label:'국평 · 전용 84㎡대'},
    {value:'84.99',label:'84.99 ㎡'},{value:'108.55',label:'108.55 ㎡ · 현재 기간 거래 없음'},
  ]);
  expect(propertyAreaOptions([],restored.area,false)).toEqual([{value:'84-band',label:'국평 · 전용 84㎡대 · 자료 확인 전'},{value:'108.55',label:'108.55 ㎡ · 자료 확인 전'}]);
  expect(propertyAreaOptions(['108.55'],restored.area,true)).toEqual([{value:'84-band',label:'국평 · 전용 84㎡대 · 현재 기간 거래 없음'},{value:'108.55',label:'108.55 ㎡'}]);
});

it('restores the review filter without changing source identity, trade, complex or area conditions',()=>{
  const base:PropertyTransaction={id:'test',trade_type:'sale',complex_id:'molit-apt:11110:test',complex_name:'검증용 단지',lawd_code:'11110',source_lawd_code:'11110',
    legal_dong_code:null,legal_dong_name:null,lot_number:null,area_m2:'108.55',floor:null,build_year:null,contract_date:'2026-08-01',price_krw:100_000_000,
    deposit_krw:null,monthly_rent_krw:null,previous_deposit_krw:null,previous_monthly_rent_krw:null,contract_term:null,contract_type:null,renewal_right:null,
    registration_date:null,reported_at:null,source_updated_at:null,cancellation:'not_reported',cancellation_date:null,quality:'valid',issues:[],statistics_eligible:true,
    source_id:'molit-apt-sale-detail',source_input_sha256:'a'.repeat(64),retrieved_at:'2026-09-20T00:00:00Z',evidence_type:'official_report',observed_at:null};
  const rows:PropertyTransaction[]=[{...base,id:'valid'},{...base,id:'cancelled',cancellation:'cancelled'},
    {...base,id:'other-area',area_m2:'84.99'},{...base,id:'other-complex',complex_id:'molit-apt:11110:other'}];
  const hash='#regionCode=11110&month=202608&trade=sale&area=108.55&complex=molit-apt:11110:test';
  const selection=(suffix:string)=>{const view=readPropertyView(hash+suffix,period);return transactionRows(rows,{trade:view.trade,complex:view.complex,area:view.area,cancelled:view.includeReview}).map(row=>row.id);};
  expect(selection('')).toEqual(['valid']);expect(selection('&review=include')).toEqual(['cancelled','valid']);
  expect(selection('&review=anything')).toEqual(['valid']);
});
