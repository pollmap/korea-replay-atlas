import type {LiveBusRoute,LiveSubwayLine,LiveTransitKind,LiveTransitSnapshot} from '../shared/live-transit';
import {TRANSIT_ROUTE_ID_PATTERN} from '../shared/transit-catalog';
import {LIVE_BROKER_OBJECT_NAME,LIVE_BROKER_PATH,LIVE_BROKER_STATUS_PATH,LIVE_BROKER_MAX_REQUEST_BYTES,
  LIVE_BROKER_REQUEST_BODY_TIMEOUT_MS,LIVE_BROKER_SOURCE_TIMEOUT_MS,LIVE_BROKER_MAX_SOURCE_TIMEOUT_MS,LIVE_BROKER_TTL_SECONDS,
  type BrokerTarget,type BrokerRequest,type BrokerQuota,type BrokerSnapshot,type BrokerErrorCode,type BrokerFailureResult,type BrokerResult,type BrokerStatusResult} from '../shared/live-broker';
import {BrokerStoreFailure,type LiveBrokerStore,type BrokerLease} from './live-broker-store';

// Retain the original import path for existing internal callers and fixtures.
export * from '../shared/live-broker';
export interface BrokerSourceContext {
  kind:LiveTransitKind;target:LiveBusRoute|LiveSubwayLine;credential:string;ttlSeconds:number;signal:AbortSignal;fetcher:typeof fetch;
}
export interface LiveBrokerOptions {
  store:LiveBrokerStore;
  source:(context:BrokerSourceContext)=>Promise<{snapshot:LiveTransitSnapshot;httpStatus:number}>;
  credentials:(kind:LiveTransitKind)=>string|undefined;
  subwayLines?:readonly Pick<LiveSubwayLine,'subway_id'|'name'>[];
  allowSubwayHttp?:boolean;
  now?:()=>number;fetcher?:typeof fetch;timeoutMs?:number;
  /** Trusted deployment policy, never accepted from an incoming request. */
  dailyLimits?:Partial<Record<LiveTransitKind,number>>;
}
class Failure extends Error {
  constructor(readonly code:BrokerErrorCode,readonly retryAfter=30){super(code);}
}
function resultFailure(error:unknown,policy?:BrokerQuota):BrokerFailureResult {
  const failure=error instanceof Failure||error instanceof BrokerStoreFailure?error:new Failure('broker_unavailable');
  const code=failure.code;
  return {protocol:1,ok:false,httpStatus:code==='invalid_request'?400:code==='aborted'?499:code==='busy'||code==='quota_exceeded'?429:503,
    error:{code,retry_after_seconds:Math.max(1,Math.min(90_000,Math.ceil(failure.retryAfter)))},...(policy?{quota:{...policy}}:{})};
}
function record(value:unknown):Record<string,unknown>|null{return value!==null&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:null;}
function exact(row:Record<string,unknown>,keys:string[]):boolean{return Object.keys(row).length===keys.length&&keys.every(key=>Object.hasOwn(row,key));}
export function parseBrokerRequest(value:unknown):BrokerRequest {
  const request=record(value),target=record(request?.target);
  if(!request||!exact(request,['protocol','target'])||request.protocol!==1||!target)throw new Failure('invalid_request');
  if(target.kind==='bus'&&exact(target,['kind','city_code','route_id'])&&typeof target.city_code==='string'&&/^[1-9]\d{0,7}$/.test(target.city_code)
    &&typeof target.route_id==='string'&&TRANSIT_ROUTE_ID_PATTERN.test(target.route_id))return {protocol:1,target:{kind:'bus',city_code:target.city_code,route_id:target.route_id}};
  if(target.kind==='subway'&&exact(target,['kind','subway_id','name'])&&typeof target.subway_id==='string'&&/^\d{4}$/.test(target.subway_id)
    &&typeof target.name==='string'&&/^[A-Za-z0-9가-힣 -]{1,40}$/.test(target.name)&&target.name.trim()===target.name)return {protocol:1,target:{kind:'subway',subway_id:target.subway_id,name:target.name}};
  throw new Failure('invalid_request');
}
/** Only this fixed object name is selected; region, deployment and client input never select a namespace instance. */
export function fixedLiveBrokerStub<T,Id>(namespace:{idFromName(name:string):Id;get(id:Id):T}):T {
  return namespace.get(namespace.idFromName(LIVE_BROKER_OBJECT_NAME));
}
function sourceTarget(target:BrokerTarget):LiveBusRoute|LiveSubwayLine {
  return target.kind==='bus'?{id:'broker-bus',label:target.route_id,city_code:target.city_code,route_id:target.route_id}
    :{id:`broker-subway-${target.subway_id}`,name:target.name,subway_id:target.subway_id};
}
function targetKey(target:BrokerTarget):string{return target.kind==='bus'?`bus:${target.city_code}:${target.route_id}`:`subway:${target.subway_id}:${target.name}`;}
function quota(daily:number):BrokerQuota {
  const budget=Math.max(1,Math.floor(daily*.8));
  return {daily_limit:daily,local_budget:budget,budget_limit:budget,guard:'durable-object',global_enforced:true,scope:'broker-mediated-requests',window:'rolling-24h-conservative'};
}
function credential(kind:LiveTransitKind,value:string|undefined):string|undefined {
  const key=value?.trim();
  return !key||key.length<8||key.length>512||/\s/.test(key)||key.toLowerCase()==='sample'||kind==='subway'&&!/^[a-zA-Z0-9_-]+$/.test(key)?undefined:key;
}
async function fingerprint(key:string):Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(key)))].map(n=>n.toString(16).padStart(2,'0')).join('');
}
function containsSecret(body:string,key:string):boolean{return [key,encodeURIComponent(key),JSON.stringify(key).slice(1,-1)].some(secret=>body.includes(secret));}
function validSnapshot(snapshot:LiveTransitSnapshot|BrokerSnapshot,target:BrokerTarget,httpStatus:number,ttl:number,instant:number):boolean {
  if(!snapshot||snapshot.schema_version!==1||snapshot.mode!=='live'||snapshot.kind!==target.kind||!Array.isArray(snapshot.vehicles)
    ||snapshot.vehicles.length>(target.kind==='bus'?200:400)||!Number.isInteger(httpStatus)||httpStatus<200||httpStatus>599
    ||!['available','partial','empty','stale','unavailable'].includes(snapshot.status)||snapshot.target?.id!==sourceTarget(target).id
    ||snapshot.source?.id!==(target.kind==='bus'?'tago':'seoul-subway')||!snapshot.counts
    ||Object.values(snapshot.counts).some(count=>!Number.isSafeInteger(count)||count<0)||snapshot.counts.accepted!==snapshot.vehicles.length
    ||snapshot.vehicles.some(vehicle=>vehicle.kind!==target.kind||(target.kind==='bus'
      ?vehicle.kind!=='bus'||vehicle.city_code!==target.city_code||vehicle.route_id!==target.route_id
      :vehicle.kind!=='subway'||vehicle.line_id!==target.subway_id)))return false;
  if(httpStatus>=400)return snapshot.status==='unavailable'&&snapshot.retrieved_at===null&&snapshot.expires_at===null&&snapshot.vehicles.length===0;
  const retrieved=Date.parse(snapshot.retrieved_at??''),expires=Date.parse(snapshot.expires_at??'');
  return Number.isFinite(retrieved)&&retrieved<=instant+30_000&&Number.isFinite(expires)&&expires>instant&&expires<=instant+ttl*1000;
}
async function waitForClient(pending:Promise<BrokerResult>,signal?:AbortSignal):Promise<BrokerResult> {
  if(!signal)return structuredClone(await pending);
  if(signal.aborted)return resultFailure(new Failure('aborted'));
  return new Promise(resolve=>{
    const abort=()=>{signal.removeEventListener('abort',abort);resolve(resultFailure(new Failure('aborted')));};
    signal.addEventListener('abort',abort,{once:true});
    void pending.then(value=>{signal.removeEventListener('abort',abort);if(!signal.aborted)resolve(structuredClone(value));},()=>{signal.removeEventListener('abort',abort);resolve(resultFailure(new Failure('broker_unavailable')));});
  });
}
function validateUpstream(input:RequestInfo|URL,init:RequestInit|undefined,target:BrokerTarget,key:string,allowHttp:boolean):number {
  if(input instanceof Request||(init?.method??'GET').toUpperCase()!=='GET')throw new Failure('upstream_invalid');
  let url:URL;try{url=new URL(String(input));}catch{throw new Failure('upstream_invalid');}
  if(url.username||url.password||url.hash)throw new Failure('upstream_invalid');
  if(target.kind==='bus'){
    const fields=['serviceKey','cityCode','routeId','pageNo','numOfRows','_type'];
    if(url.origin!=='https://apis.data.go.kr'||url.pathname!=='/1613000/BusLcInfoInqireService/getRouteAcctoBusLcList'
      ||[...url.searchParams].length!==fields.length||fields.some(name=>url.searchParams.getAll(name).length!==1)
      ||url.searchParams.get('serviceKey')!==key||url.searchParams.get('cityCode')!==target.city_code||url.searchParams.get('routeId')!==target.route_id
      ||url.searchParams.get('numOfRows')!=='100'||url.searchParams.get('_type')!=='json'||!['1','2'].includes(url.searchParams.get('pageNo')??''))throw new Failure('upstream_invalid');
    return Number(url.searchParams.get('pageNo'));
  }
  const origin=`${allowHttp?'http':'https'}://swopenapi.seoul.go.kr`;
  if(url.origin!==origin||url.search||url.pathname!==`/api/subway/${encodeURIComponent(key)}/json/realtimePosition/0/400/${encodeURIComponent(target.name)}`)throw new Failure('upstream_invalid');
  return 1;
}
async function receive(response:Response,signal:AbortSignal):Promise<Response> {
  const maximum=256*1024,length=response.headers.get('Content-Length');
  if(length!==null&&(!/^\d+$/.test(length)||Number(length)>maximum)){await response.body?.cancel().catch(()=>undefined);throw new Failure('upstream_invalid');}
  if(!response.body)return new Response(null,{status:response.status});
  const reader=response.body.getReader(),chunks:Uint8Array[]=[];let size=0;
  const abort=()=>{void reader.cancel().catch(()=>undefined);};signal.addEventListener('abort',abort,{once:true});
  try {
    while(true){
      if(signal.aborted)throw new Failure('upstream_timeout');
      const {done,value}=await reader.read();
      if(signal.aborted)throw new Failure('upstream_timeout');
      if(done)break;
      size+=value.byteLength;if(size>maximum)throw new Failure('upstream_invalid');chunks.push(value);
    }
  }catch(error){await reader.cancel().catch(()=>undefined);throw error;}
  finally{signal.removeEventListener('abort',abort);reader.releaseLock();}
  const body=new Uint8Array(size);let offset=0;for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.byteLength;}
  // fetch() supplies decoded bytes. Do not forward an obsolete encoding/length,
  // arbitrary provider headers or a credential-bearing response URL.
  const headers=new Headers({'Content-Length':String(size)}),contentType=response.headers.get('Content-Type');
  if(contentType)headers.set('Content-Type',contentType);
  return new Response([204,205,304].includes(response.status)?null:body,{status:response.status,headers});
}

