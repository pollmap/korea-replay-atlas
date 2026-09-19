import type {LiveBusRoute} from './live-transit';

export interface TransitCatalogRef {url:string;sha256:string;byte_length:number;}
export interface TransitCity extends Partial<TransitCatalogRef> {
  city_code:string;city_name:string;status:'complete'|'failed';location_service_supported:boolean;
  route_count:number;retrieved_at:string|null;error_code?:string;
}
export interface TransitRoute extends LiveBusRoute {
  route_no:string;route_type:string|null;start_station_name:string|null;end_station_name:string|null;
}
export interface TransitCityRoutes {schema_version:1;city_code:string;city_name:string;retrieved_at:string;routes:TransitRoute[];}
export interface TransitRouteCatalog {schema_version:1;cities:TransitCity[];[key:string]:unknown;}
const ROOT=/^\/data\/live-transit\/routes\/[a-f0-9]{16,64}\//;
/** Official route IDs include Hangul (for example Gumi's GMB수점10). */
export const TRANSIT_ROUTE_ID_PATTERN=/^[A-Za-z0-9가-힣_-]{1,60}$/;
const HASH=/^[a-f0-9]{64}$/,CITY=/^\d{1,8}$/;
const MAX_MANIFEST=256*1024,MAX_CITY=2*1024*1024;
function text(value:unknown,max=100):value is string{return typeof value==='string'&&value.length>0&&value.length<=max&&![...value].some(c=>c.charCodeAt(0)<32||c.charCodeAt(0)===127);}
function object(value:unknown):value is Record<string,unknown>{return typeof value==='object'&&value!==null&&!Array.isArray(value);}
export function validTransitRef(value:unknown,kind:'manifest'|'city'='manifest'):value is TransitCatalogRef {
  if(!object(value)||typeof value.url!=='string'||!ROOT.test(value.url)||!HASH.test(String(value.sha256)))return false;
  const suffix=value.url.replace(ROOT,'');
  return (kind==='manifest'?suffix==='manifest.json':/^\d{1,8}\.json$/.test(suffix))
    &&Number.isSafeInteger(value.byte_length)&&Number(value.byte_length)>0&&Number(value.byte_length)<=(kind==='manifest'?MAX_MANIFEST:MAX_CITY);
}
export function parseTransitCatalog(value:unknown,reference:TransitCatalogRef):TransitRouteCatalog {
  if(!validTransitRef(reference)||!object(value)||value.schema_version!==1||!Array.isArray(value.cities)||value.cities.length>512)throw new Error('전국 버스 도시 목록 형식이 올바르지 않습니다.');
  const cities=new Set<string>(),prefix=reference.url.slice(0,-'manifest.json'.length);
  for(const item of value.cities){
    if(!object(item)||typeof item.city_code!=='string'||!CITY.test(item.city_code)||!text(item.city_name)||cities.has(item.city_code)
      ||!['complete','failed'].includes(String(item.status))||typeof item.location_service_supported!=='boolean'
      ||!Number.isSafeInteger(item.route_count)||Number(item.route_count)<0||Number(item.route_count)>12000)throw new Error('버스 도시별 조회 범위를 확인하지 못했습니다.');
    cities.add(item.city_code);
    if(item.status==='complete'){
      if(!validTransitRef(item,'city')||item.url!==`${prefix}${item.city_code}.json`||!Number.isFinite(Date.parse(String(item.retrieved_at))))throw new Error('버스 도시별 자료 참조가 올바르지 않습니다.');
    }else if(item.url!==undefined||item.sha256!==undefined||item.byte_length!==undefined||item.route_count!==0)throw new Error('실패한 도시 자료를 조회 대상으로 사용할 수 없습니다.');
  }
  return value as unknown as TransitRouteCatalog;
}
export function parseTransitCity(value:unknown,city:TransitCity):TransitCityRoutes {
  if(!object(value)||value.schema_version!==1||value.city_code!==city.city_code||value.city_name!==city.city_name
    ||!Number.isFinite(Date.parse(String(value.retrieved_at)))||!Array.isArray(value.routes)||value.routes.length!==city.route_count)throw new Error('버스 도시별 노선 목록이 일치하지 않습니다.');
  const ids=new Set<string>(),routes=new Set<string>();
  for(const row of value.routes){
    if(!object(row)||typeof row.route_id!=='string'||!TRANSIT_ROUTE_ID_PATTERN.test(row.route_id)||row.city_code!==city.city_code
      ||row.id!==`tago-${city.city_code}-${row.route_id.toLowerCase()}`||!text(row.label,200)||!text(row.route_no,80)
      ||ids.has(String(row.id))||routes.has(row.route_id))throw new Error('버스 노선의 공식 식별자가 올바르지 않습니다.');
    ids.add(String(row.id));routes.add(row.route_id);
    for(const key of ['route_type','start_station_name','end_station_name'])if(row[key]!==null&&row[key]!==undefined&&!text(row[key],160))throw new Error('버스 노선 설명이 올바르지 않습니다.');
  }
  return value as unknown as TransitCityRoutes;
}
export function createTransitCatalogReader(fetcher:typeof fetch=fetch) {
  const memory=new Map<string,Promise<unknown>>();
  async function read(reference:TransitCatalogRef,kind:'manifest'|'city'):Promise<unknown>{
    if(!validTransitRef(reference,kind))throw new Error('버스 목록 참조가 올바르지 않습니다.');
    const key=`${reference.url}:${reference.sha256}:${reference.byte_length}`,existing=memory.get(key);
    if(existing){memory.delete(key);memory.set(key,existing);return existing;}
    const pending=(async()=>{
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
      try{
        // Workers rejects redirect:'error'; manual leaves 3xx for the !ok check.
        const response=await fetcher(reference.url,{signal:controller.signal,redirect:'manual'});
        if(!response.ok)throw new Error('버스 목록을 불러오지 못했습니다.');
        if(Number(response.headers.get('Content-Length'))>reference.byte_length){await response.body?.cancel();throw new Error('버스 목록 크기가 일치하지 않습니다.');}
        const reader=response.body?.getReader();if(!reader)throw new Error('버스 목록이 비어 있습니다.');
        const chunks:Uint8Array[]=[];let size=0;
        const cancel=()=>{void reader.cancel().catch(()=>undefined);};controller.signal.addEventListener('abort',cancel,{once:true});
        try{while(true){
          const {done,value}=await reader.read();if(controller.signal.aborted)throw new Error('버스 목록 응답 시간이 초과되었습니다.');if(done)break;
          size+=value.byteLength;if(size>reference.byte_length){await reader.cancel();throw new Error('버스 목록 크기가 일치하지 않습니다.');}chunks.push(value);
        }}finally{controller.signal.removeEventListener('abort',cancel);reader.releaseLock();}
        if(size!==reference.byte_length)throw new Error('버스 목록 크기가 일치하지 않습니다.');
        const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
        const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
        if(hash!==reference.sha256)throw new Error('버스 목록 해시가 일치하지 않습니다.');
        return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)) as unknown;
      }finally{clearTimeout(timer);}
    })();
    memory.set(key,pending);while(memory.size>10)memory.delete(memory.keys().next().value!);
    try{return await pending;}catch(error){if(memory.get(key)===pending)memory.delete(key);throw error;}
  }
  async function manifest(reference:TransitCatalogRef){return parseTransitCatalog(await read(reference,'manifest'),reference);}
  async function city(reference:TransitCatalogRef,cityCode:string){
    const catalog=await manifest(reference),selected=catalog.cities.find(row=>row.city_code===cityCode);
    if(!selected||selected.status!=='complete'||!validTransitRef(selected,'city'))return null;
    return parseTransitCity(await read(selected,'city'),selected);
  }
  async function route(reference:TransitCatalogRef,id:string):Promise<LiveBusRoute|null>{
    const match=/^tago-(\d{1,8})-[a-z0-9가-힣_-]{1,60}$/.exec(id);if(!match)return null;
    const catalog=await manifest(reference),selected=catalog.cities.find(row=>row.city_code===match[1]);
    if(!selected?.location_service_supported)return null;
    const shard=await city(reference,match[1]);return shard?.routes.find(row=>row.id===id)??null;
  }
  return {manifest,city,route};
}
