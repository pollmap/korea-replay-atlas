import {
  LIVE_TRANSIT_API_PREFIX, LIVE_SUBWAY_MAX_AGE_SECONDS, LIVE_TRANSIT_FUTURE_TOLERANCE_SECONDS, liveTransitVehicles,
  type LiveBusRoute, type LiveSubwayLine, type LiveTransitKind, type LiveTransitSnapshot,
  type LiveBusVehicle, type LiveSubwayVehicle, type LiveTransitErrorCode, type LiveTransitTargets,
} from '../shared/live-transit';
import {createTransitBrokerClient,TransitBrokerFailure,type TransitBrokerBinding} from './live-broker-client';
import {TRANSIT_ROUTE_ID_PATTERN} from '../shared/transit-catalog';

const TAGO_ENDPOINT='https://apis.data.go.kr/1613000/BusLcInfoInqireService/getRouteAcctoBusLcList';
const MAX_BYTES=256*1024, PAGE_ROWS=100, BUS_MAX_PAGES=2, SUBWAY_MAX_ROWS=400, MAX_INFLIGHT=2;
const KST_OFFSET=9*3600_000, CACHE_VERSION='transit-2';
const sources:Record<LiveTransitKind,LiveTransitSnapshot['source']>={
  bus:{id:'tago',page_url:'https://www.data.go.kr/data/15098533/openapi.do',license:'이용허락범위 제한 없음',access:'official-key'},
  subway:{id:'seoul-subway',page_url:'https://data.seoul.go.kr/dataList/OA-12601/A/1/datasetView.do',license:'공공누리 1유형 · 서울특별시 출처표시',access:'official-key'},
};
export interface LiveTransitEnv {
  LIVE_TRANSIT_MODE?:string;
  LIVE_TRANSIT_BROKER?:TransitBrokerBinding;
  DATA_GO_KR_SERVICE_KEY?:string;
  SEOUL_SUBWAY_API_KEY?:string;
  LIVE_TRANSIT_BUS_ROUTES?:string;
  LIVE_TRANSIT_SUBWAY_LINES?:string;
  /** Explicit deployment choice for the provider's documented, non-TLS endpoint. */
  SEOUL_SUBWAY_ALLOW_HTTP?:string;
}
export interface LiveTransitCache {match(request:Request):Promise<Response|undefined>;put(request:Request,response:Response):Promise<void>;}
export interface LiveTransitOptions {
  fetcher?:typeof fetch;
  cache?:LiveTransitCache;
  now?:()=>number;
  timeoutMs?:number;
  busRoutes?:readonly LiveBusRoute[];
  subwayLines?:readonly LiveSubwayLine[];
  busCatalog?:(request:Request,env:LiveTransitEnv)=>Promise<{
    reference:import('../shared/transit-catalog').TransitCatalogRef;
    resolve:(id:string)=>Promise<LiveBusRoute|null>;
  }|undefined>;
  /** Deployment-authorized per-source quota. Tests may lower this value. */
  dailyLimits?:{bus?:number;subway?:number};
}
class TransitFailure extends Error {
  constructor(readonly code:LiveTransitErrorCode,message:string,readonly status=502,readonly retryable=true){super(message);}
}
type JsonObject=Record<string,unknown>;
type Target=LiveBusRoute|LiveSubwayLine;
type Policy={daily:number;budget:number;ttl:number};
type Stored={until:number;identity:string;snapshot:LiveTransitSnapshot;httpStatus:number};
function object(value:unknown):JsonObject|null{return value!==null&&typeof value==='object'&&!Array.isArray(value)?value as JsonObject:null;}
function integer(value:unknown):number|null {
  if(typeof value!=='number'&&(typeof value!=='string'||!/^\d+$/.test(value)))return null;
  const n=Number(value);return Number.isSafeInteger(n)&&n>=0?n:null;
}
function coordinate(value:unknown):number|null {
  if(typeof value!=='number'&&(typeof value!=='string'||!value.trim()))return null;
  const n=Number(value);return Number.isFinite(n)?n:null;
}
function safeText(value:unknown,maximum=80,secrets:string[]=[]):string|null {
  if(typeof value==='number'&&Number.isSafeInteger(value))value=String(value);
  if(typeof value!=='string'||!value.trim()||value.length>maximum||[...value].some(c=>c.charCodeAt(0)<32||c.charCodeAt(0)===127)
    ||secrets.some(secret=>secret&&value.includes(secret)))return null;
  return value.trim();
}
function credential(value:string|undefined,kind:LiveTransitKind):string|null {
  const key=value?.trim();
  if(!key||key.toLowerCase()==='sample'||key.length<8||key.length>512||/\s/.test(key))return null;
  if(kind==='subway'&&!/^[a-zA-Z0-9_-]+$/.test(key))return null;
  return key;
}
function configuredTargets<T extends Target>(value:unknown,kind:LiveTransitKind):T[] {
  let rows:unknown=value??[];
  if(typeof rows==='string'){
    if(rows.length>8192)throw new TransitFailure('invalid_configuration','교통 허용 목록 설정을 확인해야 합니다.',503,false);
    try{rows=JSON.parse(rows);}catch{throw new TransitFailure('invalid_configuration','교통 허용 목록 설정을 확인해야 합니다.',503,false);}
  }
  if(!Array.isArray(rows)||rows.length>(kind==='bus'?5:16))throw new TransitFailure('invalid_configuration','교통 허용 목록 개수가 허용 범위를 벗어났습니다.',503,false);
  const ids=new Set<string>(),upstreamIds=new Set<string>();
  return rows.map(row=>{
    const item=object(row),id=safeText(item?.id,40);
    if(!item||!id||!/^[a-z0-9][a-z0-9-]{0,39}$/.test(id)||ids.has(id))throw new TransitFailure('invalid_configuration','교통 허용 목록 식별자가 올바르지 않습니다.',503,false);
    ids.add(id);
    if(kind==='bus'){
      const city=safeText(item.city_code,8),route=safeText(item.route_id,60),label=safeText(item.label);
      if(!city||!/^\d{1,8}$/.test(city)||!route||!TRANSIT_ROUTE_ID_PATTERN.test(route)||!label||upstreamIds.has(`${city}:${route}`))throw new TransitFailure('invalid_configuration','버스 허용 목록의 원천 식별자를 확인해야 합니다.',503,false);
      upstreamIds.add(`${city}:${route}`);return {id,city_code:city,route_id:route,label} as T;
    }
    const name=safeText(item.name,40),subway=safeText(item.subway_id,4);
    if(!name||!/^[A-Za-z0-9가-힣 -]+$/.test(name)||!subway||!/^\d{4}$/.test(subway)||upstreamIds.has(subway))throw new TransitFailure('invalid_configuration','지하철 허용 목록의 노선을 확인해야 합니다.',503,false);
    upstreamIds.add(subway);return {id,name,subway_id:subway} as T;
  });
}
function policy(kind:LiveTransitKind,count:number,options:LiveTransitOptions):Policy {
  const daily=options.dailyLimits?.[kind]??(kind==='bus'?10000:1000);
  if(!Number.isSafeInteger(daily)||daily<1||daily>1_000_000)throw new TransitFailure('invalid_configuration','교통 호출 예산 설정을 확인해야 합니다.',503,false);
  const budget=Math.max(1,Math.floor(daily*.8)),requests=kind==='bus'?BUS_MAX_PAGES:1;
  // Reserve the worst-case pages for every configured target over a full day.
  const ttl=Math.max(60,Math.ceil(86400*Math.max(1,count)*requests/budget/30)*30);
  return {daily,budget,ttl};
}
function nextKstDay(instant:number):number{return Math.floor((instant+KST_OFFSET)/86400_000)*86400_000+86400_000-KST_OFFSET;}
export function parseTransitKstTime(value:unknown):number|null {
  if(typeof value!=='string'||!/^20\d{2}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,3})?$/.test(value))return null;
  const milliseconds=Date.parse(value.replace(' ','T')+'+09:00');
  if(!Number.isFinite(milliseconds)||new Date(milliseconds+KST_OFFSET).toISOString().slice(0,19)!==value.slice(0,19).replace(' ','T'))return null;
  return milliseconds;
}
function upstreamCode(code:unknown):never {
  const value=String(code??'');
  if(['22','23','429','ERROR-336'].includes(value))throw new TransitFailure('quota_exceeded','원천 교통 API의 호출 한도에 도달했습니다.',429,true);
  if(['20','30','31','401','403','INFO-100','ERROR-100','ERROR-300','ERROR-301'].includes(value))throw new TransitFailure('upstream_auth','원천 교통 API의 인증 또는 서비스 승인을 확인해야 합니다.',503,false);
  throw new TransitFailure('upstream_invalid','원천 교통 API가 정상 자료를 반환하지 않았습니다.');
}
function emptySnapshot(kind:LiveTransitKind,target:Target|null,p:Policy,instant:number):LiveTransitSnapshot {
  return {schema_version:1,mode:'live',kind,status:'unavailable',target:{id:target?.id??'',label:target?('label' in target?target.label:target.name):''},
    retrieved_at:null,served_at:new Date(instant).toISOString(),expires_at:null,refresh_after_seconds:p.ttl,
    max_source_age_seconds:kind==='subway'?LIVE_SUBWAY_MAX_AGE_SECONDS:null,source:sources[kind],
    coverage:{scope:kind==='bus'?'selected-route':'selected-line',complete:false},
    counts:{upstream:0,accepted:0,invalid:0,stale:0,duplicate:0,ambiguous:0},vehicles:[],
    quota:{daily_limit:p.daily,local_budget:p.budget,guard:'isolate-and-regional-cache',global_enforced:false}};
}
function response(value:unknown,status=200,retryAfter?:number):Response {
  return Response.json(value,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...(retryAfter?{'Retry-After':String(retryAfter)}:{})}});
}
async function digest(value:string):Promise<string>{return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(v=>v.toString(16).padStart(2,'0')).join('');}

