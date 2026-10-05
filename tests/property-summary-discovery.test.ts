import {describe,expect,it} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import type {PropertyComplex} from '../shared/property';
import {EMPTY_PROPERTY_DISCOVERY_FILTERS,type PropertyDiscoveryFilters} from '../shared/property-discovery';
import {discoverPropertySummaryComplexes} from '../src/property-summary-discovery';
import type {ComplexPriceSummary,SummaryPartition} from '../src/property-map-prices';
import PropertyComplexList from '../src/PropertyComplexList';

const complex=(id:string,overrides:Partial<PropertyComplex>={}):PropertyComplex=>({id:`molit-apt:11110:${id}`,source_complex_id:id,lawd_code:'11110',name:`단지 ${id}`,legal_dong_code:'1111010100',legal_dong_name:'청운동',lot_number:'1',build_year:2008,position:null,identity_status:'source_apt_seq',source_ids:['molit-apt-sale-detail'],source_input_sha256:['a'.repeat(64)],first_contract_month:'202401',last_contract_month:'202609',observed_name_variants:[],address_conflict:false,...overrides});
const summary=(id:string,month:string,overrides:Partial<ComplexPriceSummary>={}):ComplexPriceSummary=>({complex_id:complex(id).id,deal_month:month,trade_type:'sale',rent_kind:'sale',area_m2:'84.99',transaction_count:3,latest_transaction_id:`molit-sale:${'a'.repeat(64)}:1`,latest_contract_date:`${month.slice(0,4)}-${month.slice(4)}-15`,latest_price_krw:400_000_000,latest_deposit_krw:null,latest_monthly_rent_krw:null,...overrides});
const partitions=(months:readonly string[],trade_type:'sale'|'rent'='sale'):SummaryPartition[]=>months.map(deal_month=>({deal_month,trade_type,status:'complete'}));
const filters=(overrides:Partial<PropertyDiscoveryFilters>={}):PropertyDiscoveryFilters=>({...EMPTY_PROPERTY_DISCOVERY_FILTERS,...overrides});
const search=(complexes:PropertyComplex[],rows:ComplexPriceSummary[],months:string[],condition:Partial<PropertyDiscoveryFilters>={},parts=partitions(months),trade:'sale'|'rent'='sale',area='')=>discoverPropertySummaryComplexes(complexes,rows,parts,{months,trade,area},filters(condition));

