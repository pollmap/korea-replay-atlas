import {expect,it} from 'vitest';
import {areaMatches,exclusivePyeong,M2_PER_PYEONG,NATIONAL_AREA} from '../shared/property-area';
import {pricingSummary,transactionPrice} from '../shared/property-pricing';
import {readPropertyView,transactionRows} from '../shared/property-view';
import {summarizePropertyTransactions,type PropertyTransaction} from '../shared/property';

const complexId='molit-apt:11710:test';
function row(id:string,area:string,price:number,overrides:Partial<PropertyTransaction>={}):PropertyTransaction {
  return {id,trade_type:'sale',complex_id:complexId,complex_name:'단지',lawd_code:'11710',source_lawd_code:'11710',legal_dong_code:null,legal_dong_name:null,lot_number:null,
    area_m2:area,floor:5,build_year:2000,contract_date:'2026-08-01',price_krw:price,deposit_krw:null,monthly_rent_krw:null,previous_deposit_krw:null,previous_monthly_rent_krw:null,
    contract_term:null,contract_type:null,renewal_right:null,registration_date:null,reported_at:null,source_updated_at:null,cancellation:'not_reported',cancellation_date:null,
    quality:'valid',issues:[],statistics_eligible:true,source_id:'molit-apt-sale-detail',source_input_sha256:'a'.repeat(64),retrieved_at:'2026-09-20T00:00:00Z',evidence_type:'official_report',observed_at:null,...overrides};
}
it('uses exclusive rather than supplied area, preserves zero deposit and rejects missing area',()=>{
  expect(transactionPrice(row('a','100',1e9),'pyeong')).toBeCloseTo(1e7*M2_PER_PYEONG);
  expect(transactionPrice(row('a','100',1e9),'m2')).toBe(1e7);
  expect(transactionPrice(row('a','0',1e9),'pyeong')).toBeNull();
  expect(transactionPrice(row('a','84',1e9,{area_m2:null}),'pyeong')).toBeNull();
  expect(transactionPrice(row('a','84',1e9,{trade_type:'rent',deposit_krw:0}),'pyeong')).toBe(0);
  expect(exclusivePyeong('84')).toBe('25.4');
});
it('keeps the 84 band exact and does not convert 59 or 85 m² prices into national-area observations',()=>{
  for(const value of ['84','84.1','84.99999'])expect(areaMatches(value,NATIONAL_AREA)).toBe(true);
  for(const value of ['59','83.99999','85','NaN',null])expect(areaMatches(value,NATIONAL_AREA)).toBe(false);
  const rows=[row('a','84',8e8),row('b','84.99',1e9),row('c','59',4e8),row('d','85',2e9)];
  expect(pricingSummary(rows,{complexId,trade:'sale',area:''})).toMatchObject({nationalCount:2,nationalMedian:9e8});
  expect(pricingSummary([rows[2]],{complexId,trade:'sale',area:''})).toMatchObject({nationalCount:0,nationalMedian:null});
});
it('excludes cancelled, unknown and other-complex records; preserves distinct equal reports',()=>{
  const rows=[row('a','84',8e8),row('b','84',8e8),row('c','84',9e9,{cancellation:'cancelled'}),row('d','84',9e9,{statistics_eligible:false,issues:[{field:'dealAmount',code:'invalid_format'}]}),row('e','84',9e9,{complex_id:'molit-apt:11710:other'}),row('f','84',9e9,{cancellation:'unknown'})];
  expect(pricingSummary(rows,{complexId,trade:'sale',area:'84'})).toMatchObject({count:2,median:8e8,nationalCount:2});
});
it('takes a median of row-level unit prices rather than dividing medians',()=>{
  const rows=[row('a','50',5e8),row('b','100',4e8),row('c','200',6e8)];
  expect(pricingSummary(rows,{complexId,trade:'sale',area:''}).perPyeong).toBeCloseTo(4e6*M2_PER_PYEONG);
});
it('preserves an 84-band share and uses the same filter for detail and comparison',()=>{
  const rows=[row('a','84',8e8),row('b','84.99',1e9),row('c','59',4e8)];
  expect(readPropertyView('#area=84-band',{from:'202601',to:'202609',latest_complete_month:'202608'}).area).toBe(NATIONAL_AREA);
  expect(transactionRows(rows,{trade:'sale',complex:complexId,area:NATIONAL_AREA,cancelled:false})).toHaveLength(2);
  expect(summarizePropertyTransactions(rows,{complex_id:complexId,trade_type:'sale',area_m2:NATIONAL_AREA,from:'2026-08-01',to:'2026-08-31'})).toMatchObject({count:2,median_price_krw:9e8});
});