function createTransitSource({fetcher,now,reserve}:{fetcher:typeof fetch;now:()=>number;reserve:(kind:LiveTransitKind,p:Policy)=>void}) {
  async function readJson(url:URL,kind:LiveTransitKind,p:Policy,signal:AbortSignal):Promise<unknown> {
    reserve(kind,p);
    let raw:Response;
    try{raw=await fetcher(url,{method:'GET',redirect:'manual',signal,headers:{Accept:'application/json'},cache:'no-store'});}
    catch{throw new TransitFailure(signal.aborted?'upstream_timeout':'upstream_http',signal.aborted?'원천 교통 응답 시간이 초과되었습니다.':'원천 교통 API에 연결하지 못했습니다.');}
    if(!raw.ok){await raw.body?.cancel().catch(()=>undefined);if([401,403,429].includes(raw.status))upstreamCode(raw.status);throw new TransitFailure('upstream_http','원천 교통 API가 정상 HTTP 응답을 반환하지 않았습니다.');}
    const length=Number(raw.headers.get('Content-Length'));
    if(length>MAX_BYTES){await raw.body?.cancel().catch(()=>undefined);throw new TransitFailure('response_limit','원천 교통 응답 크기가 허용 범위를 초과했습니다.');}
    if(!raw.body)throw new TransitFailure('upstream_invalid','원천 교통 응답 본문이 없습니다.');
    const reader=raw.body.getReader(),chunks:Uint8Array[]=[];let size=0;
    const abort=()=>{void reader.cancel().catch(()=>undefined);};signal.addEventListener('abort',abort,{once:true});
    try{
      while(true){
        if(signal.aborted)throw new TransitFailure('upstream_timeout','원천 교통 응답 시간이 초과되었습니다.');
        const {done,value}=await reader.read();
        if(signal.aborted)throw new TransitFailure('upstream_timeout','원천 교통 응답 시간이 초과되었습니다.');
        if(done)break;
        size+=value.byteLength;if(size>MAX_BYTES){await reader.cancel();throw new TransitFailure('response_limit','원천 교통 응답 크기가 허용 범위를 초과했습니다.');}chunks.push(value);
      }
    }finally{signal.removeEventListener('abort',abort);reader.releaseLock();}
    const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
    let text:string;try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{throw new TransitFailure('upstream_invalid','원천 교통 응답의 문자 형식이 올바르지 않습니다.');}
    if(text.trimStart().startsWith('<')){const code=/<returnReasonCode>\s*(\d{1,3})\s*<\/returnReasonCode>/.exec(text);if(code)upstreamCode(code[1]);throw new TransitFailure('upstream_invalid','원천 교통 응답이 JSON 형식이 아닙니다.');}
    let body:unknown;try{body=JSON.parse(text);}catch{throw new TransitFailure('upstream_invalid','원천 교통 응답이 JSON 형식이 아닙니다.');}
    const gateway=object(object(object(body)?.OpenAPI_ServiceResponse)?.cmmMsgHeader);
    if(gateway)upstreamCode(gateway.returnReasonCode);
    return body;
  }
  async function busSnapshot(route:LiveBusRoute,key:string,p:Policy,signal:AbortSignal):Promise<LiveTransitSnapshot> {
    const rows:JsonObject[]=[];let total:number|null=null;
    for(let page=1;page<=BUS_MAX_PAGES;page++){
      const url=new URL(TAGO_ENDPOINT);url.search=new URLSearchParams({serviceKey:key,cityCode:route.city_code,routeId:route.route_id,pageNo:String(page),numOfRows:String(PAGE_ROWS),_type:'json'}).toString();
      const root=object(await readJson(url,'bus',p,signal)),source=object(root?.response),header=object(source?.header),body=object(source?.body);
      if(!['0','00'].includes(String(header?.resultCode)))upstreamCode(header?.resultCode);
      const count=integer(body?.totalCount),pageNo=integer(body?.pageNo),pageRows=integer(body?.numOfRows);
      if(count===null||pageNo!==page||pageRows!==PAGE_ROWS)throw new TransitFailure('upstream_inconsistent','버스 응답의 페이지 정보가 일치하지 않습니다.');
      if(count>PAGE_ROWS*BUS_MAX_PAGES)throw new TransitFailure('response_limit','노선별 차량 응답이 조회 상한을 초과했습니다.');
      if(total!==null&&total!==count)throw new TransitFailure('upstream_inconsistent','버스 조회 중 전체 차량 수가 변경되어 다시 확인해야 합니다.');
      total=count;
      const item=object(body?.items)?.item,items=item===undefined?[]:Array.isArray(item)?item:[item];
      if(items.length!==Math.min(PAGE_ROWS,Math.max(0,total-rows.length))||items.some(row=>!object(row)))throw new TransitFailure('upstream_inconsistent','버스 응답에서 페이지 누락을 발견했습니다.');
      rows.push(...items as JsonObject[]);if(rows.length===total)break;
    }
    if(rows.length!==total)throw new TransitFailure('upstream_inconsistent','버스 응답의 전체 건수가 일치하지 않습니다.');
    const instant=now(),snapshot=emptySnapshot('bus',route,p,instant),seen=new Set<string>();
    const hidden=[key,encodeURIComponent(key)],accepted:{vehicle:LiveBusVehicle;identity:string}[]=[];
    snapshot.counts.upstream=rows.length;
    for(const row of rows){
      const vehicle=safeText(row.vehicleno,40,hidden),lon=coordinate(row.gpslong),lat=coordinate(row.gpslati);
      if(!vehicle||lon===null||lat===null||lon<124||lon>132.2||lat<32.5||lat>39.5){snapshot.counts.invalid++;continue;}
      if(seen.has(vehicle))throw new TransitFailure('upstream_inconsistent','버스 응답의 차량 식별자가 중복되었습니다.');seen.add(vehicle);
      const stationOrder=integer(row.nodeord);
      accepted.push({identity:`${route.city_code}:${route.route_id}:${vehicle}`,vehicle:{kind:'bus',id:'',label:route.label,route_id:route.route_id,city_code:route.city_code,
        observed_at:null,source_received_at:null,retrieved_at:new Date(instant).toISOString(),position:{lon,lat,crs:'EPSG:4326',method:'provider-map-matched'},
        station_id:safeText(row.nodeid,60,hidden),station_name:safeText(row.nodenm,80,hidden),station_order:stationOrder!==null&&stationOrder<=10000?stationOrder:null}});
    }
    snapshot.vehicles=await Promise.all(accepted.map(async row=>({...row.vehicle,id:(await digest(row.identity)).slice(0,24)})));
    snapshot.counts.accepted=snapshot.vehicles.length;snapshot.coverage.complete=snapshot.counts.invalid===0;
    snapshot.status=rows.length===0?'empty':snapshot.vehicles.length?(snapshot.counts.invalid?'partial':'available'):'unavailable';
    if(rows.length&&!snapshot.vehicles.length)snapshot.error={code:'upstream_invalid',message:'버스 응답의 유효한 좌표를 확인하지 못했습니다.',retryable:true};
    snapshot.retrieved_at=new Date(instant).toISOString();snapshot.expires_at=new Date(instant+p.ttl*1000).toISOString();return snapshot;
  }
  async function subwaySnapshot(line:LiveSubwayLine,key:string,p:Policy,env:LiveTransitEnv,signal:AbortSignal):Promise<LiveTransitSnapshot> {
    const scheme=env.SEOUL_SUBWAY_ALLOW_HTTP==='true'?'http':'https';
    const url=new URL(`${scheme}://swopenapi.seoul.go.kr/api/subway/${encodeURIComponent(key)}/json/realtimePosition/0/${SUBWAY_MAX_ROWS}/${encodeURIComponent(line.name)}`);
    const root=object(await readJson(url,'subway',p,signal)),hasNestedMessage=root!==null&&Object.hasOwn(root,'errorMessage');
    const message=hasNestedMessage?object(root?.errorMessage):root,code=String(message?.code??'');
    const emptyCode=code==='INFO-200';
    // Seoul also returns flat INFO-200 envelopes (body status may be 500).
    // Only that explicit empty contract is supported outside errorMessage.
    if(!emptyCode&&(!hasNestedMessage||code!=='INFO-000'))upstreamCode(code);
    const value=root?.realtimePositionList;
    if((!Array.isArray(value)&&!(emptyCode&&value===undefined))||(Array.isArray(value)&&value.length>SUBWAY_MAX_ROWS))throw new TransitFailure('response_limit','지하철 응답 목록의 형식 또는 크기가 올바르지 않습니다.');
    const rows=(value??[]) as unknown[],reportedTotal=integer(message?.total);
    if((!hasNestedMessage&&reportedTotal!==0)||(emptyCode&&rows.length)||(reportedTotal!==null&&reportedTotal!==rows.length))throw new TransitFailure('upstream_inconsistent','지하철 응답의 전체 건수와 목록이 일치하지 않습니다.');
    const instant=now(),snapshot=emptySnapshot('subway',line,p,instant),latest=new Map<string,LiveSubwayVehicle>(),ambiguous=new Map<string,number>();
    const hidden=[key,encodeURIComponent(key)],states:LiveSubwayVehicle['reported_status'][]=['approaching','arrived','departed','left-previous'];
    snapshot.counts.upstream=rows.length;
    for(const value of rows){
      const row=object(value),train=safeText(row?.trainNo,16,hidden),station=safeText(row?.statnId,20,hidden),name=safeText(row?.statnNm,80,hidden);
      const received=parseTransitKstTime(row?.recptnDt),direction=String(row?.updnLine),state=integer(row?.trainSttus);
      if(!row||String(row.subwayId)!==line.subway_id||!train||!/^[A-Za-z0-9-]+$/.test(train)||!station||!/^\d+$/.test(station)||!name||received===null||!['0','1'].includes(direction)||state===null||state>3
        ||received>instant+LIVE_TRANSIT_FUTURE_TOLERANCE_SECONDS*1000){snapshot.counts.invalid++;continue;}
      if(instant-received>LIVE_SUBWAY_MAX_AGE_SECONDS*1000){snapshot.counts.stale++;continue;}
      const date=new Date(received+KST_OFFSET).toISOString().slice(0,10),id=`${line.subway_id}:${date}:${train}:${direction}`;
      const vehicle:LiveSubwayVehicle={kind:'subway',id,label:`${line.name} ${train}`,line_id:line.subway_id,train_no:train,direction:direction==='0'?'up':'down',
        observed_at:null,source_received_at:new Date(received).toISOString(),retrieved_at:new Date(instant).toISOString(),station_id:station,station_name:name,
        terminal_name:safeText(row.statnTnm,80,hidden),reported_status:states[state],position:null,station_mapping:'unresolved'};
      const previous=latest.get(id),blocked=ambiguous.get(id);
      if(blocked!==undefined&&received<=blocked){snapshot.counts.duplicate++;continue;}
      if(previous){
        snapshot.counts.duplicate++;const older=Date.parse(previous.source_received_at);
        if(received<older)continue;
        if(received===older&&(previous.station_id!==station||previous.reported_status!==vehicle.reported_status||previous.terminal_name!==vehicle.terminal_name)){
          latest.delete(id);ambiguous.set(id,received);snapshot.counts.ambiguous++;continue;
        }
      }
      ambiguous.delete(id);latest.set(id,vehicle);
    }
    snapshot.vehicles=[...latest.values()];snapshot.counts.accepted=snapshot.vehicles.length;
    snapshot.coverage.complete=snapshot.counts.invalid===0&&snapshot.counts.stale===0&&snapshot.counts.ambiguous===0;
    snapshot.status=rows.length===0?'empty':snapshot.vehicles.length?(snapshot.coverage.complete?'available':'partial'):snapshot.counts.stale?'stale':'unavailable';
    if(snapshot.status==='unavailable')snapshot.error={code:'upstream_invalid',message:'지하철 응답에서 유효한 최신 역 상태를 확인하지 못했습니다.',retryable:true};
    snapshot.retrieved_at=new Date(instant).toISOString();snapshot.expires_at=new Date(instant+p.ttl*1000).toISOString();return snapshot;
  }
  return {busSnapshot,subwaySnapshot};
}

