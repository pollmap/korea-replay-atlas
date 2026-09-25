import {discoverPropertyComplexes,EMPTY_PROPERTY_DISCOVERY_FILTERS,type PropertyDiscoveryFilters} from './property-discovery';
export const SAVED_FILTERS_KEY='korea-replay.property-filters.v1';
export const SAVED_FILTERS_LIMIT=50;
export interface SavedPropertyFilter {id:string;name:string;region:string;trade:'sale'|'rent';filters:PropertyDiscoveryFilters;savedAt:string;}
const sortValues=['recent','count','name','price-low','price-high','pyeong-low','pyeong-high'];
const hasControl=(text:string)=>[...text].some(char=>char.charCodeAt(0)<32);
export function checkedDiscoveryFilters(value:unknown,trade:'sale'|'rent'):PropertyDiscoveryFilters{
  if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('검색조건 형식이 올바르지 않습니다.');
  const row=value as Record<string,unknown>,filters={...EMPTY_PROPERTY_DISCOVERY_FILTERS};
  for(const key of ['query','dong','buildYearMin','buildYearMax','priceMinEok','priceMaxEok','areaMinM2','areaMaxM2'] as const){
    const text=row[key]??'';if(typeof text!=='string'||text.length>(key==='query'||key==='dong'?100:32)||hasControl(text))throw new Error('검색조건의 값이 올바르지 않습니다.');filters[key]=text;
  }
  if(typeof row.sort!=='string'||!sortValues.includes(row.sort)||row.hasTrades!==undefined&&typeof row.hasTrades!=='boolean')throw new Error('정렬·거래 조건을 확인해 주세요.');
  filters.sort=row.sort as PropertyDiscoveryFilters['sort'];filters.hasTrades=!!row.hasTrades;
  if(row.rentKind!==undefined){if(typeof row.rentKind!=='string'||!['all','jeonse','monthly'].includes(row.rentKind))throw new Error('전세·월세 조건을 확인해 주세요.');if(trade==='rent')filters.rentKind=row.rentKind as PropertyDiscoveryFilters['rentKind'];}
  const result=discoverPropertyComplexes([],[],false,trade,filters);if(result.errors.length)throw new Error(result.errors.join(' '));
  return filters;
}
export function savedFiltersJson(items:readonly SavedPropertyFilter[]){return JSON.stringify({schema_version:1,kind:'property-saved-filters',items},null,2);}
export function parseSavedFilters(raw:string):SavedPropertyFilter[]{
  if(raw.length>100_000)throw new Error('검색조건 파일은 100KB 이하여야 합니다.');
  const value=JSON.parse(raw) as Record<string,unknown>;
  if(!value||value.schema_version!==1||value.kind!=='property-saved-filters'||!Array.isArray(value.items)||value.items.length>SAVED_FILTERS_LIMIT)throw new Error('지원하지 않는 검색조건 파일입니다.');
  const ids=new Set<string>();
  return value.items.map((value:unknown)=>{
    if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('검색조건 항목을 확인해 주세요.');const row=value as Record<string,unknown>;
    if(typeof row.id!=='string'||!/^[a-zA-Z0-9-]{1,64}$/.test(row.id)||ids.has(row.id)||typeof row.name!=='string'||!row.name.trim()||row.name.length>40||hasControl(row.name)
      ||typeof row.region!=='string'||!/^\d{5}$/.test(row.region)||!['sale','rent'].includes(String(row.trade))||typeof row.savedAt!=='string'||!/^\d{4}-\d{2}-\d{2}T/.test(row.savedAt)||!Number.isFinite(Date.parse(row.savedAt)))throw new Error('검색조건의 이름·지역·유형·저장일을 확인해 주세요.');
    ids.add(row.id);const trade=row.trade as 'sale'|'rent';
    return {id:row.id,name:row.name.trim(),region:row.region,trade,filters:checkedDiscoveryFilters(row.filters,trade),savedAt:row.savedAt};
  });
}
export function mergeSavedFilters(current:readonly SavedPropertyFilter[],incoming:readonly SavedPropertyFilter[]):SavedPropertyFilter[]{
  const items=new Map(current.map(row=>[row.id,row]));for(const row of incoming)if(!items.has(row.id))items.set(row.id,row);
  return parseSavedFilters(savedFiltersJson([...items.values()]));
}
