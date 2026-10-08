import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import type {PropertyComplex} from '../shared/property';
import {DEFAULT_SURROUNDINGS_FILTER,surroundingsFilterChange,surroundingsSourceUrl,surroundingsView,type SurroundingsRecord,type SurroundingsSourceState} from '../shared/property-surroundings';
import PropertySurroundings from '../src/PropertySurroundings';

const scope={complexId:'molit-apt:11110:test',releaseId:'property-0123456789abcdef'};
const source={id:'test-fixture',label:'검증용 출처',url:'https://example.test/source',asOf:'2026-09-27'};
const complex={id:scope.complexId,name:'검증 단지',legal_dong_name:'검증동',lot_number:'1',position:null} as PropertyComplex;
const records:SurroundingsRecord[]=[
  {id:'a',category:'transport',type:'subway',name:'가역',distanceMeters:900,source},
  {id:'b',category:'transport',type:'bus',name:'나정류장',distanceMeters:200,source},
  {id:'c',category:'transport',type:'rail',name:'다역',distanceMeters:2000,source},
  {id:'d',category:'transport',type:'road',name:'라도로',distanceMeters:4000,source},
];
const ready=(overrides:Partial<Extract<SurroundingsSourceState,{status:'ready'}>>={}):SurroundingsSourceState=>({status:'ready',scope,category:'transport',coverageRadius:3000,complete:true,records,source,...overrides});
const render=(state?:SurroundingsSourceState)=>renderToStaticMarkup(createElement(PropertySurroundings,{complex,region:'검증 지역',releaseId:scope.releaseId,sources:state?{transport:state}:undefined}));

it('provides category, radius, subtype and order controls before connecting data, without invented zeroes or endless loading',()=>{
  const html=render();
  for(const text of ['교통','학교','생활','개발계획','500m','1km','3km','가까운순','이름순','교통 목록','자료 연결 전','반경 1km'])expect(html).toContain(text);
  expect(html).not.toContain('0곳');expect(html).not.toContain('불러오는 중');expect(html).not.toContain('가역');
  expect(html).toContain('단지 외부 지도');expect(html).toContain('네이버');expect(html).not.toContain('<option value="road">');
});

it('filters the actual distance and subtype and orders the selected records',()=>{
  const filter=DEFAULT_SURROUNDINGS_FILTER;
  expect(surroundingsView(ready(),scope,filter).records.map(row=>row.id)).toEqual(['b','a']);
  expect(surroundingsView(ready(),scope,surroundingsFilterChange(filter,{radius:500})).records.map(row=>row.id)).toEqual(['b']);
  expect(surroundingsView(ready(),scope,surroundingsFilterChange(filter,{radius:3000})).records.map(row=>row.id)).toEqual(['b','a','c']);
  expect(surroundingsView(ready(),scope,surroundingsFilterChange(filter,{sort:'name'})).records.map(row=>row.id)).toEqual(['a','b']);
  expect(surroundingsView(ready(),scope,surroundingsFilterChange(filter,{type:'subway'})).records.map(row=>row.id)).toEqual(['a']);
});

it('clears incompatible subtype filters on category changes and rejects impossible filters',()=>{
  const subway=surroundingsFilterChange(DEFAULT_SURROUNDINGS_FILTER,{type:'subway'});
  expect(surroundingsFilterChange(subway,{category:'school'})).toMatchObject({category:'school',type:'all',radius:1000});
  expect(()=>surroundingsFilterChange(subway,{type:'elementary'})).toThrow('invalid_surroundings_type');
  expect(DEFAULT_SURROUNDINGS_FILTER).toEqual({category:'transport',radius:1000,type:'all',sort:'distance'});
});

it('does not borrow another complex or release source and distinguishes loading, failure and confirmed emptiness',()=>{
  for(const changed of [{...scope,complexId:'other'},{...scope,releaseId:'other'}])expect(surroundingsView(ready(),changed,DEFAULT_SURROUNDINGS_FILTER)).toMatchObject({state:'unconnected',count:null,records:[]});
  expect(surroundingsView({status:'loading',scope},scope,DEFAULT_SURROUNDINGS_FILTER)).toMatchObject({state:'loading',count:null});
  expect(surroundingsView({status:'error',scope},scope,DEFAULT_SURROUNDINGS_FILTER)).toMatchObject({state:'error',count:null});
  expect(surroundingsView(ready({records:[]}),scope,DEFAULT_SURROUNDINGS_FILTER)).toMatchObject({state:'empty',count:0});
  expect(render({status:'loading',scope})).toContain('자료 불러오는 중');
  expect(render({status:'error',scope})).toContain('role="alert"');expect(render({status:'error',scope})).not.toContain('0곳');
  expect(render(ready({records:[]}))).toContain('조건에 맞는 자료가 없습니다');
});