export interface CanonicalTransitSourceRequest {
  kind:LiveTransitKind;
  target:LiveBusRoute|LiveSubwayLine;
  env:LiveTransitEnv;
  ttlSeconds:number;
  signal:AbortSignal;
  /** The broker must reserve each HTTP page before this function sends it. */
  fetcher:typeof fetch;
  now?:()=>number;
}
/** Parse one canonical source snapshot without regional cache or isolate quota.
 * Only the private broker uses this path, with its durable guarded fetcher.
 * The legacy quota label is replaced by the broker after successful validation.
 */
export async function fetchCanonicalTransitSnapshot(input:CanonicalTransitSourceRequest):Promise<{snapshot:LiveTransitSnapshot;httpStatus:number}> {
  const {env,signal,fetcher}=input,kind=input.kind==='subway'?'subway':'bus',now=input.now??Date.now;
  const p=policy(kind,1,{});let target:Target|null=null;
  try {
    if(input.kind!=='bus'&&input.kind!=='subway')throw new TransitFailure('invalid_request','지원하지 않는 교통 원천입니다.',400,false);
    target=configuredTargets([input.target],kind)[0];
    if(!Number.isInteger(input.ttlSeconds)||input.ttlSeconds<60||input.ttlSeconds>3600||typeof fetcher!=='function')throw new TransitFailure('invalid_configuration','교통 원천 조회 설정을 확인해야 합니다.',503,false);
    p.ttl=input.ttlSeconds;
    const key=credential(kind==='bus'?env.DATA_GO_KR_SERVICE_KEY:env.SEOUL_SUBWAY_API_KEY,kind);
    if(!key)throw new TransitFailure('not_configured','공식 교통 API 인증이 아직 연결되지 않았습니다.',503,false);
    if(signal.aborted)throw new TransitFailure('aborted','교통 조회가 취소되었습니다.',499,true);
    const source=createTransitSource({fetcher,now,reserve:()=>undefined});
    const snapshot=kind==='bus'?await source.busSnapshot(target as LiveBusRoute,key,p,signal):await source.subwaySnapshot(target as LiveSubwayLine,key,p,env,signal);
    return {snapshot,httpStatus:200};
  } catch(error) {
    const failure=error instanceof TransitFailure?error:new TransitFailure(signal.aborted?'upstream_timeout':'upstream_invalid','원천 교통 자료를 처리하지 못했습니다.');
    const snapshot=emptySnapshot(kind,target,p,now());
    snapshot.error={code:failure.code,message:failure.message,retryable:failure.retryable};
    snapshot.refresh_after_seconds=['quota_exceeded','upstream_auth'].includes(failure.code)?300:30;
    return {snapshot,httpStatus:failure.status};
  }
}

