import {expect,it} from 'vitest';
import type {ComplexPriceSummary} from '../src/property-map-prices';
import type {PropertyTransaction} from '../shared/property';
import {distributionPrice,medianLinePaths,monthlyMedianPoints,rawMonthlyMedian,summaryMedian,weightedMedian} from '../src/property-monthly-median';
import {parseComplexPriceSummaries} from '../src/property-summary-client';
const id='molit-apt:11710:11710-8865';
const selection={complexId:id,trade:'sale' as const,rentKind:'all' as const,area:'84-band',months:['202606','202607','202608']};
const row=(area='84.99',extra:Partial<ComplexPriceSummary>={}):ComplexPriceSummary=>({complex_id:id,deal_month:'202608',trade_type:'sale',rent_kind:'sale',area_m2:area,transaction_count:2,latest_transaction_id:`molit-sale:${'a'.repeat(64)}:1`,latest_contract_date:'2026-08-01',latest_price_krw:900000000,latest_deposit_krw:null,latest_monthly_rent_krw:null,median_price_krw:800000000,...extra});
it('merges actual occurrence counts, never medians of area medians',()=>{
  const a=row('84.1',{transaction_count:10,median_price_krw:700000000,price_distribution:[[700000000,10]]}),b=row('84.99',{transaction_count:3,median_price_krw:900000000,price_distribution:[[900000000,3]]});
  expect(summaryMedian([a,b],'total')).toBe(700000000);
  expect(summaryMedian([{...a,price_distribution:undefined},{...b,price_distribution:undefined}],'total')).toBeNull();
});
it('preserves even medians, repeated prices, zero deposits and per-report area units',()=>{
  expect(weightedMedian([[0,1],[400000000,1]])).toBe(200000000);
  expect(weightedMedian([[0,4],[400000000,1]])).toBe(0);
  const rows=[row('50',{price_distribution:[[100000000,2]]}),row('100',{price_distribution:[[100000000,2]]})];
  expect(summaryMedian(rows,'m2')).toBe(1500000);
  expect(distributionPrice(100000001,'2','m2')).toBe(50000000);
  expect(distributionPrice(100000003,'2','m2')).toBe(50000002);
  expect(distributionPrice(121000000,'400','pyeong')).toBe(1000000);
});
it('reads an exact legacy area median but never substitutes the latest contract',()=>{
  expect(summaryMedian([row()],'total')).toBe(800000000);
  expect(summaryMedian([row('84.99',{median_price_krw:undefined})],'total')).toBeNull();
});
it('does not mix jeonse and monthly-rent deposit distributions',()=>{
  const a=row('84.99',{trade_type:'rent',rent_kind:'jeonse',price_distribution:[[100000000,2]]}),b=row('84.99',{trade_type:'rent',rent_kind:'monthly',price_distribution:[[0,2]]});
  expect(summaryMedian([a,b],'total')).toBeNull();
});
it('breaks the line at unknown, zero-trade and pre-source months',()=>{
  expect(medianLinePaths([1,2,null,3,4,null,5],1,5)).toHaveLength(2);
  expect(medianLinePaths([null,5,null],5,5)).toEqual([]);
  const points=monthlyMedianPoints([row()],selection.months.map((month,i)=>({deal_month:month,trade_type:'sale',status:i===1?'pending':'complete'})),selection,'total');
  expect(points.map(point=>point.median)).toEqual([null,null,800000000]);
  expect(points.map(point=>point.count)).toEqual([0,null,2]);
});
it('uses verified raw rows only when their complete month count matches the summary',()=>{
  const raw=(price:number,extra:Partial<PropertyTransaction>={}):PropertyTransaction=>({complex_id:id,trade_type:'sale',contract_date:'2026-08-01',area_m2:'84.99',price_krw:price,deposit_krw:null,monthly_rent_krw:null,cancellation:'not_reported',issues:[],lawd_code:'11710',source_lawd_code:'11710',statistics_eligible:true,...extra} as PropertyTransaction);
  const result={month:'202608',status:'ready' as const,rows:[raw(700000000),raw(900000000),raw(600000000,{cancellation:'cancelled'})]};
  expect(rawMonthlyMedian(result,selection,'total',2)).toBe(800000000);
  expect(rawMonthlyMedian(result,selection,'total',3)).toBeNull();
  expect(rawMonthlyMedian({...result,status:'error'},selection,'total',2)).toBeNull();
});
it('rejects corrupted distributions before they can change chart weights',()=>{
  const envelope={schema_version:1,kind:'property-complex-summaries',property_release_id:'property-0123456789abcdef',lawd_code:'11710',rows:[row('84.99',{price_distribution:[[800000000,2]]})]};
  expect(parseComplexPriceSummaries(envelope,'property-0123456789abcdef','11710','202608')).toHaveLength(1);
  for(const distribution of [[[800000000,1]],[[900000000,1],[800000000,1]],[[800000000,0]],[[800000001,2]],[[800000000,1],[800000000,1]]])expect(()=>parseComplexPriceSummaries({...envelope,rows:[row('84.99',{price_distribution:distribution as Array<[number,number]>})]},'property-0123456789abcdef','11710','202608')).toThrow();
});
