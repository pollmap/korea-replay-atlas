import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it,vi} from 'vitest';
import {M2_PER_PYEONG} from '../shared/property-area';
import type {PropertyComplex,PropertyRegionDetail} from '../shared/property';
import type {ComplexPriceSummary,SummaryPartition} from '../src/property-map-prices';
import {defaultSummaryArea,monthlySummaryPoints,summaryPrice} from '../src/property-monthly-summary';
import PropertyMonthlySummary from '../src/PropertyMonthlySummary';
import PropertyHistory from '../src/PropertyHistory';

const id='molit-apt:11710:11710-8865';
function row(month:string,area='84.99',extra:Partial<ComplexPriceSummary>={}):ComplexPriceSummary{return {complex_id:id,deal_month:month,trade_type:'sale',rent_kind:'sale',area_m2:area,transaction_count:2,latest_transaction_id:`sale:${month}:${area}`,latest_contract_date:`${month.slice(0,4)}-${month.slice(4)}-20`,latest_price_krw:1e9,latest_deposit_krw:null,latest_monthly_rent_krw:null,...extra};}
const selection={complexId:id,trade:'sale' as const,rentKind:'all' as const,area:'84-band',months:['202606','202607','202608']};
const partitions:SummaryPartition[]=[{deal_month:'202606',trade_type:'sale',status:'empty'},{deal_month:'202607',trade_type:'sale',status:'pending'},{deal_month:'202608',trade_type:'sale',status:'complete'}];
it('keeps actual zero, missing months and unavailable summary values distinct',()=>{
  const points=monthlySummaryPoints([row('202607'),row('202608')],partitions,selection);
  expect(points[0]).toMatchObject({count:0,latest:null});expect(points[1]).toMatchObject({count:null,latest:null});expect(points[2]).toMatchObject({count:2,latest:row('202608')});
});
it('counts all selected exact-area reports and plots an actual latest report rather than averaging monthly medians',()=>{
  const rows=[row('202608','84.1',{transaction_count:10,latest_price_krw:8e8,median_price_krw:7e8}),row('202608','84.99',{transaction_count:3,latest_transaction_id:'aaa',latest_contract_date:'2026-08-25',latest_price_krw:1.1e9,median_price_krw:9e8}),row('202608','59',{transaction_count:100,latest_price_krw:3e8})];
  const points=monthlySummaryPoints(rows,partitions,selection);expect(points[2].count).toBe(13);expect(points[2].latest?.latest_price_krw).toBe(1.1e9);
  expect(summaryPrice(points[2].latest!,'pyeong')).toBeCloseTo(1.1e9/84.99*M2_PER_PYEONG);
});
it('separates monthly-rent and jeonse reports while preserving genuine zero deposits',()=>{
  const monthly=row('202608','84.99',{trade_type:'rent',rent_kind:'monthly',latest_price_krw:null,latest_deposit_krw:0,latest_monthly_rent_krw:1e6});
  const jeonse=row('202608','84.99',{trade_type:'rent',rent_kind:'jeonse',latest_price_krw:null,latest_deposit_krw:5e8,latest_monthly_rent_krw:0});
  const p=[{deal_month:'202608',trade_type:'rent' as const,status:'complete'}];
  expect(monthlySummaryPoints([monthly,jeonse],p,{...selection,trade:'rent',rentKind:'monthly'})[2]).toMatchObject({count:2,latest:monthly});
  expect(summaryPrice(monthly,'total')).toBe(0);
});
it('chooses national area first and otherwise weights modal area by report count',()=>{
  const base={complexId:id,trade:'sale' as const,rentKind:'all' as const,months:['202606','202607','202608']};
  expect(defaultSummaryArea([row('202608','59',{transaction_count:100}),row('202608')],base)).toBe('84-band');
  expect(defaultSummaryArea([row('202606','102',{transaction_count:1}),row('202607','102',{transaction_count:1}),row('202608','59',{transaction_count:10})],base)).toBe('59');
});
const detail={release_id:'property-0123456789abcdef',lawd_code:'11710',period:{from:'200609',to:'202609',latest_complete_month:'202608'},partitions:[]} as unknown as PropertyRegionDetail;
const complex={id,name:'헬리오시티'} as PropertyComplex;
function props(){return {detail,complex,month:'202608',trade:'sale' as const,area:'84-band',range:12 as const,rentKind:'all' as const,basis:'total' as const,onBasis:vi.fn(),onArea:vi.fn(),onRange:vi.fn(),data:{rows:[row('202608')],partitions},state:'ready' as const,error:'',onRetry:vi.fn(),onFullRaw:vi.fn(),renderRaw:vi.fn(({month}:{month:string})=>createElement('p',null,`${month} 원문표`))};}
it('labels monthly representatives honestly and opens only the latest known month initially',()=>{
  const input=props(),html=renderToStaticMarkup(createElement(PropertyMonthlySummary,input));
  expect(html).toContain('월별 마지막 유효 계약 한 건의 가격');expect(html).toContain('전체 거래 점이나 월 중앙값이 아닙니다');expect(html).toContain('202608 원문표');
  expect(input.renderRaw).toHaveBeenCalledWith(expect.objectContaining({month:'202608',highlightTransaction:row('202608').latest_transaction_id,scatter:false}));
  expect(html).not.toContain('<polyline');expect(html).toContain('선택 기간 전체 원문 조회');
});
it('does not show an incomplete partition as a valid price even if a mismatched summary row exists',()=>{
  const input=props(),html=renderToStaticMarkup(createElement(PropertyMonthlySummary,{...input,data:{rows:[row('202607')],partitions}}));
  expect(html).not.toContain('<strong>10억</strong>');expect(html).toContain('확인된 거래 없음');
});
it('preserves the legacy short-period, review and 3D opt-out raw flows',()=>{
  const base={detail,complex,origin:'https://example.com',month:'202608',trade:'sale' as const,area:'84-band',onArea:vi.fn(),range:36 as const,onRange:vi.fn(),includeReview:false};
  for(const extra of [{range:3 as const},{includeReview:true},{summaryFirst:false}]){
    const html=renderToStaticMarkup(createElement(PropertyHistory,{...base,...extra}));
    expect(html).not.toContain('monthly-summary-history');expect(html).toContain('계산 기준·자료 안내');
  }
  const html=renderToStaticMarkup(createElement(PropertyHistory,base));
  expect(html).toContain('monthly-summary-history');expect(html).toContain('2026.08 계약 기록');
  expect(html).not.toContain('원문 거래 차트</h3>');
});

it('sums an inclusive area range for the shared chart without treating it as one floor plan',()=>{
  const rows=[row('202608','60',{transaction_count:2}),row('202608','84.99',{transaction_count:3}),row('202608','85',{transaction_count:4}),row('202608','85.01',{transaction_count:99})];
  const result=monthlySummaryPoints(rows,partitions,{...selection,area:'range:60:85'});
  expect(result[2].count).toBe(9);expect(result[2].latest?.area_m2).not.toBe('85.01');
});

it('places price and contract date before area controls, and source notes after the raw record table',()=>{
  const html=renderToStaticMarkup(createElement(PropertyMonthlySummary,props()));
  expect(html.indexOf('history-summary')).toBeLessThan(html.indexOf('history-controls'));
  expect(html.indexOf('history-controls')).toBeLessThan(html.indexOf('history-chart-toolbar'));
  expect(html.indexOf('202608 원문표')).toBeLessThan(html.indexOf('<summary>자료 기준'));
});
