import {describe,it,expect} from 'vitest';
import type {FeatureCollection,Point} from 'geojson';
import {propertyMapPrices,propertyMapFilterKey,type ComplexPriceSummary,type MapPriceData} from '../src/property-map-prices';
import {parseComplexPriceSummaries,parseComplexMonthSummaryPack} from '../src/property-summary-client';
import {readPropertyView} from '../shared/property-view';
const release='property-8deba5b9951e48da',complex='molit-apt:11710:apt-1';
const base:FeatureCollection<Point>={type:'FeatureCollection',features:[{type:'Feature',geometry:{type:'Point',coordinates:[127.1,37.5]},properties:{name:'단지',property_complex_id:complex,property_release_id:release,recent_sale_label:'최근 신고 99억'}}]};
const view=readPropertyView('#regionCode=11710&trade=sale&month=202608&historyMonths=3&area=84-band',{from:'200609',to:'202610',latest_complete_month:'202609'});
const report:ComplexPriceSummary={complex_id:complex,deal_month:'202608',trade_type:'sale',rent_kind:'sale',area_m2:'84.99',transaction_count:1,latest_transaction_id:`molit-sale:${'1'.repeat(64)}:1`,latest_contract_date:'2026-08-15',latest_price_krw:900_000_000,latest_deposit_krw:null,latest_monthly_rent_krw:null};
const ready:MapPriceData={state:'ready',rows:[report],partitions:['202606','202607','202608'].map(deal_month=>({deal_month,trade_type:'sale',status:'complete'}))};
const labels=(v=view,data=ready)=>propertyMapPrices(base,release,v,data).features[0].properties!;
describe('selected-condition map prices',()=>{
  it('uses the same exact area and period instead of a static latest-month label',()=>{
    expect(labels()).toMatchObject({filtered_price_label:'9억',filtered_label:'9억\n전용 25.7평',filtered_area_m2:'84.99',filtered_contract_date:'2026-08-15'});
    expect(labels({...view,month:'202607'}).filtered_label).toBe('미수집 포함');
    expect(labels({...view,area:'59.99'}).filtered_label).toBe('해당 거래 없음');
    expect(labels()).not.toHaveProperty('filtered_label','99억');
  });
  it('distinguishes missing, failed, loading, and a verified zero',()=>{
    for(const [state,text] of [['loading','조건 확인 중'],['missing','조건 미연결'],['error','조회 실패']] as const)expect(labels(view,{...ready,state,rows:[]}).filtered_label).toBe(text);
    expect(labels(view,{...ready,rows:[]}).filtered_label).toBe('해당 거래 없음');
    expect(labels(view,{...ready,rows:[],partitions:[]}).filtered_label).toBe('미수집 포함');
  });
  it('never generates supply-area pyeong or sale prices for rentals',()=>{
    expect(labels({...view,areaBasis:'supply'}).filtered_label).toBe('공급면적 미연결');
    const rental={...report,trade_type:'rent' as const,rent_kind:'monthly' as const,latest_price_krw:null,latest_deposit_krw:300_000_000,latest_monthly_rent_krw:2_500_000};
    expect(labels({...view,trade:'rent',rentKind:'monthly'},{...ready,rows:[rental]}).filtered_price_label).toBe('3억 / 월 250만');
    expect(labels({...view,trade:'rent',rentKind:'jeonse'},{...ready,rows:[rental]}).filtered_label).toBe('미수집 포함');
  });
  it('pins filter keys independently of decorative/detail changes',()=>{
    expect(propertyMapFilterKey(release,view)).toBe(propertyMapFilterKey(release,{...view,detailSection:'facts',priceBasis:'pyeong',markerDisplay:'name'}));
    expect(propertyMapFilterKey(release,view)).not.toBe(propertyMapFilterKey(release,{...view,area:'59'}));
    expect(labels().property_filter_key).toBe(propertyMapFilterKey(release,view));
  });
});
describe('summary contract audit',()=>{
  const payload={schema_version:1,kind:'property-complex-summaries',property_release_id:release,lawd_code:'11710',rows:[report]};
  it('rejects a different release, month, duplicate group, and missing source money',()=>{
    expect(parseComplexPriceSummaries(payload,release,'11710','202608')).toHaveLength(1);
    for(const value of [{...payload,property_release_id:'other'},{...payload,rows:[report,report]},{...payload,rows:[{...report,latest_price_krw:null}]}])expect(()=>parseComplexPriceSummaries(value,release,'11710','202608')).toThrow();
    expect(()=>parseComplexPriceSummaries(payload,release,'11710','202607')).toThrow();
  });
  it('keeps month packs scoped and rejects an invalid date or missing month',()=>{
    const pack={...payload,kind:'property-complex-summary-month-pack',months:[{deal_month:'202608',rows:[report]}]};
    expect(parseComplexMonthSummaryPack(pack,release,'11710','202608')).toEqual([report]);
    expect(parseComplexMonthSummaryPack(pack,release,'11710',null)).toEqual([report]);
    expect(()=>parseComplexMonthSummaryPack(pack,release,'11710','202607')).toThrow();
    expect(()=>parseComplexPriceSummaries({...payload,rows:[{...report,latest_contract_date:'2026-08-32'}]},release,'11710','202608')).toThrow();
    expect(()=>parseComplexMonthSummaryPack({...pack,months:[...pack.months,...pack.months]},release,'11710','202608')).toThrow();
  });
});

it('keeps the same shared range in map prices and rejects out-of-range reports',()=>{
  const selected=readPropertyView('#regionCode=11710&month=202608&historyMonths=3&area=range:60:85',{from:'200610',to:'202610',latest_complete_month:'202609'});
  expect(selected.area).toBe('range:60:85');
  expect(labels(selected).filtered_price_label).toBe('9억');
  expect(labels({...selected,area:'range::60'}).filtered_label).toBe('해당 거래 없음');
  expect(propertyMapFilterKey(release,selected)).not.toBe(propertyMapFilterKey(release,{...selected,area:'range::60'}));
});