export function createLiveTransitHandler(options:LiveTransitOptions={}) {
  const fetcher=options.fetcher??fetch,now=options.now??Date.now;
  const edge=options.cache??(typeof caches!=='undefined'?(caches as unknown as {default?:LiveTransitCache}).default:undefined);
  const timeoutMs=Math.max(10,Math.min(options.timeoutMs??8000,15000));
  const memory=new Map<string,Stored>(),inflight=new Map<string,Promise<Stored>>(),fingerprints=new Map<string,Promise<string>>();
  const quotas=new Map<LiveTransitKind,{reset:number;used:number;blocked:boolean}>();
  const brokerClients=new WeakMap<TransitBrokerBinding,ReturnType<typeof createTransitBrokerClient>>();
  function brokerClient(env:LiveTransitEnv){
    const binding=env.LIVE_TRANSIT_BROKER;
    if(!binding||typeof binding.fetch!=='function')throw new TransitBrokerFailure('broker_unavailable');
    let client=brokerClients.get(binding);
    if(!client){client=createTransitBrokerClient(binding,{cache:edge,now,timeoutMs:options.timeoutMs});brokerClients.set(binding,client);}
    return client;
  }
  function reserve(kind:LiveTransitKind,p:Policy):void {
    const instant=now();let quota=quotas.get(kind);
    if(!quota||instant>=quota.reset){quota={reset:nextKstDay(instant),used:0,blocked:false};quotas.set(kind,quota);}
    if(quota.blocked||quota.used>=p.budget)throw new TransitFailure('quota_exceeded','교통 조회의 보수적인 일일 호출 예산을 소진했습니다.',429,true);
    quota.used++;
  }
  const {busSnapshot,subwaySnapshot}=createTransitSource({fetcher,now,reserve});
  async function load(kind:LiveTransitKind,target:Target,key:string,p:Policy,env:LiveTransitEnv,cacheKey:string,identity:string):Promise<Stored> {
    const current=memory.get(cacheKey);if(current&&current.until>now())return current;memory.delete(cacheKey);
    if(edge){
      try{
        const hit=await edge.match(new Request(cacheKey));
        if(hit){
          const saved=await hit.json() as Stored;
          if(saved.identity===identity&&saved.until>now()&&saved.until<=now()+Math.max(p.ttl,300)*1000&&saved.snapshot?.schema_version===1&&saved.snapshot.kind===kind
            &&Array.isArray(saved.snapshot.vehicles)&&saved.snapshot.vehicles.length<=SUBWAY_MAX_ROWS
            &&(kind==='bus'?saved.snapshot.source?.id==='tago'&&saved.snapshot.vehicles.every(vehicle=>vehicle.kind==='bus'
              &&vehicle.city_code===(target as LiveBusRoute).city_code&&vehicle.route_id===(target as LiveBusRoute).route_id):saved.snapshot.target.id===target.id)){
            memory.set(cacheKey,saved);return saved;
          }
        }
      }catch{/* Cache failure does not expose cached or source exception strings. */}
    }
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeoutMs);
    let stored:Stored;
    try{
      const snapshot=kind==='bus'?await busSnapshot(target as LiveBusRoute,key,p,controller.signal):await subwaySnapshot(target as LiveSubwayLine,key,p,env,controller.signal);
      stored={until:Date.parse(snapshot.expires_at!),identity,snapshot,httpStatus:200};
    }catch(error){
      const failure=error instanceof TransitFailure?error:new TransitFailure(controller.signal.aborted?'upstream_timeout':'upstream_invalid',controller.signal.aborted?'원천 교통 응답 시간이 초과되었습니다.':'원천 교통 자료를 처리하지 못했습니다.');
      if(failure.code==='quota_exceeded'){const quota=quotas.get(kind);if(quota)quota.blocked=true;}
      const snapshot=emptySnapshot(kind,target,p,now());snapshot.error={code:failure.code,message:failure.message,retryable:failure.retryable};
      const ttl=['quota_exceeded','upstream_auth'].includes(failure.code)?300:30;snapshot.refresh_after_seconds=ttl;
      stored={until:now()+ttl*1000,identity,snapshot,httpStatus:failure.status};
    }finally{clearTimeout(timer);}
    memory.set(cacheKey,stored);while(memory.size>32)memory.delete(memory.keys().next().value!);
    if(edge){try{await edge.put(new Request(cacheKey),Response.json(stored,{headers:{'Cache-Control':`public, max-age=${Math.max(1,Math.ceil((stored.until-now())/1000))}`}}));}catch{/* Serving validated data does not depend on a successful cache write. */}}
    return stored;
  }
  return async function handleLiveTransit(request:Request,env:LiveTransitEnv={}):Promise<Response|null> {
    const url=new URL(request.url);if(url.pathname!==LIVE_TRANSIT_API_PREFIX&&!url.pathname.startsWith(`${LIVE_TRANSIT_API_PREFIX}/`))return null;
    let kind:LiveTransitKind=url.pathname.endsWith('/subway')?'subway':'bus',target:Target|null=null,p:Policy={daily:kind==='bus'?10000:1000,budget:kind==='bus'?8000:800,ttl:60};
    try{
      if(request.method!=='GET')throw new TransitFailure('invalid_request','최신 교통 조회는 GET 요청만 지원합니다.',405,false);
      if(env.LIVE_TRANSIT_MODE!==undefined&&!['broker','direct'].includes(env.LIVE_TRANSIT_MODE))throw new TransitFailure('invalid_configuration','교통 조회 서버 설정을 확인해야 합니다.',503,false);
      const central=env.LIVE_TRANSIT_MODE==='broker';
      const bus=configuredTargets<LiveBusRoute>(options.busRoutes??env.LIVE_TRANSIT_BUS_ROUTES,'bus'),subway=configuredTargets<LiveSubwayLine>(options.subwayLines??env.LIVE_TRANSIT_SUBWAY_LINES,'subway');
      const busPolicy=policy('bus',bus.length,options),subwayPolicy=policy('subway',subway.length,options);
      // A recommended route can also have a national selector. Both use the
      // longer refresh interval, so changing aliases cannot increase source calls.
      if(options.busCatalog)busPolicy.ttl=Math.max(busPolicy.ttl,90);
      if(central){busPolicy.ttl=90;subwayPolicy.ttl=120;}
      if(url.pathname===`${LIVE_TRANSIT_API_PREFIX}/targets`){
        if(url.search)throw new TransitFailure('invalid_request','허용 목록 조회에는 검색 조건을 사용할 수 없습니다.',400,false);
        const health=central?await brokerClient(env).status():null;
        const configured={bus:health?health.configured.bus:Boolean(credential(env.DATA_GO_KR_SERVICE_KEY,'bus')),
          subway:health?health.configured.subway:Boolean(credential(env.SEOUL_SUBWAY_API_KEY,'subway'))};
        const targets:LiveTransitTargets={schema_version:1,mode:'live',bus_routes:bus.map(route=>({...route,configured:configured.bus,refresh_after_seconds:busPolicy.ttl})),
          subway_lines:subway.map(line=>({...line,configured:configured.subway,refresh_after_seconds:subwayPolicy.ttl})),continuous_collection:false,global_quota_enforced:Boolean(health),
          ...(health?{global_quota_scope:'broker-mediated-requests' as const}:{})};
        const catalog=await options.busCatalog?.(request,env);
        if(catalog)targets.bus_catalog={...catalog.reference,configured:configured.bus};
        return response(targets);
      }
      if(url.pathname!==`${LIVE_TRANSIT_API_PREFIX}/bus`&&url.pathname!==`${LIVE_TRANSIT_API_PREFIX}/subway`)throw new TransitFailure('invalid_request','지원하지 않는 최신 교통 조회 경로입니다.',400,false);
      kind=url.pathname.endsWith('/subway')?'subway':'bus';p=kind==='bus'?busPolicy:subwayPolicy;
      const param=kind==='bus'?'route':'line';
      if([...url.searchParams.keys()].some(name=>name!==param)||url.searchParams.getAll(param).length!==1)throw new TransitFailure('invalid_request','고정된 교통 대상 하나만 조회할 수 있습니다.',400,false);
      target=(kind==='bus'?bus:subway).find(row=>row.id===url.searchParams.get(param))??null;
      if(!target&&kind==='bus'&&/^tago-\d{1,8}-[a-z0-9가-힣_-]{1,60}$/.test(url.searchParams.get(param)??'')){
        const catalog=await options.busCatalog?.(request,env);
        target=await catalog?.resolve(url.searchParams.get(param)!)??null;
        // Only the selected route is polled. A national route count is not a
        // request fan-out and must not lengthen its freshness to several days.
        p={...busPolicy,ttl:90};
        if(target&&bus.some(route=>route.city_code===(target as LiveBusRoute).city_code&&route.route_id===(target as LiveBusRoute).route_id))p=busPolicy;
      }
      if(!target)throw new TransitFailure('invalid_request','허용 목록에 없는 교통 대상입니다.',400,false);
      if(request.signal.aborted)throw new TransitFailure('aborted','교통 조회가 취소되었습니다.',499,true);
      let stored:Stored;
      if(central){
        const result=await brokerClient(env).query(kind,target);
        stored={...result,identity:'broker',until:Date.parse(result.snapshot.expires_at??'')};
      }else{
      const key=credential(kind==='bus'?env.DATA_GO_KR_SERVICE_KEY:env.SEOUL_SUBWAY_API_KEY,kind);
      if(!key)throw new TransitFailure('not_configured','공식 교통 API 인증이 아직 연결되지 않았습니다.',503,false);
      if(request.signal.aborted)throw new TransitFailure('aborted','교통 조회가 취소되었습니다.',499,true);
      let fingerprint=fingerprints.get(key);if(!fingerprint){fingerprint=digest(key);fingerprints.set(key,fingerprint);while(fingerprints.size>4)fingerprints.delete(fingerprints.keys().next().value!);}
      const identity=kind==='bus'?`${(target as LiveBusRoute).city_code}-${(target as LiveBusRoute).route_id}`:`${(target as LiveSubwayLine).subway_id}-${(target as LiveSubwayLine).name}-${env.SEOUL_SUBWAY_ALLOW_HTTP==='true'?'http':'https'}`;
      // Provider identity is case-sensitive. Public bus IDs/labels are views of
      // this same source; credential and quota-policy scopes remain separate.
      const publicScope=kind==='subway'?`/${target.id}`:'';
      const cacheKey=`https://korea-replay-live.invalid/${CACHE_VERSION}/${kind}/${encodeURIComponent(identity)}${publicScope}/${p.ttl}/${p.daily}/${(await fingerprint).slice(0,24)}`;
      let pending=inflight.get(cacheKey);
      if(!pending){
        if(inflight.size>=MAX_INFLIGHT)throw new TransitFailure('busy','다른 최신 교통 조회가 진행 중입니다. 잠시 후 다시 확인해 주세요.',429,true);
        pending=load(kind,target,key,p,env,cacheKey,identity);inflight.set(cacheKey,pending);
        void pending.finally(()=>{if(inflight.get(cacheKey)===pending)inflight.delete(cacheKey);}).catch(()=>undefined);
      }
      stored=await pending;
      }
      if(request.signal.aborted)throw new TransitFailure('aborted','교통 조회가 취소되었습니다.',499,true);
      const snapshot={...stored.snapshot,target:{id:target.id,label:'label' in target?target.label:target.name},served_at:new Date(now()).toISOString(),counts:{...stored.snapshot.counts}};
      if(kind==='bus')snapshot.vehicles=stored.snapshot.vehicles.map(vehicle=>({...vehicle,label:(target as LiveBusRoute).label}));
      if(snapshot.kind==='subway'&&snapshot.vehicles.length){
        snapshot.vehicles=liveTransitVehicles(snapshot,now());const removed=stored.snapshot.vehicles.length-snapshot.vehicles.length;
        snapshot.counts.stale+=removed;snapshot.counts.accepted=snapshot.vehicles.length;
        if(removed){snapshot.coverage={...snapshot.coverage,complete:false};snapshot.status=snapshot.vehicles.length?'partial':'stale';}
      }
      return response(snapshot,stored.httpStatus,stored.httpStatus>=400?snapshot.refresh_after_seconds:undefined);
    }catch(error){
      const failure=error instanceof TransitFailure?error:error instanceof TransitBrokerFailure
        ?new TransitFailure(error.code,error.code==='broker_unavailable'?'교통 조회 서버에 연결하지 못했습니다. 잠시 후 다시 확인해 주세요.':error.code==='quota_exceeded'?'교통 원천의 공통 호출 예산에 도달했습니다.':error.code==='not_configured'||error.code==='upstream_auth'?'공식 교통 API 인증 또는 승인을 확인해야 합니다.':'교통 조회를 잠시 후 다시 시도해 주세요.',error.code==='busy'||error.code==='quota_exceeded'?429:error.code==='aborted'?499:503,!['not_configured','upstream_auth'].includes(error.code))
        :new TransitFailure('upstream_invalid','최신 교통 자료를 처리하지 못했습니다.');
      const snapshot=emptySnapshot(kind,target,p,now());snapshot.error={code:failure.code,message:failure.message,retryable:failure.retryable};
      if(failure.code==='busy')snapshot.refresh_after_seconds=5;
      if(env.LIVE_TRANSIT_MODE==='broker')snapshot.quota=error instanceof TransitBrokerFailure&&error.quota?error.quota:{...snapshot.quota,guard:'unavailable',global_enforced:false};
      if(error instanceof TransitBrokerFailure)snapshot.refresh_after_seconds=error.retryAfter;
      return response(snapshot,failure.status,failure.retryable?snapshot.refresh_after_seconds:undefined);
    }
  };
}
