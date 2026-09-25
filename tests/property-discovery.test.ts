import {describe,expect,it} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {discoverPropertyComplexes,EMPTY_PROPERTY_DISCOVERY_FILTERS,propertyDiscoveryDongs,type PropertyDiscoveryFilters} from '../shared/property-discovery';
import type {PropertyComplex,PropertyTransaction} from '../shared/property';
import PropertyComplexList from '../src/PropertyComplexList';
import {propertyFilterChips} from '../shared/property-filter-chips';

const complex=(id:string,overrides:Partial<PropertyComplex>={}):PropertyComplex=>({
  id:`molit-apt:11110:${id}`,source_complex_id:id,lawd_code:'11110',name:'해 뜨는 아파트',
  legal_dong_code:'1111010100',legal_dong_name:'청운동',lot_number:'10-1',build_year:2008,position:null,
  identity_status:'source_apt_seq',source_ids:['molit-apt-sale-detail'],source_input_sha256:['a'.repeat(64)],
  first_contract_month:'202605',last_contract_month:'202609',observed_name_variants:['해뜨는'],address_conflict:false,...overrides,
});
const transaction=(id:string,complexId='a',overrides:Partial<PropertyTransaction>={}):PropertyTransaction=>({
  id,trade_type:'sale',complex_id:`molit-apt:11110:${complexId}`,complex_name:'해뜨는 아파트',lawd_code:'11110',source_lawd_code:'11110',
  legal_dong_code:'1111010100',legal_dong_name:'청운동',lot_number:'10-1',area_m2:'84.99',floor:10,build_year:2008,
  contract_date:'2026-08-12',price_krw:400_000_000,deposit_krw:null,monthly_rent_krw:null,
  previous_deposit_krw:null,previous_monthly_rent_krw:null,contract_term:null,contract_type:null,renewal_right:null,
  registration_date:null,reported_at:null,source_updated_at:null,cancellation:'not_reported',cancellation_date:null,
  quality:'valid',issues:[],statistics_eligible:true,source_id:'molit-apt-sale-detail',source_input_sha256:'a'.repeat(64),
  retrieved_at:'2026-09-20T00:00:00Z',evidence_type:'official_report',observed_at:null,...overrides,
});
const filters=(overrides:Partial<PropertyDiscoveryFilters>={}):PropertyDiscoveryFilters=>({...EMPTY_PROPERTY_DISCOVERY_FILTERS,...overrides});
const search=(complexes:PropertyComplex[],rows:PropertyTransaction[],overrides:Partial<PropertyDiscoveryFilters>={},ready=true,trade:'sale'|'rent'='sale')=>discoverPropertyComplexes(complexes,rows,ready,trade,filters(overrides));

