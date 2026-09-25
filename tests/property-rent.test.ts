import {expect,it} from 'vitest';
import {rentKindMatches,readRentKind} from '../shared/property-rent';
import {readPropertyView,transactionRows} from '../shared/property-view';
import {pricingSummary} from '../shared/property-pricing';
import {discoverPropertyComplexes,EMPTY_PROPERTY_DISCOVERY_FILTERS} from '../shared/property-discovery';
import {checkedDiscoveryFilters,parseSavedFilters,savedFiltersJson} from '../shared/property-saved-filters';
import {propertyFilterChips} from '../shared/property-filter-chips';
import {NATIONAL_AREA} from '../shared/property-area';
import type {PropertyTransaction,PropertyComplex} from '../shared/property';
const id='molit-apt:11710:test';
const row=(key:string,monthly:number|null,deposit:number):PropertyTransaction=>({
  id:key,trade_type:'rent',complex_id:id,area_m2:'84.99',contract_date:'2026-08-01',monthly_rent_krw:monthly,deposit_krw:deposit,price_krw:null,
  quality:'valid',statistics_eligible:true,cancellation:'not_provided',issues:[],lawd_code:'11710',source_lawd_code:'11710',
  complex_name:'검증 단지',legal_dong_code:null,legal_dong_name:null,lot_number:null,floor:5,build_year:2018,
  previous_deposit_krw:null,previous_monthly_rent_krw:null,contract_term:null,contract_type:null,renewal_right:null,
  registration_date:null,reported_at:null,source_updated_at:null,cancellation_date:null,source_id:'molit-apt-rent',
  source_input_sha256:'a'.repeat(64),retrieved_at:'2026-09-26T00:00:00Z',evidence_type:'official_report',observed_at:null,
});
const rows=[row('jeonse',0,900_000_000),row('monthly',2_000_000,100_000_000),row('unknown',null,500_000_000)];
const complex={id,name:'검증 단지',observed_name_variants:[],legal_dong_name:'가락동',build_year:2018} as unknown as PropertyComplex;

it('distinguishes an explicit zero monthly payment from missing, negative and non-finite values',()=>{
  expect(rows.filter(r=>rentKindMatches(r,'jeonse')).map(r=>r.id)).toEqual(['jeonse']);
  expect(rows.filter(r=>rentKindMatches(r,'monthly')).map(r=>r.id)).toEqual(['monthly']);
  expect(rows.filter(r=>rentKindMatches(r,'all'))).toHaveLength(3);
  for(const value of [-1,NaN,Infinity])expect(rentKindMatches(row('bad',value,1),'monthly')).toBe(false);
  expect(rentKindMatches({...rows[0],trade_type:'sale'},'monthly')).toBe(true);
});
it('uses the same rental condition for discovery, history rows, price cards and national-area medians',()=>{
  for(const kind of ['jeonse','monthly'] as const){
    const filtered=transactionRows(rows,{trade:'rent',complex:id,area:NATIONAL_AREA,cancelled:false,rentKind:kind});
    const pricing=pricingSummary(rows,{complexId:id,trade:'rent',area:'',rentKind:kind});
    const discovery=discoverPropertyComplexes([complex],rows,true,'rent',{...EMPTY_PROPERTY_DISCOVERY_FILTERS,rentKind:kind});
    expect(filtered.map(r=>r.id)).toEqual([kind]);
    expect(pricing.count).toBe(1);expect(pricing.nationalCount).toBe(1);
    expect(pricing.median).toBe(filtered[0].deposit_krw);expect(pricing.nationalMedian).toBe(filtered[0].deposit_krw);
    expect(discovery.items[0].count).toBe(1);expect(discovery.items[0].latest?.id).toBe(kind);
  }
});
it('preserves uncollected discovery as unknown rather than an empty subtype',()=>{
  const result=discoverPropertyComplexes([complex],[],false,'rent',{...EMPTY_PROPERTY_DISCOVERY_FILTERS,rentKind:'jeonse'});
  expect(result.items).toHaveLength(1);expect(result.items[0].count).toBeNull();expect(result.transactionFiltersPending).toBe(true);
});
it('restores rental shares while old, invalid and sale links keep their existing semantics',()=>{
  const period={from:'202507',to:'202609',latest_complete_month:'202608'};
  expect(readPropertyView('#trade=rent&rentKind=jeonse',period).rentKind).toBe('jeonse');
  expect(readPropertyView('#trade=rent&rentKind=monthly',period).rentKind).toBe('monthly');
  expect(readPropertyView('#trade=rent',period).rentKind).toBeUndefined();
  expect(readPropertyView('#trade=sale&rentKind=jeonse',period).rentKind).toBeUndefined();
  expect(readRentKind('__proto__')).toBe('all');
});
it('roundtrips rental saved conditions and rejects coerced enum values',()=>{
  const filters={...EMPTY_PROPERTY_DISCOVERY_FILTERS,rentKind:'monthly' as const,hasTrades:false};
  const record={id:'rent-test',name:'월세 찾기',region:'11710',trade:'rent' as const,filters,savedAt:'2026-09-26T00:00:00Z'};
  expect(parseSavedFilters(savedFiltersJson([record]))).toEqual([record]);
  expect(()=>checkedDiscoveryFilters({...filters,rentKind:['monthly']},'rent')).toThrow();
  const chip=propertyFilterChips(filters,'rent').find(item=>item.id==='rentKind');
  expect(chip?.label).toBe('월세');expect(chip?.clear).toEqual({rentKind:'all'});
});
