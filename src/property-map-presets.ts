import {atlasFetch} from './atlas-client';
import {areaMatches} from '../shared/property-area';
import {historyMonths} from '../shared/property-history';
import type {PropertyViewState} from '../shared/property-view';
import {parseComplexPriceSummaries} from './property-summary-client';
import type {ComplexPriceSummary,SummaryPartition} from './property-map-prices';
const urls=import.meta.glob<string>('./data/map-price-presets-b87eea7c1c03dc21/*.json',{eager:true,query:'?url',import:'default'});
const registered='property-b87eea7c1c03dc21';
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
export function parseMapPricePreset(value:unknown,release:string,view:PropertyViewState):{rows:ComplexPriceSummary[];partitions:SummaryPartition[]}|null{
  if(!object(value)||value.schema_version!==1||value.kind!=='property-map-price-presets'||value.property_release_id!==release||value.lawd_code!==view.region||!Array.isArray(value.point_ids)||value.point_ids.length>4000||!Array.isArray(value.partitions)||value.partitions.length>1200||!object(value.views))throw Error('지도 가격 자료 형식 오류');
  const ids=new Set(value.point_ids);if(ids.size!==value.point_ids.length||value.point_ids.some(id=>typeof id!=='string'||!new RegExp(`^molit-apt:${view.region}:[A-Za-z0-9_-]{1,64}$`).test(id)))throw Error('지도 단지 식별자 오류');
  const partitions=value.partitions.map(p=>{
    if(!object(p)||typeof p.deal_month!=='string'||!/^\d{4}(?:0[1-9]|1[0-2])$/.test(p.deal_month)||!['sale','rent'].includes(String(p.trade_type))||!['complete','empty','pending','partial','failed','source_unavailable'].includes(String(p.status)))throw Error('지도 가격 기간 오류');
    return p as unknown as SummaryPartition;
  });
  const kind=view.trade==='sale'?'sale':view.rentKind&&view.rentKind!=='all'?view.rentKind:'rent';
  const selected=value.views[`${view.month}|${view.historyMonths}|${view.area}|${kind}`];
  if(selected===undefined)return null;
  if(!Array.isArray(selected)||selected.length>4000)throw Error('지도 가격 목록 오류');
  const bounds=partitions.map(p=>p.deal_month).sort();
  const pool=parseComplexPriceSummaries({...value,kind:'property-complex-summary-pack',from_month:bounds[0],to_month:bounds.at(-1)},release,view.region,null);
  const wanted=new Set(historyMonths(view.month,view.historyMonths)),seen=new Set<string>();
  const rows=selected.map(index=>{
    if(!Number.isSafeInteger(index)||index<0||index>=pool.length)throw Error('지도 가격 참조 오류');
    const row=pool[index];
    if(!ids.has(row.complex_id)||seen.has(row.complex_id)||!wanted.has(row.deal_month)||row.trade_type!==view.trade||!areaMatches(row.area_m2,view.area)||view.trade==='rent'&&view.rentKind&&view.rentKind!=='all'&&row.rent_kind!==view.rentKind)throw Error('지도 가격 조건 오류');
    seen.add(row.complex_id);return row;
  });
  return {rows,partitions:partitions.filter(p=>wanted.has(p.deal_month))};
}
export async function loadMapPricePreset(release:string,view:PropertyViewState,signal:AbortSignal){
  if(release!==registered)return null;
  const url=urls[`./data/map-price-presets-b87eea7c1c03dc21/${view.region}.json`];if(!url)return null;
  const response=await atlasFetch(url,{signal});if(!response.ok)throw Error('지도 가격 조회 실패');
  const body=await response.text();if(body.length>1024*1024)throw Error('지도 가격 용량 초과');
  return parseMapPricePreset(JSON.parse(body),release,view);
}