import { EMPTY_CATALOG, PLACES, SOURCES } from '../shared/sources';
import type { Asset, BBox, Catalog, CatalogV2 } from '../shared/contracts';
import {collectTago} from './collectors';
import {createPublishedPlaceSearch} from '../shared/search-v2';
import {createLiveWeatherHandler} from './live-weather';
import {createLiveTransitHandler,type LiveTransitEnv} from './live-transit';
import {createTransitCatalogReader,validTransitRef,type TransitCatalogRef} from '../shared/transit-catalog';
import liveTargets from '../config/live-transit-routes.json';

export interface Env extends LiveTransitEnv {
  ASSETS: Fetcher;
  DATA?: R2Bucket;
  DB?: D1Database;
  ENVIRONMENT?: string;
  DATA_STORAGE?: string;
  COLLECTORS_ENABLED?: string;
  STATIC_RELEASE_ID?: string;
  CF_VERSION_METADATA?: {id:string;tag?:string;timestamp?:string};
  DATA_GO_KR_SERVICE_KEY?: string;
  KMA_AUTH_KEY?: string;
  KORAIL_SERVICE_KEY?: string;
}
const transitReaders=new WeakMap<object,ReturnType<typeof createTransitCatalogReader>>();
const transitDescriptors=new WeakMap<object,{expires:number;promise:Promise<TransitCatalogRef|undefined>}>();
const liveTransit=createLiveTransitHandler({busRoutes:liveTargets.bus_routes,subwayLines:liveTargets.subway_lines,
  busCatalog:async(request,environment)=>{
    const env=environment as Env,storage=env.DATA??env.ASSETS,origin=new URL(request.url).origin;
    let saved=transitDescriptors.get(storage);
    if(!saved||saved.expires<Date.now()){
      const promise=catalog(env,origin,null).then(value=>{
        const reference=value.live_transit_routes;
        if(reference&&!validTransitRef(reference))throw new Error('Invalid transit descriptor');
        return reference;
      });
      saved={expires:Date.now()+30000,promise};transitDescriptors.set(storage,saved);
      void promise.catch(()=>{if(transitDescriptors.get(storage)===saved)transitDescriptors.delete(storage);});
    }
    const reference=await saved.promise;if(!reference)return undefined;
    let reader=transitReaders.get(storage);
    if(!reader){
      reader=createTransitCatalogReader((input,init)=>dataFile(new Request(new URL(String(input),origin),init),env));
      transitReaders.set(storage,reader);
    }
    return {reference,resolve:(id)=>reader!.route(reference,id)};
  }});
const liveWeatherHandlers=new WeakMap<Env,ReturnType<typeof createLiveWeatherHandler>>();
function liveWeather(env:Env){
  let handler=liveWeatherHandlers.get(env);
  if(!handler){handler=createLiveWeatherHandler({dataGoKrServiceKey:env.DATA_GO_KR_SERVICE_KEY});liveWeatherHandlers.set(env,handler);}
  return handler;
}
const searchCache=new WeakMap<object,Map<string,ReturnType<typeof createPublishedPlaceSearch>>>();
const searchDescriptors=new WeakMap<object,Map<string,{expires:number;promise:Promise<Asset|undefined>}>>();

async function searchDescriptor(env:Env,origin:string,release:string|null):Promise<Asset|undefined>{
  if(release!==null&&!/^pub-[a-f0-9]{16}$/.test(release))throw new Error('release 형식이 올바르지 않습니다.');
  const storage=env.DATA??env.ASSETS,key=`${origin}:${release??'latest'}`;
  let cache=searchDescriptors.get(storage);
  if(!cache){cache=new Map();searchDescriptors.set(storage,cache);}
  const current=cache.get(key);
  if(current&&current.expires>Date.now())return current.promise;
  cache.delete(key);
  const promise=catalog(env,origin,release).then(value=>value.assets.find(asset=>asset.format==='search-index'));
  const entry={expires:release?Infinity:Date.now()+30000,promise};cache.set(key,entry);
  // Retain descriptors, not multi-megabyte catalogs, and bound old share links.
  while(cache.size>8)cache.delete(cache.keys().next().value!);
  try{return await promise;}catch(error){if(cache.get(key)===entry)cache.delete(key);throw error;}
}