describe('selected-period complex list summaries',()=>{
  it('sums every selected month and exact area while retaining the latest real report without manufacturing a transaction',()=>{
    const rows=[summary('a','202401',{transaction_count:4}),summary('a','202609',{latest_price_krw:500_000_000,transaction_count:2}),summary('a','202609',{area_m2:'120',latest_contract_date:'2026-09-29',latest_price_krw:900_000_000,transaction_count:8}),summary('a','202301',{transaction_count:50})];
    const result=search([complex('a')],rows,['202401','202609'],{},undefined,'sale','84-band');
    expect(result.items[0]).toMatchObject({count:6,confirmedCount:6,latest:rows[1]});
    expect(result.items[0].latest).toBe(rows[1]);expect(result.items[0].latest).not.toHaveProperty('floor');expect(result.items[0].latest).not.toHaveProperty('cancellation');
  });
  it('filters the last real contract price instead of falling back to an older lower price or claiming an exact price-bounded count',()=>{
    const rows=[summary('a','202608',{transaction_count:4,latest_price_krw:300_000_000}),summary('a','202609',{transaction_count:2,latest_price_krw:700_000_000}),summary('b','202609',{transaction_count:5,latest_price_krw:500_000_000})];
    const result=search([complex('a'),complex('b')],rows,['202608','202609'],{priceMinEok:'4',priceMaxEok:'6'});
    expect(result.items.map(item=>item.complex.source_complex_id)).toEqual(['b']);expect(result.items[0].confirmedCount).toBe(5);expect(result.priceFiltered).toBe(true);
    expect(search([complex('a')],rows,['202608','202609'],{priceMinEok:'7',priceMaxEok:'7'}).items[0].count).toBe(6);
  });
  it('applies decimal local area limits and shared exact area to the same summary rows',()=>{
    const rows=[summary('a','202609',{area_m2:'84.99',transaction_count:2}),summary('a','202609',{area_m2:'84',transaction_count:6}),summary('a','202609',{area_m2:'85',transaction_count:9})];
    expect(search([complex('a')],rows,['202609'],{areaMinM2:'84.99',areaMaxM2:'84.99'}).items[0].count).toBe(2);
    expect(search([complex('a')],rows,['202609'],{},undefined,'sale','84').items[0].count).toBe(6);
    expect(search([complex('a')],rows,['202609'],{areaMinM2:'85',areaMaxM2:'84'}).errors).toHaveLength(1);
    expect(search([complex('a')],rows,['202609'],{priceMinEok:'Infinity'}).items).toHaveLength(0);
  });
  it('preserves zero rental deposits and monthly rent separately and filters only the chosen rental type',()=>{
    const monthly=summary('a','202609',{trade_type:'rent',rent_kind:'monthly',latest_price_krw:null,latest_deposit_krw:0,latest_monthly_rent_krw:1_500_000,transaction_count:2});
    const jeonse=summary('a','202609',{trade_type:'rent',rent_kind:'jeonse',latest_price_krw:null,latest_deposit_krw:300_000_000,latest_monthly_rent_krw:0,transaction_count:5});
    const result=search([complex('a')],[monthly,jeonse],['202609'],{rentKind:'monthly',priceMinEok:'0',priceMaxEok:'0'},partitions(['202609'],'rent'),'rent');
    expect(result.items[0]).toMatchObject({count:2,latest:monthly});
  });
  it('counts only audited complete months and distinguishes partial knowledge from a verified zero',()=>{
    const rows=[summary('a','202608',{transaction_count:3}),summary('a','202609',{transaction_count:100,latest_price_krw:999_000_000})];
    const partial=search([complex('a'),complex('b')],rows,['202608','202609'],{hasTrades:true},[{deal_month:'202608',trade_type:'sale',status:'complete'},{deal_month:'202609',trade_type:'sale',status:'failed'}]);
    expect(partial.items[0]).toMatchObject({count:null,confirmedCount:3,missingMonths:1,latest:rows[0]});
    expect(partial.items[1]).toMatchObject({count:null,confirmedCount:0,latest:null});
    expect(partial.transactionFiltersPending).toBe(true);
    expect(search([complex('a')],[],['202609']).items[0]).toMatchObject({count:0,confirmedCount:0});
    expect(search([complex('a')],[],['202609'],{hasTrades:true}).items).toHaveLength(0);
  });
  it('never turns a source-unavailable period into zero reported contracts',()=>{
    const result=search([complex('a')],[],['200609'],{hasTrades:true},[{deal_month:'200609',trade_type:'rent',status:'source_unavailable'}],'rent');
    expect(result.items[0]).toMatchObject({count:null,latest:null,sourceUnavailableMonths:1,missingMonths:0});expect(result.verifiedMonths).toBe(0);
  });
  it('keeps legal dong, aliases, source IDs and deterministic date/ID ties intact',()=>{
    const a=complex('a',{observed_name_variants:['Sunny Palace']}),b=complex('b',{legal_dong_name:'교남동'}),rows=[summary('a','202609',{latest_transaction_id:'z'}),summary('a','202609',{area_m2:'84',latest_transaction_id:'a'})];
    const result=search([a,b],rows,['202609'],{query:'sunny palace',dong:'청운동'});
    expect(result.items).toHaveLength(1);expect(result.items[0].complex).toBe(a);expect(result.items[0].latest).toBe(rows[1]);expect(rows[0].latest_transaction_id).toBe('z');
  });
  it('sorts by actual last exclusive unit price and leaves unavailable prices last',()=>{
    const rows=[summary('a','202609',{area_m2:'50',latest_price_krw:500_000_000}),summary('b','202609',{area_m2:'100',latest_price_krw:600_000_000})];
    expect(search([complex('missing'),complex('a'),complex('b')],rows,['202609'],{sort:'pyeong-low'}).items.map(item=>item.complex.source_complex_id)).toEqual(['b','a','missing']);
  });
});

describe('PC list period controls and pending state',()=>{
  it('provides working period choices and keeps unconnected investment conditions disabled',()=>{
    const html=renderToStaticMarkup(createElement(PropertyComplexList,{complexes:[complex('a')],rows:[],dataReady:true,trade:'sale',onSelect:()=>{},release:'property-a'.padEnd(25,'a'),savedFilterRegion:'11110',month:'202609',periodMonths:36,onPeriodMonths:()=>{},area:'84-band',onArea:()=>{}}));
    expect(html).toContain('aria-label="단지 목록 조회 기간"');expect(html).toContain('<option value="36" selected="">최근 3년</option>');expect(html).toContain('최근 20년');expect(html).toContain('선택 기간의 거래 요약을 불러오는 중');expect(html).not.toContain('현재 조건 0건');expect(html).toContain('aria-pressed="true"');
    expect(html).toContain('단지·투자 조건');expect(html).toContain('disabled="" title="공식 자료 연결 전">공급면적');expect(html).toContain('갭가격 · 연결 전');
  });
});

it('uses the shared area range when local list bounds are absent',()=>{
  const rows=[summary('a','202609',{area_m2:'60',transaction_count:2}),summary('a','202609',{area_m2:'85',transaction_count:3}),summary('a','202609',{area_m2:'86',transaction_count:99})];
  expect(search([complex('a')],rows,['202609'],{},undefined,'sale','range:60:85').items[0].count).toBe(5);
});

it('restores shared recent-price bounds in the list even before summaries load, and clearing overrides local values',()=>{
  const html=renderToStaticMarkup(createElement(PropertyComplexList,{complexes:[complex('a')],rows:[],dataReady:false,trade:'sale',onSelect:()=>{},latestPrice:{min:'3',max:'5'},area:'',release:'property-a'.padEnd(25,'a'),savedFilterRegion:'11110',month:'202609',periodMonths:36}));
  expect(html).toContain('매매 3–5억');
  expect(html).not.toContain('현재 조건 0건');
  expect(html).toContain('data-filter="price" data-active="true"');
  const cleared=renderToStaticMarkup(createElement(PropertyComplexList,{complexes:[],rows:[],dataReady:false,trade:'sale',onSelect:()=>{},latestPrice:{min:'',max:''},area:''}));
  expect(cleared).not.toContain('매매 3–5억');
});
