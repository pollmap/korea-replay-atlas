import {afterEach,expect,it,vi} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ComplexComparison from '../src/ComplexComparison';
import PropertyExplorer from '../src/PropertyExplorer';
import PropertyHistory from '../src/PropertyHistory';
import type {AtlasContent} from '../src/useAtlas';
import type {PropertyComplex,RegionMetric} from '../shared/property';
import type {PropertyRegionDetail} from '../shared/property';

const release='property-0123456789abcdef',period={from:'202109',to:'202609',latest_complete_month:'202608'};
const coverage={expected:2,complete:0,empty:0,failed:0,pending:2,partial:0,historical_coverage:'current_codes_only_pending_effective_date_crosswalk' as const};
const metric:RegionMetric={lawd_code:'11110',deal_month:'202608',trade_type:'sale',status:'pending',source_rows:null,eligible_rows:null,cancelled_rows:null,
  invalid_rows:null,statistics_excluded_rows:null,complex_count:null,retrieved_at:null,median_price_per_m2_krw:null,statistic:'reported-row-median-price-per-m2',cancellation_policy:'exclude_cancelled_and_unknown'};
const asset={url:`/data/property/${release}/test.json`,sha256:'a'.repeat(64),bytes:10};
function atlas():AtlasContent{return {
  origin:'https://korea-replay-data.pages.dev',
  map:{schema_version:1,release_id:`map2d-${'a'.repeat(20)}`,bounds:[124,32,132,39],topics:[],sources:[],source_release_id:'test',reference_dates:{},attribution:'test'},
  property:{schema_version:1,kind:'property-release',release_id:release,generated_at:'2026-09-20T00:00:00Z',period,coverage,sources:[],regions:asset,
    code_registry:{source_url:'https://www.code.go.kr/',retrieved_at:'2026-09-20T00:00:00Z',sha256:'a'.repeat(64),current_region_count:1},coordinates:{verified_complexes:0,unresolved_complexes:0,name_only_join:false},caveats:[]},
  regions:{schema_version:1,kind:'property-regions',release_id:release,regions:[{lawd_code:'11110',legal_code:'1111000000',name:'검증 지역',index:asset,coverage,
    latest:{sale:metric,rent:{...metric,trade_type:'rent',cancellation_policy:'source_not_provided'}}}]},
};}
afterEach(()=>vi.unstubAllGlobals());

it('does not call unrequested price-card observations zero transactions',()=>{
  const detail={release_id:release,period,partitions:[]} as unknown as PropertyRegionDetail;
  const complex={id:'molit-apt:11110:test'} as PropertyComplex;
  const html=renderToStaticMarkup(createElement(PropertyHistory,{detail,complex,origin:'https://example.com',month:'202608',trade:'sale',area:'',onArea:()=>{},range:3,onRange:()=>{},includeReview:false}));
  expect(html).toContain('자료 확인 전');
  expect(html).not.toContain('확인 0건');
  expect(html).not.toContain('유효 0건');
});

it('does not present an entirely uncollected nationwide view as zero transactions',()=>{
  vi.stubGlobal('location',{hash:''});
  const html=renderToStaticMarkup(createElement(PropertyExplorer,{atlas:atlas(),hidden:false,onClose:()=>{},onLocate:()=>{},onViewState:()=>{}}));
  expect(html).toContain('<strong>—<small>건</small></strong>');
  expect(html).not.toContain('<strong>0<small>건</small></strong>');
  expect(html).toContain('현행 1개 지역 코드 조회 기준');expect(html).toContain('개편 전 코드 대응은 미반영');
});

it('keeps comparison removal available before the request succeeds and preserves a pinned area',()=>{
  const item:PropertyComplex={id:'molit-apt:11110:test',source_complex_id:'test',lawd_code:'11110',name:'검증용 단지',legal_dong_code:null,legal_dong_name:null,
    lot_number:null,build_year:null,position:null,identity_status:'source_apt_seq',source_ids:['molit-apt-sale-detail'],source_input_sha256:['a'.repeat(64)],
    first_contract_month:'202608',last_contract_month:'202608',observed_name_variants:[],address_conflict:false};
  const html=renderToStaticMarkup(createElement(ComplexComparison,{atlas:atlas(),items:[item],month:'202607',trade:'sale',area:'108.55',onArea:()=>{},onRemove:()=>{}}));
  expect(html).toContain('비교 자료를 불러오는 중');expect(html).toContain('aria-label="검증용 단지 단지 비교 제외"');
  expect(html).toMatch(/<option value="108\.55" selected="">108\.55 ㎡ · 자료 확인 전<\/option>/);
  expect(html).not.toContain('<td>0건</td>');
});

it('shows ten-year detail and comparison periods without claiming ten years are collected',()=>{
  const detail={release_id:release,period,partitions:[]} as unknown as PropertyRegionDetail;
  const complex={id:'molit-apt:11110:test',lawd_code:'11110',name:'검증 단지'} as PropertyComplex;
  const history=renderToStaticMarkup(createElement(PropertyHistory,{detail,complex,origin:'https://example.com',month:'202608',trade:'sale',area:'84-band',onArea:()=>{},range:120,onRange:()=>{},includeReview:false,onMonth:()=>{}}));
  expect(history).toContain('최근 10년');expect(history).toContain('게시 0/120개월');
  expect(history).toContain('이 거래 유형의 게시 자료가 없습니다.');expect(history).toContain('거래 기간 이동');
  const comparison=renderToStaticMarkup(createElement(ComplexComparison,{atlas:atlas(),items:[complex],month:'202608',range:120,onRange:()=>{},trade:'sale',area:'84-band',onArea:()=>{},onRemove:()=>{}}));
  expect(comparison).toContain('2016.09–2026.08');expect(comparison).toContain('비교 조회 기간');
  expect(comparison).not.toContain('0건');
});