it('does not call incomplete coverage or unknown distances a verified zero',()=>{
  expect(surroundingsView(ready({records:[],complete:false}),scope,DEFAULT_SURROUNDINGS_FILTER)).toMatchObject({state:'partial',count:null});
  expect(surroundingsView(ready({records:[],coverageRadius:500}),scope,DEFAULT_SURROUNDINGS_FILTER)).toMatchObject({state:'partial',count:null});
  const unknown={...records[0],distanceMeters:null};
  expect(surroundingsView(ready({records:[unknown]}),scope,DEFAULT_SURROUNDINGS_FILTER)).toMatchObject({state:'partial',count:null,unknownDistances:1,records:[]});
  expect(render(ready({records:[],complete:false}))).not.toContain('0곳');
});

it('renders only sourced real records with straight-line distances and excludes unsafe source links',()=>{
  const html=render(ready());expect(html).toContain('가역');expect(html).toContain('나정류장');expect(html).not.toContain('다역');expect(html).toContain('직선 900m');expect(html).toContain('2026-09-27');
  expect(html).not.toContain('분 소요');expect(surroundingsSourceUrl('javascript:alert(1)')).toBeUndefined();expect(surroundingsSourceUrl('https://user:pass@example.test/')).toBeUndefined();
});
it('does not count duplicate or mismatched-category records as complete coverage',()=>{
  expect(surroundingsView(ready({records:[records[0],records[0]]}),scope,DEFAULT_SURROUNDINGS_FILTER)).toMatchObject({state:'error',count:null});
  const school:SurroundingsRecord={id:'school',category:'school',type:'elementary',name:'검증학교',distanceMeters:400,source};
  expect(surroundingsView(ready({records:[school]}),scope,DEFAULT_SURROUNDINGS_FILTER)).toMatchObject({state:'error',count:null});
  expect(surroundingsView(ready({category:'school',records:[school]}),scope,surroundingsFilterChange(DEFAULT_SURROUNDINGS_FILTER,{category:'school'}))).toMatchObject({state:'ready',count:1,records:[school]});
});
it('bounds dense facility lists to twenty rows without dropping source records',()=>{
  const many=Array.from({length:85},(_,i)=>({...records[0],id:`facility-${i}`,name:`시설 ${i}`}));
  const html=render(ready({records:many,complete:false}));
  expect(html.match(/<li>/g)).toHaveLength(20);
  expect(html).toContain('지도기록 85개');expect(html).toContain('시설 목록 페이지');
  expect(html).toContain('다음 시설');expect(html).not.toContain('시설 84');
});


it('uses a corroborated official navigation marker without claiming a verified entrance',()=>{
  const point={complexId:complex.id,kaptCode:'A10000000',longitude:127.1,latitude:37.5,releaseId:scope.releaseId,coordinateStatus:'provider_xy_crs_unconfirmed' as const,navigationEvidence:{method:'official_site_marker_same_kapt_code_and_coordinates' as const,pointSemantics:'provider_map_navigation_marker' as const,sourceUrl:'https://openapt.seoul.go.kr/',checkedAt:'2026-10-08T16:14:03Z',markerResponseSha256:'a'.repeat(64),navigationPageSha256:'b'.repeat(64)}};
  const output=(navigationPoint:typeof point)=>renderToStaticMarkup(createElement(PropertySurroundings,{complex,region:'검증 지역',releaseId:scope.releaseId,navigationPoint,onUseMapCenter:()=>null}));
  const html=output(point);
  expect(html).toContain('서울시 공식 지도 기준점 · 직선거리');
  expect(html).not.toContain('검증된 단지 위치 기준');
  expect(output({...point,complexId:'other'})).toContain('위치 확인 중');
  expect(output({...point,releaseId:'property-other'})).toContain('위치 확인 중');
});
