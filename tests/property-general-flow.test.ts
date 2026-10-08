import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import {EMPTY_PROPERTY_DISCOVERY_FILTERS,discoverPropertyComplexes} from '../shared/property-discovery';
import type {PropertyComplex,PropertyRegion,PropertyTransaction} from '../shared/property';
import {propertyListFilters,readPropertyView,transactionRows} from '../shared/property-view';
import {areaFromBounds} from '../shared/property-area';
import {generalPropertyFilters,propertyBudgetLabel,propertyFilterDraftContext,currentPropertyFilterPanel} from '../src/property-general-filters';
import PropertyFilterBar from '../src/PropertyFilterBar';
import PropertyComplexList from '../src/PropertyComplexList';

const period={from:'200610',to:'202610',latest_complete_month:'202609'};
const complex:PropertyComplex={id:'molit-apt:11710:test',source_complex_id:'test',lawd_code:'11710',name:'검증 아파트',legal_dong_code:'1171010700',legal_dong_name:'가락동',lot_number:'10',build_year:2018,position:null,identity_status:'source_apt_seq',source_ids:['molit-apt-sale-detail'],source_input_sha256:['a'.repeat(64)],first_contract_month:'202608',last_contract_month:'202609',observed_name_variants:[],address_conflict:false};
const row=(id:string,date:string,price:number):PropertyTransaction=>({id,trade_type:'sale',complex_id:complex.id,complex_name:complex.name,lawd_code:'11710',source_lawd_code:'11710',legal_dong_code:complex.legal_dong_code,legal_dong_name:'가락동',lot_number:'10',area_m2:'84.99',floor:7,build_year:2018,contract_date:date,price_krw:price,deposit_krw:null,monthly_rent_krw:null,previous_deposit_krw:null,previous_monthly_rent_krw:null,contract_term:null,contract_type:null,renewal_right:null,registration_date:null,reported_at:null,source_updated_at:null,cancellation:'not_reported',cancellation_date:null,quality:'valid',issues:[],statistics_eligible:true,source_id:'molit-apt-sale-detail',source_input_sha256:'a'.repeat(64),retrieved_at:'2026-10-01T00:00:00Z',evidence_type:'official_report',observed_at:null});