export function createLiveBroker(options:LiveBrokerOptions) {
  const store=options.store,now=options.now??Date.now,nativeFetch=options.fetcher??fetch;
  const timeout=Math.max(10,Math.min(options.timeoutMs??LIVE_BROKER_SOURCE_TIMEOUT_MS,LIVE_BROKER_MAX_SOURCE_TIMEOUT_MS)),inflight=new Map<string,Promise<BrokerResult>>();
  const ready=store.initialize();void ready.catch(()=>undefined);
  const policies={bus:quota(options.dailyLimits?.bus??10000),subway:quota(options.dailyLimits?.subway??1000)};
  const validPolicy=Object.values(policies).every(p=>Number.isSafeInteger(p.daily_limit)&&p.daily_limit>=1&&p.daily_limit<=1_000_000);

  async function execute(target:BrokerTarget,key:string,epoch:string,cacheKey:string,p:BrokerQuota,ttl:number):Promise<BrokerResult> {
    let lease:BrokerLease|undefined,ownFailure:unknown,result:BrokerResult=resultFailure(new Failure('broker_unavailable'));
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined,pageBusy=false,pages=0;
    try {
      store.checkAuthentication(target.kind,epoch,now());
      store.checkBudget(target.kind,p.budget_limit,now());
      lease=await store.acquire(crypto.randomUUID(),cacheKey,target.kind,now());
      timer=setTimeout(()=>controller.abort(),timeout);
      const guardedFetch:typeof fetch=async(input,init)=>{
        try{
          if(controller.signal.aborted)throw new Failure('upstream_timeout');
          const page=validateUpstream(input,init,target,key,options.allowSubwayHttp===true);
          if(pageBusy||page!==pages+1)throw new Failure('upstream_invalid');
          pageBusy=true;
          try {
            await store.reserve(lease!,p.budget_limit,now(),epoch);
            if(controller.signal.aborted)throw new Failure('upstream_timeout');
            pages++;
            try{
              const response=await nativeFetch(input,{method:'GET',redirect:'manual',signal:controller.signal,headers:{Accept:'application/json'},cache:'no-store'});
              return await receive(response,controller.signal);
            }catch(error){throw error instanceof Failure?error:new Failure(controller.signal.aborted?'upstream_timeout':'upstream_http');}
          }finally{pageBusy=false;}
        }catch(error){ownFailure??=error instanceof Failure||error instanceof BrokerStoreFailure?error:new Failure(controller.signal.aborted?'upstream_timeout':'broker_unavailable');throw ownFailure;}
      };
      const source=await options.source({kind:target.kind,target:sourceTarget(target),credential:key,ttlSeconds:ttl,signal:controller.signal,fetcher:guardedFetch});
      if(ownFailure)throw ownFailure;
      if(controller.signal.aborted)throw new Failure('upstream_timeout');
      const snapshot=source.snapshot;
      if(!pages||!validSnapshot(snapshot,target,source.httpStatus,ttl,now()))throw new Failure('upstream_invalid');
      if(snapshot.error?.code==='quota_exceeded')await store.block(target.kind,now());
      if(snapshot.error?.code==='upstream_auth')await store.blockAuthentication(target.kind,epoch,now());
      result={protocol:1,ok:true,httpStatus:source.httpStatus,snapshot:{...snapshot,quota:p}};
      const serialized=JSON.stringify(result);
      if(containsSecret(serialized,key))throw new Failure('upstream_invalid');
      const expires=source.httpStatus>=400?now()+(['quota_exceeded','upstream_auth'].includes(snapshot.error?.code??'')?300:30)*1000:Date.parse(snapshot.expires_at??'');
      if(!Number.isFinite(expires)||expires<=now()||expires>now()+Math.max(ttl,300)*1000)throw new Failure('upstream_invalid');
      await store.save(cacheKey,{body:serialized,bytes:new TextEncoder().encode(serialized).byteLength,expires},now());
    }catch(error){result=resultFailure(error);}
    finally{
      if(timer!==undefined)clearTimeout(timer);
      if(lease){try{await store.release(lease);}catch{result=resultFailure(new Failure('broker_unavailable'));}}
    }
    return result;
  }
  async function query(input:unknown,signal?:AbortSignal):Promise<BrokerResult> {
    let confirmedPolicy:BrokerQuota|undefined;
    try {
      if(signal?.aborted)throw new Failure('aborted');
      const {target}=parseBrokerRequest(input);
      if(target.kind==='subway'&&!options.subwayLines?.some(line=>line.subway_id===target.subway_id&&line.name===target.name))throw new Failure('invalid_request');
      if(!validPolicy)throw new Failure('broker_unavailable');
      const p=policies[target.kind];confirmedPolicy=p;
      const key=credential(target.kind,options.credentials(target.kind));
      if(!key)throw new Failure('not_configured');
      await ready;if(signal?.aborted)throw new Failure('aborted');
      const ttl=LIVE_BROKER_TTL_SECONDS[target.kind];
      const epoch=await fingerprint(key),cacheKey=`v1:${targetKey(target)}:${p.daily_limit}:${ttl}:${epoch}:${target.kind==='subway'&&options.allowSubwayHttp===true?'http':'https'}`;
      if(signal?.aborted)throw new Failure('aborted');
      const saved=store.cached(cacheKey,now());
      if(saved&&saved.expires>now()){
        const checkedAt=now();
        const value=JSON.parse(saved.body) as BrokerResult;
        if(value.protocol!==1||!value.ok||JSON.stringify(value.snapshot?.quota)!==JSON.stringify(p)||!validSnapshot(value.snapshot,target,value.httpStatus,ttl,checkedAt)
          ||value.httpStatus<400&&Date.parse(value.snapshot.expires_at!)!==saved.expires||containsSecret(saved.body,key))throw new Failure('broker_unavailable');
        return value;
      }
      let pending=inflight.get(cacheKey);
      if(!pending){
        if(inflight.size>=2)throw new Failure('busy',5);
        pending=execute(target,key,epoch,cacheKey,p,ttl);inflight.set(cacheKey,pending);
        void pending.finally(()=>{if(inflight.get(cacheKey)===pending)inflight.delete(cacheKey);}).catch(()=>undefined);
      }
      const result=await waitForClient(pending,signal);
      return result.ok?result:{...result,quota:{...p}};
    }catch(error){return resultFailure(error,confirmedPolicy);}
  }
  async function status(signal?:AbortSignal):Promise<BrokerStatusResult> {
    try {
      if(signal?.aborted)throw new Failure('aborted');
      if(!validPolicy)throw new Failure('broker_unavailable');
      await ready;if(signal?.aborted)throw new Failure('aborted');
      return {protocol:1,ok:true,httpStatus:200,
        configured:{bus:Boolean(credential('bus',options.credentials('bus'))),subway:Boolean(credential('subway',options.credentials('subway')))},
        guard:'durable-object',global_enforced:true,scope:'broker-mediated-requests',window:'rolling-24h-conservative',
        ttl_seconds:{...LIVE_BROKER_TTL_SECONDS},daily_limits:{bus:policies.bus.daily_limit,subway:policies.subway.daily_limit},
        budget_limits:{bus:policies.bus.budget_limit,subway:policies.subway.budget_limit},...store.status(now())};
    }catch(error){return resultFailure(error);}
  }
  async function fetchSnapshot(request:Request):Promise<BrokerResult> {
    try {
      if(request.signal.aborted)throw new Failure('aborted');
      const length=request.headers.get('Content-Length');if(length!==null&&(!/^\d+$/.test(length)||Number(length)>LIVE_BROKER_MAX_REQUEST_BYTES))throw new Failure('invalid_request');
      if(!request.body)throw new Failure('invalid_request');
      const reader=request.body.getReader(),parts:Uint8Array[]=[];let size=0;
      let timedOut=false;const cancel=()=>{void reader.cancel().catch(()=>undefined);};
      const timer=setTimeout(()=>{timedOut=true;cancel();},LIVE_BROKER_REQUEST_BODY_TIMEOUT_MS);request.signal.addEventListener('abort',cancel,{once:true});
      try{while(true){
        if(request.signal.aborted)throw new Failure('aborted');
        const {done,value}=await reader.read();
        if(request.signal.aborted)throw new Failure('aborted');if(timedOut)throw new Failure('invalid_request');
        if(done)break;size+=value.byteLength;if(size>LIVE_BROKER_MAX_REQUEST_BYTES)throw new Failure('invalid_request');parts.push(value);
      }}catch(error){await reader.cancel().catch(()=>undefined);throw error;}
      finally{clearTimeout(timer);request.signal.removeEventListener('abort',cancel);reader.releaseLock();}
      const bytes=new Uint8Array(size);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.byteLength;}
      let body:unknown;try{body=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));}catch{throw new Failure('invalid_request');}
      return await query(body,request.signal);
    }catch(error){return resultFailure(error);}
  }
  return {query,status,async fetch(request:Request):Promise<Response>{
    const url=new URL(request.url);
    const result=url.search?resultFailure(new Failure('invalid_request'))
      :request.method==='GET'&&url.pathname===LIVE_BROKER_STATUS_PATH?await status(request.signal)
      :request.method==='POST'&&url.pathname===LIVE_BROKER_PATH?await fetchSnapshot(request)
      :resultFailure(new Failure('invalid_request'));
    return Response.json(result,{status:result.httpStatus,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...(!result.ok?{'Retry-After':String(result.error.retry_after_seconds)}:{})}});
  }};
}