describe('property discovery provenance and filtering',()=>{
  it('only hides no-trade complexes after the month is verified and never counts cancelled contracts',()=>{
    const complexes=[complex('a'),complex('b'),complex('c')];
    const rows=[transaction('valid','a'),transaction('cancelled','b',{cancellation:'cancelled'})];
    expect(search(complexes,rows,{hasTrades:true}).items.map(item=>item.complex.source_complex_id)).toEqual(['a']);
    const pending=search(complexes,rows,{hasTrades:true},false);
    expect(pending.items).toHaveLength(3);
    expect(pending.items.every(item=>item.count===null)).toBe(true);
    expect(pending.transactionFiltersPending).toBe(true);
  });
  it('removes one active filter without changing the other conditions or sort order',()=>{
    const state=filters({query:'해뜨는',dong:'청운동',priceMinEok:'3',priceMaxEok:'5',areaMinM2:'84',areaMaxM2:'84.99999',hasTrades:true,sort:'price-low'});
    const chips=propertyFilterChips(state,'sale');
    expect(chips.map(item=>item.label)).toEqual(['검색 해뜨는','청운동','매매 3–5억','전용 84㎡대','거래 있는 단지']);
    const next={...state,...chips.find(item=>item.id==='price')!.clear};
    expect(next).toMatchObject({priceMinEok:'',priceMaxEok:'',areaMinM2:'84',hasTrades:true,sort:'price-low',dong:'청운동'});
    expect(propertyFilterChips(filters({priceMaxEok:'0',hasTrades:false}),'rent').map(item=>item.label)).toEqual(['보증금 0억 이하']);
    expect(propertyFilterChips(filters({sort:'count'}),'sale')).toEqual([]);
  });
  it('sorts by latest exclusive unit price rather than total and leaves unknown prices last',()=>{
    const rows=[transaction('a','a',{area_m2:'50',price_krw:5e8}),transaction('b','b',{area_m2:'100',price_krw:6e8})];
    expect(search([complex('a'),complex('b'),complex('c')],rows,{sort:'pyeong-low'}).items.map(item=>item.complex.source_complex_id)).toEqual(['b','a','c']);
    expect(search([complex('a'),complex('b'),complex('c')],rows,{sort:'pyeong-high'}).items.map(item=>item.complex.source_complex_id)).toEqual(['a','b','c']);
  });
  it('matches Korean whitespace variants, known aliases and legal dong without inferring position',()=>{
    const a=complex('a'),b=complex('b',{name:'다른단지',observed_name_variants:['ABC Palace'],legal_dong_name:'교남동'});
    expect(search([a,b],[],{query:' 해뜨는아파트 '}).items.map(row=>row.complex.id)).toEqual([a.id]);
    expect(search([a,b],[],{query:'abc palace'}).items.map(row=>row.complex.id)).toEqual([b.id]);
    expect(search([a,b],[],{query:'교 남 동'}).items.map(row=>row.complex.id)).toEqual([b.id]);
    expect(search([a,b],[],{query:'청운동 해뜨는'}).items.map(row=>row.complex.id)).toEqual([a.id]);
    expect(search([a],[]).items[0].complex.position).toBeNull();
  });
  it('keeps homonymous complexes distinct by source ID, even with an identical address',()=>{
    const result=search([complex('a'),complex('b')],[transaction('1','a'),transaction('2','a'),transaction('3','b',{price_krw:900_000_000})]);
    expect(result.items.map(row=>[row.complex.source_complex_id,row.count,row.latest?.price_krw])).toEqual([['a',2,400_000_000],['b',1,900_000_000]]);
  });
  it('does not reuse stale rows or describe unverified months as zero transactions',()=>{
    const result=search([complex('a')],[transaction('stale')],{priceMinEok:'100'},false);
    expect(result.items).toMatchObject([{count:null,latest:null}]);expect(result.transactionFiltersPending).toBe(true);
    expect(search([complex('a')],[]).items).toMatchObject([{count:0,latest:null}]);
  });
  it('excludes cancelled, cancellation-unknown, invalid and wrong-scope records using shared eligibility',()=>{
    const rows=[transaction('ok'),transaction('cancel','a',{cancellation:'cancelled'})];
    rows.push(transaction('unknown','a',{cancellation:'unknown'}),transaction('bad','a',{quality:'invalid',issues:[{field:'dealAmount',code:'invalid_format'}]}),transaction('scope','a',{source_lawd_code:'99999'}),transaction('unlinked','a',{complex_id:null}));
    expect(search([complex('a')],rows).items[0].count).toBe(1);
  });
  it('uses inclusive integer-won bounds and shows the latest matching area, never an unrelated larger transaction',()=>{
    const rows=[transaction('older','a',{price_krw:450_010_000,area_m2:'84.99'}),transaction('newer','a',{contract_date:'2026-08-28',price_krw:900_000_000,area_m2:'120'})];
    const result=search([complex('a')],rows,{priceMinEok:'4.5001',priceMaxEok:'4.5001',areaMinM2:'84.99',areaMaxM2:'84.99'});
    expect(result.items[0]).toMatchObject({count:1,latest:{id:'older',area_m2:'84.99',price_krw:450_010_000}});
    expect(search([complex('a')],rows,{priceMaxEok:'4.5'}).items).toHaveLength(0);
  });
  it('treats valid zero rental deposits as amounts and never combines deposits with monthly rent',()=>{
    const row=transaction('rent','a',{trade_type:'rent',cancellation:'not_provided',source_id:'molit-apt-rent',price_krw:null,deposit_krw:0,monthly_rent_krw:1_500_000});
    expect(search([complex('a')],[row],{priceMinEok:'0',priceMaxEok:'0'},true,'rent').items[0]).toMatchObject({count:1,latest:{deposit_krw:0,monthly_rent_krw:1_500_000}});
    expect(search([complex('a')],[row],{priceMinEok:'0.01'},true,'rent').items).toHaveLength(0);
    expect(search([complex('a')],[row],{},true,'sale').items[0].count).toBe(0);
  });
  it('retains valid contracts with nonstandard lot descriptions consistent with source policy',()=>{
    const row=transaction('lot','a',{quality:'invalid',issues:[{field:'jibun',code:'invalid_format'}]});
    expect(search([complex('a')],[row]).items[0].count).toBe(1);
  });
  it('filters the source construction year without substituting missing values or years from transactions',()=>{
    const result=search([complex('a',{build_year:null}),complex('b',{build_year:2020}),complex('c',{build_year:2021})],[transaction('a','a',{build_year:2020})],{buildYearMin:'2020',buildYearMax:'2020'});
    expect(result.items.map(row=>row.complex.source_complex_id)).toEqual(['b']);
  });
  it.each([
    {priceMinEok:'-1'}, {priceMaxEok:'Infinity'}, {areaMinM2:'NaN'}, {priceMinEok:'5',priceMaxEok:'4'},
    {areaMinM2:'85',areaMaxM2:'84'}, {buildYearMin:'2021',buildYearMax:'2020'}, {buildYearMin:'2000.5'},
    {priceMinEok:'90000001'}, {areaMaxM2:'10001'}, {buildYearMax:'10000'}, {priceMinEok:'1e2'},
  ])('rejects malformed or inverted ranges instead of silently applying no filter: %s',overrides=>{
    const result=search([complex('a')],[transaction('a')],overrides);
    expect(result.errors.length).toBeGreaterThan(0);expect(result.items).toHaveLength(0);
  });
  it('sorts by latest matching date or count with deterministic ties and does not mutate inputs',()=>{
    const complexes=[complex('b',{name:'나무'}),complex('a',{name:'가람'}),complex('c',{name:'다온'})];
    const rows=[transaction('old-1','a'),transaction('old-2','a'),transaction('recent-z','b',{contract_date:'2026-08-29'}),transaction('recent-a','b',{contract_date:'2026-08-29'}),transaction('last','c',{contract_date:'2026-08-30'})];
    expect(search(complexes,rows).items.map(row=>row.complex.source_complex_id)).toEqual(['c','b','a']);
    expect(search(complexes,rows,{sort:'count'}).items.map(row=>row.complex.source_complex_id)).toEqual(['b','a','c']);
    expect(search(complexes,rows,{sort:'name'}).items.map(row=>row.complex.source_complex_id)).toEqual(['a','b','c']);
    expect(search(complexes,rows).items.find(row=>row.complex.source_complex_id==='b')?.latest?.id).toBe('recent-a');
    expect(complexes[0].source_complex_id).toBe('b');expect(rows[0].id).toBe('old-1');
  });
  it('uses exact legal dong selection and deduplicates options without manufacturing a missing dong',()=>{
    const complexes=[complex('a'),complex('b'),complex('c',{legal_dong_name:'청운'}),complex('d',{legal_dong_name:null})];
    expect(propertyDiscoveryDongs(complexes)).toEqual(['청운','청운동']);
    expect(search(complexes,[],{dong:'청운동'}).items).toHaveLength(2);
  });
  it('sorts latest matching sale prices in both directions with missing contracts last',()=>{
    const complexes=[complex('missing'),complex('a'),complex('b')];
    const rows=[transaction('old','a',{price_krw:990_000_000}),transaction('latest','a',{price_krw:300_000_000,contract_date:'2026-08-30'}),transaction('b','b',{price_krw:500_000_000})];
    expect(search(complexes,rows,{sort:'price-low'}).items.map(r=>r.complex.source_complex_id)).toEqual(['a','b','missing']);
    expect(search(complexes,rows,{sort:'price-high'}).items.map(r=>r.complex.source_complex_id)).toEqual(['b','a','missing']);
  });
  it('orders zero rental deposits before positive deposits without adding monthly rent',()=>{
    const complexes=[complex('missing'),complex('zero'),complex('positive')];
    const rental={trade_type:'rent',cancellation:'not_provided',source_id:'molit-apt-rent',price_krw:null} as const;
    const rows=[transaction('z','zero',{...rental,deposit_krw:0,monthly_rent_krw:2_000_000}),transaction('p','positive',{...rental,deposit_krw:1_000_000,monthly_rent_krw:0})];
    expect(search(complexes,rows,{sort:'price-low'},true,'rent').items.map(r=>r.complex.source_complex_id)).toEqual(['zero','positive','missing']);
    expect(search(complexes,rows,{sort:'price-high'},true,'rent').items.map(r=>r.complex.source_complex_id)).toEqual(['positive','zero','missing']);
  });
});

