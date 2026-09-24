export const WATCHLIST_LIMIT=100;
export const WATCHLIST_KEY='korea-replay.property-watchlist.v1';
export interface WatchedComplex {id:string;regionCode:string;name:string;address:string;savedAt:string;}
export function parseWatchlist(raw:string):WatchedComplex[]{
  if(raw.length>100_000)throw new Error('관심 단지 파일은 100KB 이하여야 합니다.');
  const value:unknown=JSON.parse(raw);
  if(!value||typeof value!=='object'||!('schema_version' in value)||value.schema_version!==1||!('items' in value)||!Array.isArray(value.items)||value.items.length>WATCHLIST_LIMIT)throw new Error('지원하지 않는 관심 단지 파일입니다.');
  const seen=new Set<string>();
  return value.items.map((item:unknown)=>{
    if(!item||typeof item!=='object')throw new Error('관심 단지 항목이 올바르지 않습니다.');
    const row=item as Record<string,unknown>;
    if(typeof row.regionCode!=='string'||!/^\d{5}$/.test(row.regionCode)||typeof row.id!=='string'||!new RegExp(`^molit-apt:${row.regionCode}:[A-Za-z0-9_-]{1,64}$`).test(row.id)
      ||typeof row.name!=='string'||!row.name.trim()||row.name.length>150||typeof row.address!=='string'||row.address.length>250
      ||typeof row.savedAt!=='string'||!/^\d{4}-\d{2}-\d{2}T/.test(row.savedAt)||!Number.isFinite(Date.parse(row.savedAt))||seen.has(row.id))throw new Error('관심 단지 식별번호·지역·이름 또는 저장일을 확인해 주세요.');
    seen.add(row.id);return {id:row.id,regionCode:row.regionCode,name:row.name,address:row.address,savedAt:row.savedAt};
  });
}
export function watchlistJson(items:readonly WatchedComplex[]):string{return JSON.stringify({schema_version:1,items},null,2);}
export function mergeWatchlist(current:readonly WatchedComplex[],incoming:readonly WatchedComplex[]):WatchedComplex[]{
  const items=new Map(current.map(row=>[row.id,row]));
  for(const row of incoming)if(!items.has(row.id))items.set(row.id,row);
  if(items.size>WATCHLIST_LIMIT)throw new Error(`관심 단지는 최대 ${WATCHLIST_LIMIT}개까지 저장할 수 있습니다.`);
  return parseWatchlist(watchlistJson([...items.values()]));
}