function searchRunner(env:Env,origin:string){
  const storage=env.DATA??env.ASSETS;
  let origins=searchCache.get(storage);if(!origins){origins=new Map();searchCache.set(storage,origins);}
  let runner=origins.get(origin);
  if(!runner){
    const fetcher:typeof fetch=(input,init)=>dataFile(new Request(input instanceof Request?input:new URL(input.toString(),origin),init),env);
    runner=createPublishedPlaceSearch(fetcher,{maxRequests:40});origins.set(origin,runner);
    while(origins.size>4)origins.delete(origins.keys().next().value!);
  }
  return runner;
}
export function parseBBox(value: string | null): BBox | null {
  if (!value) return null;
  const p=value.split(',').map(Number);
  if(p.length!==4 || p.some(v=>!Number.isFinite(v)) || p[0]>=p[2] || p[1]>=p[3] || p[0]<-180 || p[2]>180 || p[1]<-90 || p[3]>90) throw new Error('bbox는 서,남,동,북 순서의 유효한 좌표여야 합니다.');
  return p as BBox;
}
export function intersects(a:BBox,b:BBox) {return a[0]<=b[2] && a[2]>=b[0] && a[1]<=b[3] && a[3]>=b[1];}
function json(value:unknown,status=200) {
  return Response.json(value,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
}
// R2 get() returns metadata without a body for any failed conditional, not
// exclusively cache hits. Evaluate HTTP precedence ourselves (RFC 9110 §13.2).
function httpDate(value:string|null):number|null {
  if(!value || !/^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)/.test(value))return null;
  const parsed=Date.parse(value);return Number.isFinite(parsed)?Math.floor(parsed/1000):null;
}
function etagMatches(value:string,etag:string,weak:boolean):boolean {
  if(value.trim()==='*')return true;
  // Entity-tag opaque values may contain commas, so split by their quoted form.
  return (value.match(/(?:W\/)?"[^"\r\n]*"/g)??[]).some(tag=>weak?tag.replace(/^W\//,'')===etag:tag===etag);
}
function precondition(headers:Headers,object:R2Object):304|412|null {
  const match=headers.get('If-Match'),none=headers.get('If-None-Match');
  const modified=Math.floor(object.uploaded.getTime()/1000);
  if(match!==null){if(!etagMatches(match,object.httpEtag,false))return 412;}
  else {const since=httpDate(headers.get('If-Unmodified-Since'));if(since!==null&&modified>since)return 412;}
  if(none!==null){if(etagMatches(none,object.httpEtag,true))return 304;}
  else {const since=httpDate(headers.get('If-Modified-Since'));if(since!==null&&modified<=since)return 304;}
  return null;
}
function conditionalHeaders(headers:Headers):Headers {
  const result=new Headers();
  for(const name of ['If-Match','If-None-Match'])if(headers.has(name))result.set(name,headers.get(name)!);
  for(const [name,priority] of [['If-Unmodified-Since','If-Match'],['If-Modified-Since','If-None-Match']]){
    if(!headers.has(priority)&&httpDate(headers.get(name))!==null)result.set(name,headers.get(name)!);
  }
  return result;
}
function objectHeaders(object:R2Object,path:string):Headers {
  const headers=new Headers();object.writeHttpMetadata(headers);
  headers.set('ETag',object.httpEtag);headers.set('Last-Modified',object.uploaded.toUTCString());
  headers.set('Accept-Ranges','bytes');headers.set('X-Content-Type-Options','nosniff');
  headers.set('Cache-Control',path==='catalog.json'?'public, max-age=30':'public, max-age=31536000, immutable');
  return headers;
}
type ByteRange={offset:number;length:number};
function byteRange(value:string,size:number):ByteRange|'unsatisfiable'|null {
  // Unsupported units, malformed syntax and multipart requests are ignored;
  // this endpoint serves one byte range or the complete representation.
  const match=/^bytes=(\d*)-(\d*)$/i.exec(value.trim());
  if(!match||(!match[1]&&!match[2]))return null;
  if(!size)return 'unsatisfiable';
  const first=match[1]?BigInt(match[1]):null,last=match[2]?BigInt(match[2]):null,total=BigInt(size);
  if(first===null){
    if(last===0n)return 'unsatisfiable';
    const length=Number(last!>total?total:last!);return {offset:size-length,length};
  }
  if(last!==null&&last<first)return null;
  if(first>=total)return 'unsatisfiable';
  const end=last===null||last>=total?total-1n:last;
  return {offset:Number(first),length:Number(end-first+1n)};
}
async function r2Data(request:Request,bucket:R2Bucket,path:string):Promise<Response> {
  const key=`public/${path}`,isHead=request.method==='HEAD';
  const unavailable=()=>new Response(null,{status:503,headers:{'Cache-Control':'no-store','Retry-After':'1'}});
  const missing=()=>isHead?new Response(null,{status:404,headers:{'Cache-Control':'no-store'}}):json({error:'자료를 찾을 수 없습니다.'},404);
  try {
    // Ordinary GETs retain one R2 operation. HEAD never opens an object body.
    const requestedRange=isHead?null:request.headers.get('Range');
    let object=await (isHead||requestedRange?bucket.head(key):bucket.get(key,{onlyIf:conditionalHeaders(request.headers)}));
    for(let attempt=0;attempt<3;attempt++){
      if(!object)return missing();
      const headers=objectHeaders(object,path),condition=precondition(request.headers,object);
      if(condition){
        if('body' in object)await (object as R2ObjectBody).body.cancel();
        if(condition===412)headers.set('Cache-Control','no-store');
        return new Response(null,{status:condition,headers});
      }
      if(isHead){headers.set('Content-Length',String(object.size));return new Response(null,{headers});}
      let range:ByteRange|'unsatisfiable'|null=null;
      const ifRange=request.headers.get('If-Range');
      // R2 does not implement If-Range. Only a strong matching ETag enables
      // it here; upload dates cannot prove absence of same-second rewrites.
      if(requestedRange&&(!ifRange||ifRange===object.httpEtag))range=byteRange(requestedRange,object.size);
      if(range==='unsatisfiable'){
        headers.set('Content-Range',`bytes */${object.size}`);headers.set('Cache-Control','no-store');
        return new Response(null,{status:416,headers});
      }
      if(!('body' in object)){
        if(!requestedRange||attempt===2)return unavailable();
        // Pin HEAD metadata to GET bytes. If a mutable object changed, R2
        // supplies new metadata without a body and the loop rechecks conditions.
        object=await bucket.get(key,{onlyIf:{etagMatches:object.etag},...(range?{range}: {})});
        continue;
      }
      const body=object as R2ObjectBody;
      if(range){
        const actual=object.range;
        if(!actual||!('offset' in actual)||!('length' in actual)||actual.offset!==range.offset||actual.length!==range.length){
          await body.body.cancel();return unavailable();
        }
        headers.set('Content-Range',`bytes ${range.offset}-${range.offset+range.length-1}/${object.size}`);
        headers.set('Content-Length',String(range.length));
        return new Response(body.body,{status:206,headers});
      }
      headers.set('Content-Length',String(object.size));
      return new Response(body.body,{headers});
    }
    return unavailable();
  } catch {return unavailable();}
}
async function dataFile(request:Request, env:Env):Promise<Response> {
  let path:string;
  try{path=decodeURIComponent(new URL(request.url).pathname).replace(/^\/data\//,'');}catch{return json({error:'허용되지 않은 파일 경로입니다.'},400);}
  if(!/^[a-zA-Z0-9_./-]+$/.test(path) || path.split('/').some(p=>p==='..'||p==='.') || path.startsWith('raw/') || path.startsWith('private/')) return json({error:'허용되지 않은 파일 경로입니다.'},400);
  if(env.DATA)return r2Data(request,env.DATA,path);
  const result=await env.ASSETS.fetch(request);
  if(result.headers.get('content-type')?.includes('text/html')) return json({error:'자료가 아직 적재되지 않았습니다.'},404);
  return result;
}
async function catalog(env:Env,origin:string,release:string|null):Promise<Catalog> {
  if(release!==null&&!/^pub-[a-f0-9]{16}$/.test(release))throw new Error('release 형식이 올바르지 않습니다.');
  const response=await dataFile(new Request(`${origin}/data/${release?`releases/${release}.json`:'catalog.json'}`),env);
  if(response.status===404&&!release) return structuredClone(EMPTY_CATALOG);
  if(response.status===404)throw new Error('공유한 공개 버전을 찾을 수 없습니다.');
  if(!response.ok) throw new Error('데이터 목록을 불러오지 못했습니다.');
  const c=await response.json() as Catalog|CatalogV2;
  if(![1,2].includes(c.schema_version) || !Array.isArray(c.assets) || !Array.isArray(c.layers) || !c.release_id || (release&&c.release_id!==release)) throw new Error('데이터 목록 형식이 올바르지 않습니다.');
  if(c.schema_version===2&&!Array.isArray(c.indexes))throw new Error('지역별 데이터 목록 형식이 올바르지 않습니다.');
  const layers=[...c.layers];
  for(const definition of EMPTY_CATALOG.layers){
    if(layers.some(l=>l.id===definition.id))continue;
    const matches=c.assets.filter(a=>a.layer===definition.id&&a.format!=='search-index');
    layers.push({...definition,state:matches.length?'partial':definition.state,reason:matches.length?null:definition.reason,record_count:matches.reduce((n,a)=>n+a.count,0),updated_at:matches.length?c.generated_at:null});
  }
  layers.sort((a,b)=>EMPTY_CATALOG.layers.findIndex(l=>l.id===a.id)-EMPTY_CATALOG.layers.findIndex(l=>l.id===b.id));
  return {...c,schema_version:1,assets:c.schema_version===2?[...c.assets,...c.indexes]:c.assets,layers,sources:SOURCES};
}

/** Stream the prebuilt v1 manifest rather than parsing the whole country on an
 * edge request. The browser uses CatalogV2 and loads only regional indexes. */
async function legacyCatalog(env:Env,origin:string,release:string|null):Promise<Response>{
  if(release!==null&&!/^pub-[a-f0-9]{16}$/.test(release))throw new Error('release 형식이 올바르지 않습니다.');
  if(env.DATA_STORAGE==='static'&&env.STATIC_RELEASE_ID){
    const id=release??env.STATIC_RELEASE_ID;
    const response=await dataFile(new Request(`${origin}/data/releases/${id}.v1.json`),env);
    if(response.status===404)throw new Error('공유한 공개 버전을 찾을 수 없습니다.');
    if(!response.ok)throw new Error('데이터 목록을 불러오지 못했습니다.');
    const headers=new Headers(response.headers);headers.set('Cache-Control',release?'public, max-age=31536000, immutable':'no-cache');
    return new Response(response.body,{status:response.status,headers});
  }
  const current=await catalog(env,origin,release);
  const legacy=(current as Catalog&{legacy_url?:unknown}).legacy_url;
  // The development server also reads the published CatalogV2 pointer. Keep
  // its public v1 route identical to the static deployment's flat manifest.
  if(legacy!==undefined){
    if(!/^pub-[a-f0-9]{16}$/.test(current.release_id)||legacy!==`/data/releases/${current.release_id}.v1.json`)throw new Error('v1 호환 목록 경로 오류');
    const response=await dataFile(new Request(new URL(legacy,origin)),env);
    if(response.status===404)throw new Error('공유한 공개 버전을 찾을 수 없습니다.');
    if(!response.ok)throw new Error('데이터 목록을 불러오지 못했습니다.');
    const headers=new Headers(response.headers);headers.set('Cache-Control',release?'public, max-age=31536000, immutable':'no-cache');
    return new Response(response.body,{status:response.status,headers});
  }
  return json(current);
}
export function runtimeInfo(env:Env,url:URL){
  const id=env.CF_VERSION_METADATA?.id??null;
  const host=url.hostname.match(/^(?:[a-f0-9]{8}-)?korea-replay\.([a-z0-9-]+)\.workers\.dev$/);
  const valid=id&&/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(id);
  return {version_id:valid?id:null,release_id:env.STATIC_RELEASE_ID??null,
    preview_origin:valid&&host?`https://${id.slice(0,8)}-korea-replay.${host[1]}.workers.dev`:null};
}
type CoverageAsset=Pick<Asset,'layer'|'format'|'bbox'|'from'|'to'>;
async function coverageAssets(c:Catalog,env:Env,origin:string):Promise<CoverageAsset[]>{
  if(!c.coverage_url)return c.assets;
  if(!c.coverage_url.startsWith('/data/indexes/')||!/^\/data\/[a-zA-Z0-9_./-]+$/.test(c.coverage_url)||c.coverage_url.includes('..'))throw new Error('공간 범위 색인 경로 오류');
  const response=await dataFile(new Request(new URL(c.coverage_url,origin)),env);
  if(!response.ok)throw new Error('공간 범위 색인을 불러오지 못했습니다.');
  const data=await response.json() as {schema_version:number;assets:CoverageAsset[]};
  if(data.schema_version!==1||!Array.isArray(data.assets)||data.assets.length>20000)throw new Error('공간 범위 색인 형식 오류');
  return data.assets;
}
function selectAssets<T extends CoverageAsset>(assets:T[],url:URL) {
  const bbox=parseBBox(url.searchParams.get('bbox'));
  const from=url.searchParams.get('from'),to=url.searchParams.get('to');
  if(Boolean(from)!==Boolean(to)) throw new Error('from과 to를 함께 지정해 주세요.');
  const zoned=/T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
  if(from && to && (!zoned.test(from)||!zoned.test(to)||!Number.isFinite(Date.parse(from)) || !Number.isFinite(Date.parse(to)) || Date.parse(from)>Date.parse(to) || Date.parse(to)-Date.parse(from)>86400000)) throw new Error('조회 기간은 시간대가 포함된 유효한 24시간 이내여야 합니다.');
  return assets.filter(a=>(!bbox||intersects(a.bbox,bbox)) && (!from||!to||!a.from||!a.to||(Date.parse(a.from)<=Date.parse(to) && Date.parse(a.to)>=Date.parse(from))));
}
export async function handleRequest(request:Request,env:Env):Promise<Response> {
  const url=new URL(request.url);
  if(url.pathname.startsWith('/api/v1/live/')){
    try{
      const response=url.pathname.startsWith('/api/v1/live/weather/')?await liveWeather(env)(request):await liveTransit(request,env);
      return response??json({error:'현재 관측 API를 찾을 수 없습니다.'},404);
    }catch{
      // Authenticated upstream URLs may contain keys. Never forward exceptions.
      return json({error:'현재 관측 조회에 실패했습니다. 잠시 뒤 다시 조회해 주세요.'},503);
    }
  }
  if(request.method!=='GET' && request.method!=='HEAD')return json({error:'허용되지 않은 메서드입니다.'},405);
  if(url.pathname.startsWith('/data/')) return dataFile(request,env);
  if(!url.pathname.startsWith('/api/')) return env.ASSETS.fetch(request);
  try {
    if(url.pathname==='/api/v1/health')return json({status:'ok',environment:env.ENVIRONMENT??'unknown',storage:env.DATA?'r2':env.DATA_STORAGE==='static'?'static-assets':'local-assets',database:Boolean(env.DB),collectors:env.COLLECTORS_ENABLED==='true',...runtimeInfo(env,url)});
    if(url.pathname==='/api/v1/runtime'){
      const info=runtimeInfo(env,url);
      if(!info.release_id)info.release_id=(await catalog(env,url.origin,null)).release_id;
      return json(info);
    }
    if(url.pathname==='/api/v1/search') {
      const q=(url.searchParams.get('q')??'').trim().toLocaleLowerCase();
      if(q.length>80)return json({error:'검색어는 80자 이하로 입력해 주세요.'},400);
      const release=url.searchParams.get('release');
      // Empty search still belongs to the requested immutable release. Validate
      // that release before returning presets; never silently accept a missing one.
      if(!q){if(release!==null)await searchDescriptor(env,url.origin,release);return json({places:PLACES});}
      const index=await searchDescriptor(env,url.origin,release);
      const loaded=await searchRunner(env,url.origin)(index,q);
      return json({places:loaded.places,coverage:index?'published-index':'city-presets',...loaded.metadata});
    }
    if(url.pathname.startsWith('/api/v1/sources/')) {
      const source=SOURCES.find(s=>s.id===url.pathname.split('/').at(-1));
      return source?json(source):json({error:'출처를 찾을 수 없습니다.'},404);
    }
    if(!['/api/v1/catalog','/api/v1/coverage','/api/v1/replay'].includes(url.pathname))return json({error:'API를 찾을 수 없습니다.'},404);
    if(url.pathname.endsWith('/catalog'))return await legacyCatalog(env,url.origin,url.searchParams.get('release'));
    const c=await catalog(env,url.origin,url.searchParams.get('release'));
    if(url.pathname.endsWith('/replay')) return json({release_id:c.release_id,assets:selectAssets(c.assets,url).filter(a=>a.format==='replay'||a.format==='imagery')});
    const assets=selectAssets(await coverageAssets(c,env,url.origin),url);
    return json({release_id:c.release_id,basis:'published-source-assets',layers:c.layers.map(layer=>({
      ...layer,matching_assets:assets.filter(a=>a.layer===layer.id).length,
      in_view:layer.id==='sun'||assets.some(a=>a.layer===layer.id),
    }))});
  } catch(error) {
    const message=error instanceof Error?error.message:'요청 처리에 실패했습니다.';
    const invalid=message.startsWith('bbox')||message.startsWith('from')||message.startsWith('조회 기간')||message.startsWith('release');
    return json({error:message},invalid?400:message.startsWith('공유한')?404:503);
  }
}
export default {fetch:handleRequest,scheduled:(_controller:ScheduledController,env:Env,ctx:ExecutionContext)=>{if(env.COLLECTORS_ENABLED==='true')ctx.waitUntil(collectTago(env,_controller.scheduledTime));}};