describe('property discovery presentation',()=>{
  it('renders no more than 40 actionable complex records and includes real paging controls',()=>{
    const html=renderToStaticMarkup(createElement(PropertyComplexList,{complexes:Array.from({length:81},(_,i)=>complex(String(i))),rows:[],dataReady:true,trade:'sale',onSelect:()=>{}}));
    expect(html.match(/class="discovery-open"/g)).toHaveLength(40);
    expect(html).toContain('단지 목록 페이지');expect(html).toContain('1 / 3');expect(html).toContain('81개 단지');
  });
  it('renders rental deposit and monthly rent with independent labels and exact latest-contract area',()=>{
    const row=transaction('rent','a',{trade_type:'rent',cancellation:'not_provided',source_id:'molit-apt-rent',price_krw:null,deposit_krw:0,monthly_rent_krw:1_500_000});
    const html=renderToStaticMarkup(createElement(PropertyComplexList,{complexes:[complex('a')],rows:[row],dataReady:true,trade:'rent',onSelect:()=>{}}));
    expect(html).toContain('최근 보증금');expect(html).toContain('0만원');expect(html).toContain('월세 150만원');expect(html).toContain('전용 84.99㎡');
  });
  it('never renders 0 transactions from unavailable data and preserves the selected source identity',()=>{
    const html=renderToStaticMarkup(createElement(PropertyComplexList,{complexes:[complex('a')],rows:[],dataReady:false,trade:'sale',selectedId:complex('a').id,onSelect:()=>{}}));
    expect(html).toContain('신고 건수 미확인');expect(html).not.toContain('현재 조건 0건');expect(html).toContain('aria-pressed="true"');
  });
});