it('derives common controls and the candidate list from the same pinned shared conditions',()=>{
 const view=readPropertyView('#regionCode=11710&legalDong=가락동&trade=rent&rentKind=jeonse&month=202609&historyMonths=36&area=84-band&latestPriceMinEok=3&latestPriceMaxEok=8&buildYearMin=2016&listSort=price-low&complex=molit-apt%3A11710%3Atest',period);
 const filters=generalPropertyFilters(propertyListFilters(view),{area:view.area,latestPrice:{min:view.latestPriceMinEok??'',max:view.latestPriceMaxEok??''},dong:view.legalDong,rentKind:view.rentKind});
 expect(filters).toMatchObject({dong:'가락동',rentKind:'jeonse',priceMinEok:'3',priceMaxEok:'8',areaMinM2:'84',areaMaxM2:'84.99999',buildYearMin:'2016',sort:'price-low'});
 expect(areaFromBounds(filters.areaMinM2,filters.areaMaxM2)).toBe(view.area);
 expect(view).toMatchObject({complex:complex.id,historyMonths:36,month:'202609'});
});
it('qualifies candidates by the latest price without deleting older expensive history rows',()=>{
 const rows=[row('older','2026-08-01',9e8),row('recent','2026-09-20',4e8)];
 const filters=generalPropertyFilters(EMPTY_PROPERTY_DISCOVERY_FILTERS,{area:'84-band',latestPrice:{min:'3',max:'5'},dong:'가락동'});
 const candidates=discoverPropertyComplexes([complex],rows,true,'sale',filters);
 expect(candidates.items[0]).toMatchObject({count:2,latest:{id:'recent',price_krw:4e8}});
 expect(transactionRows(rows,{trade:'sale',complex:complex.id,area:'84-band',cancelled:false}).map(item=>item.id)).toEqual(['recent','older']);
 const noMatch=generalPropertyFilters(filters,{latestPrice:{min:'5',max:'8'}});
 expect(discoverPropertyComplexes([complex],rows,true,'sale',noMatch).items).toHaveLength(0);
 expect(rows).toHaveLength(2);
});
it('renders the common condition strip with existing values, not a fixed personal budget',()=>{
 const html=renderToStaticMarkup(createElement(PropertyFilterBar,{regions:[{lawd_code:'11710',name:'서울특별시 송파구'} as PropertyRegion],province:'서울특별시',region:'11710',dong:'가락동',dongs:['가락동'],trade:'sale',rentKind:'all',area:'84-band',filters:{...EMPTY_PROPERTY_DISCOVERY_FILTERS,priceMinEok:'20',priceMaxEok:'35',areaMinM2:'84',areaMaxM2:'84.99999'},month:'202609',months:['202608','202609'],provisionalMonth:'202610',range:36,onProvince:()=>{},onRegion:()=>{},onDong:()=>{},onTrade:()=>{},onFilters:()=>{},onRange:()=>{},onMonth:()=>{}}));
 expect(html).toContain('송파구 가락동');expect(html).toContain('20–35억');expect(html).toContain('국평 · 전용 84㎡대');expect(html).toContain('최근 3년');
 expect(html).toContain('aria-label="거래 유형"');expect(html).toContain('상세조건');expect(html).not.toContain('4억 이하');
 expect(html.match(/aria-haspopup="dialog"/g)).toHaveLength(5);
});
it('removes duplicate list controls while preserving sort, actionable price, area, contract date and watch action',()=>{
 const html=renderToStaticMarkup(createElement(PropertyComplexList,{compactControls:true,complexes:[complex],rows:[row('recent','2026-09-20',4e8)],dataReady:true,trade:'sale',onSelect:()=>{},onWatch:()=>{},savedFilterRegion:'11710'}));
 expect(html).toContain('aria-label="단지 정렬"');expect(html).toContain('4억원');expect(html).toContain('전용 84.99㎡');expect(html).toContain('2026-09-20');expect(html).toContain('검증 아파트 관심 저장');
 expect(html).not.toContain('discovery-search');expect(html).not.toContain('단지 조건 빠른 선택');expect(html).not.toContain('모든 법정동');expect(html).not.toContain('내 검색조건');expect(html).not.toContain('단지·투자 조건');
});
it('keeps unverified prices distinct from an actual empty selected month in the compact list',()=>{
 const render=(dataReady:boolean)=>renderToStaticMarkup(createElement(PropertyComplexList,{compactControls:true,complexes:[complex],rows:[],dataReady,trade:'sale',onSelect:()=>{}}));
 expect(render(false)).toContain('거래 확인 전');expect(render(false)).not.toContain('선택 월 거래 없음');expect(render(true)).toContain('선택 월 거래 없음');
});
it('leaves unspecified discovery fields alone and labels exact zero budget without coercion',()=>{
 const base={...EMPTY_PROPERTY_DISCOVERY_FILTERS,buildYearMin:'2010',query:'원래 조건',sort:'name' as const,hasTrades:true};
 expect(generalPropertyFilters(base,{area:'range:60:85',latestPrice:{min:'0',max:'0'}})).toMatchObject({...base,areaMinM2:'60',areaMaxM2:'85',priceMinEok:'0',priceMaxEok:'0'});
 expect(propertyBudgetLabel('0','0')).toBe('0–0억');expect(propertyBudgetLabel('','')).toBe('예산');
});

it('discards an open price edit after sale changes to jeonse instead of restoring all rentals on apply',()=>{
 const filters={...EMPTY_PROPERTY_DISCOVERY_FILTERS,rentKind:'all' as const,priceMaxEok:'8'};
 const view={region:'11710',trade:'sale' as const,month:'202609',range:36,filters};
 const opened=propertyFilterDraftContext(view);
 expect(currentPropertyFilterPanel('price',opened,opened)).toBe('price');
 const jeonse=propertyFilterDraftContext({...view,trade:'rent',filters:{...filters,rentKind:'jeonse'}});
 expect(currentPropertyFilterPanel('price',opened,jeonse)).toBeNull();
 expect(currentPropertyFilterPanel('more',opened,jeonse)).toBeNull();
});
it('invalidates pending area, budget and building edits when applied conditions are cleared or restored',()=>{
 const view={region:'11710',trade:'sale' as const,month:'202609',range:36,filters:{...EMPTY_PROPERTY_DISCOVERY_FILTERS,priceMinEok:'20',priceMaxEok:'35',areaMinM2:'84',areaMaxM2:'84.99999'}};
 const opened=propertyFilterDraftContext(view);
 const changed=[{...view,filters:EMPTY_PROPERTY_DISCOVERY_FILTERS},{...view,region:'11620'},{...view,month:'202608'},{...view,range:12}];
 for(const next of changed)for(const panel of ['price','area','more'] as const)expect(currentPropertyFilterPanel(panel,opened,propertyFilterDraftContext(next))).toBeNull();
});
it('keeps immediate region and period selectors open for dependent dong and reference-month choices',()=>{
 const view={region:'11710',trade:'sale' as const,month:'202609',range:36,filters:EMPTY_PROPERTY_DISCOVERY_FILTERS};
 const opened=propertyFilterDraftContext(view),changed=propertyFilterDraftContext({...view,region:'11620',range:12});
 expect(currentPropertyFilterPanel('region',opened,changed)).toBe('region');
 expect(currentPropertyFilterPanel('period',opened,changed)).toBe('period');
 expect(currentPropertyFilterPanel(null,opened,changed)).toBeNull();
});
