import {validTransitQuota,type LiveBusRoute,type LiveSubwayLine,type LiveTransitKind,type LiveTransitSnapshot,type LiveTransitQuota} from '../shared/live-transit';
import {LIVE_BROKER_MAX_SNAPSHOT_BYTES,LIVE_BROKER_RESPONSE_TIMEOUT_MS,LIVE_BROKER_PATH,LIVE_BROKER_STATUS_PATH,type BrokerResult,type BrokerStatus} from '../shared/live-broker';

export type TransitBrokerBinding=Pick<Fetcher,'fetch'>;
type Target=LiveBusRoute|LiveSubwayLine;
interface EdgeCache {match(request:Request):Promise<Response|undefined>;put(request:Request,response:Response):Promise<void>;}
interface Options {now?:()=>number;cache?:EdgeCache;timeoutMs?:number;}
export class TransitBrokerFailure extends Error {
  constructor(readonly code:'broker_unavailable'|'not_configured'|'busy'|'quota_exceeded'|'upstream_auth'|'aborted',readonly retryAfter=30,readonly quota?:LiveTransitQuota){super(code);}
}
type Row=Record<string,unknown>;
const row=(value:unknown):value is Row=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const integer=(value:unknown,min=0,max=1_000_000):value is number=>typeof value==='number'&&Number.isSafeInteger(value)&&value>=min&&value<=max;
const text=(value:unknown,max=500):value is string=>typeof value==='string'&&value.length<=max&&![...value].some(c=>c.charCodeAt(0)<32||c.charCodeAt(0)===127);
const time=(value:unknown):value is string=>typeof value==='string'&&Number.isFinite(Date.parse(value));
const optionalText=(value:unknown,max=100)=>value===null||text(value,max);
const canonical=(kind:LiveTransitKind,target:Target)=>kind==='bus'?{kind,city_code:(target as LiveBusRoute).city_code,route_id:(target as LiveBusRoute).route_id}
  :{kind,subway_id:(target as LiveSubwayLine).subway_id,name:(target as LiveSubwayLine).name};

/** Reject cache/binding corruption before any payload can enter a public response. */
export function validBrokerSnapshot(value:unknown,kind:LiveTransitKind,target:Target,now:number):value is Extract<BrokerResult,{ok:true}> {
  if(!row(value)||value.protocol!==1||value.ok!==true||!integer(value.httpStatus,200,599)||!row(value.snapshot))return false;
  const s=value.snapshot,source=kind==='bus'?'tago':'seoul-subway',ttl=kind==='bus'?90:120;
  const targetId=kind==='bus'?'broker-bus':`broker-subway-${(target as LiveSubwayLine).subway_id}`;
  if(s.schema_version!==1||s.mode!=='live'||s.kind!==kind||!['available','partial','empty','stale','unavailable'].includes(String(s.status))
    ||!row(s.target)||s.target.id!==targetId||s.target.label!==(kind==='bus'?(target as LiveBusRoute).route_id:(target as LiveSubwayLine).name)||!row(s.source)||s.source.id!==source||s.source.access!=='official-key'
    ||s.source.page_url!==(kind==='bus'?'https://www.data.go.kr/data/15098533/openapi.do':'https://data.seoul.go.kr/dataList/OA-12601/A/1/datasetView.do')
    ||!text(s.source.license)||!row(s.coverage)||s.coverage.scope!==(kind==='bus'?'selected-route':'selected-line')||typeof s.coverage.complete!=='boolean'
    ||!row(s.counts)||!['upstream','accepted','invalid','stale','duplicate','ambiguous'].every(k=>integer((s.counts as Row)[k]))
    ||!validTransitQuota(s.quota)||s.quota.guard!=='durable-object'||!time(s.served_at)||!integer(s.refresh_after_seconds,1,90000)
    ||s.max_source_age_seconds!==(kind==='bus'?null:300)||!Array.isArray(s.vehicles)||s.vehicles.length>(kind==='bus'?200:400)
    ||s.counts.accepted!==s.vehicles.length)return false;
  if(s.error!==undefined&&(!row(s.error)||!text(s.error.message)||typeof s.error.retryable!=='boolean'
    ||!['invalid_request','not_configured','invalid_configuration','upstream_auth','upstream_http','upstream_timeout','upstream_invalid','upstream_inconsistent','response_limit','quota_exceeded','busy','aborted','broker_unavailable'].includes(String(s.error.code))))return false;
  if(value.httpStatus>=400)return s.status==='unavailable'&&s.vehicles.length===0&&s.retrieved_at===null&&s.expires_at===null&&row(s.error);
  if(value.httpStatus!==200||!time(s.retrieved_at)||!time(s.expires_at)||Date.parse(s.retrieved_at)>now+30_000
    ||Date.parse(s.expires_at)<=now||Date.parse(s.expires_at)>now+ttl*1000||Date.parse(s.expires_at)<=Date.parse(s.retrieved_at)
    ||s.refresh_after_seconds!==ttl)return false;
  if(s.status==='unavailable'&&(s.vehicles.length!==0||!row(s.error)))return false;
  if((s.status==='empty'||s.status==='stale')&&s.vehicles.length!==0)return false;
  if((s.status==='available'||s.status==='partial')&&s.vehicles.length===0)return false;
  return s.vehicles.every((v:unknown)=>{
    if(!row(v)||v.kind!==kind||!text(v.id,100)||!text(v.label,100)||v.observed_at!==null||!time(v.retrieved_at)||v.retrieved_at!==s.retrieved_at)return false;
    if(kind==='bus'){
      const p=v.position,t=target as LiveBusRoute;
      return v.route_id===t.route_id&&v.city_code===t.city_code&&v.source_received_at===null&&row(p)
        &&typeof p.lon==='number'&&Number.isFinite(p.lon)&&p.lon>=124&&p.lon<=132.5&&typeof p.lat==='number'&&Number.isFinite(p.lat)&&p.lat>=32&&p.lat<=39.5
        &&p.crs==='EPSG:4326'&&p.method==='provider-map-matched'&&optionalText(v.station_id)&&optionalText(v.station_name)
        &&(v.station_order===null||integer(v.station_order));
    }
    return v.line_id===(target as LiveSubwayLine).subway_id&&v.position===null&&v.station_mapping==='unresolved'
      &&time(v.source_received_at)&&text(v.train_no,80)&&text(v.station_id,80)&&text(v.station_name,80)&&optionalText(v.terminal_name)
      &&['up','down'].includes(String(v.direction))&&['approaching','arrived','departed','left-previous'].includes(String(v.reported_status));
  });
}

function statusResponse(value:unknown):BrokerStatus {
  if(!row(value)||value.protocol!==1||value.ok!==true||value.httpStatus!==200||value.guard!=='durable-object'||value.global_enforced!==true
    ||value.scope!=='broker-mediated-requests'||value.window!=='rolling-24h-conservative'||!row(value.configured)||!row(value.ttl_seconds)
    ||!row(value.daily_limits)||!row(value.budget_limits)||!row(value.reserved)||!integer(value.active_leases,0,2))throw new TransitBrokerFailure('broker_unavailable');
  for(const kind of ['bus','subway'] as const)if(typeof value.configured[kind]!=='boolean'||value.ttl_seconds[kind]!==(kind==='bus'?90:120)
    ||!integer(value.daily_limits[kind],1)||!integer(value.budget_limits[kind],1)||Number(value.budget_limits[kind])>Number(value.daily_limits[kind])
    ||!integer(value.reserved[kind]))throw new TransitBrokerFailure('broker_unavailable');
  return value as unknown as BrokerStatus;
}
async function jsonBody(response:Response,signal:AbortSignal,limit:number):Promise<unknown> {
  const length=response.headers.get('Content-Length');
  if(!response.body||length!==null&&(!/^\d+$/.test(length)||Number(length)>limit)){await response.body?.cancel();throw new TransitBrokerFailure('broker_unavailable');}
  const reader=response.body.getReader(),chunks:Uint8Array[]=[];let size=0;
  const abort=()=>{void reader.cancel().catch(()=>undefined);};signal.addEventListener('abort',abort,{once:true});
  try{
    while(true){
      if(signal.aborted)throw new TransitBrokerFailure('broker_unavailable');
      const {done,value}=await reader.read();if(signal.aborted)throw new TransitBrokerFailure('broker_unavailable');
      if(done)break;size+=value.byteLength;if(size>limit)throw new TransitBrokerFailure('broker_unavailable');chunks.push(value);
    }
    const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
    return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
  }catch{await reader.cancel().catch(()=>undefined);throw new TransitBrokerFailure('broker_unavailable');}
  finally{signal.removeEventListener('abort',abort);reader.releaseLock();}
}

/** Optional regional caching must not keep a request alive indefinitely. */
async function boundedCache<T>(pending:Promise<T>):Promise<T|undefined> {
  let timer:ReturnType<typeof setTimeout>|undefined;
  try{return await Promise.race([pending.catch(()=>undefined),new Promise<undefined>(resolve=>{timer=setTimeout(()=>resolve(undefined),250);})]);}
  finally{if(timer!==undefined)clearTimeout(timer);}
}

export function createTransitBrokerClient(binding:TransitBrokerBinding,options:Options={}) {
  const now=options.now??Date.now,timeout=Math.max(10,Math.min(options.timeoutMs??LIVE_BROKER_RESPONSE_TIMEOUT_MS,LIVE_BROKER_RESPONSE_TIMEOUT_MS));
  const edge=options.cache??(typeof caches!=='undefined'?(caches as unknown as {default?:EdgeCache}).default:undefined);
  const memory=new Map<string,Extract<BrokerResult,{ok:true}>>(),inflight=new Map<string,Promise<Extract<BrokerResult,{ok:true}>>>();
  let health:{until:number;value:BrokerStatus}|undefined,healthPending:Promise<BrokerStatus>|undefined;
  async function call(path:string,body?:unknown):Promise<{body:unknown;status:number}> {
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
    try{
      const operation=(async()=>{const response=await binding.fetch(new Request(`https://transit-broker.internal${path}`,{method:body===undefined?'GET':'POST',signal:controller.signal,
        headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})}));
      return {body:await jsonBody(response,controller.signal,body===undefined?4096:LIVE_BROKER_MAX_SNAPSHOT_BYTES),status:response.status};})();
      return await Promise.race([operation,new Promise<never>((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(new TransitBrokerFailure('broker_unavailable'));},timeout);})]);
    }catch{throw new TransitBrokerFailure('broker_unavailable');}finally{if(timer!==undefined)clearTimeout(timer);}
  }
  const status=async():Promise<BrokerStatus>=>{
    if(health&&health.until>now())return health.value;
    if(!healthPending){healthPending=(async()=>{const response=await call(LIVE_BROKER_STATUS_PATH);if(response.status!==200)throw new TransitBrokerFailure('broker_unavailable');
      const value=statusResponse(response.body);health={until:now()+15000,value};return value;})();
      void healthPending.finally(()=>{healthPending=undefined;}).catch(()=>undefined);}
    return healthPending;
  };
  const query=async(kind:LiveTransitKind,target:Target):Promise<{snapshot:LiveTransitSnapshot;httpStatus:number}>=>{
    const key=`https://korea-replay-live.invalid/broker-v1/${encodeURIComponent(JSON.stringify(canonical(kind,target)))}`;
    const remembered=memory.get(key);if(remembered&&validBrokerSnapshot(remembered,kind,target,now()))return {snapshot:structuredClone(remembered.snapshot),httpStatus:remembered.httpStatus};
    memory.delete(key);
    if(edge){try{const cached=await boundedCache(edge.match(new Request(key)));if(cached){const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),1000);
      let value:unknown;try{value=await jsonBody(cached,abort.signal,LIVE_BROKER_MAX_SNAPSHOT_BYTES);}finally{clearTimeout(timer);}
      if(validBrokerSnapshot(value,kind,target,now())&&value.httpStatus===200){memory.set(key,value);while(memory.size>32)memory.delete(memory.keys().next().value!);return {snapshot:structuredClone(value.snapshot),httpStatus:value.httpStatus};}
    }}catch{/* A cache failure cannot activate a direct provider request. */}}
    let pending=inflight.get(key);
    if(!pending){
      if(inflight.size>=2)throw new TransitBrokerFailure('busy',5);
      pending=(async()=>{
        const result=await call(LIVE_BROKER_PATH,{protocol:1,target:canonical(kind,target)}),value=result.body;
        if(row(value)&&value.protocol===1&&value.ok===false&&row(value.error)&&value.httpStatus===result.status){
          const code=['not_configured','busy','quota_exceeded','upstream_auth','aborted'].includes(String(value.error.code))?value.error.code as TransitBrokerFailure['code']:'broker_unavailable';
          throw new TransitBrokerFailure(code,integer(value.error.retry_after_seconds,1,90000)?value.error.retry_after_seconds:30,
            validTransitQuota(value.quota)&&value.quota.guard==='durable-object'?value.quota:undefined);
        }
        if(!validBrokerSnapshot(value,kind,target,now())||result.status!==value.httpStatus)throw new TransitBrokerFailure('broker_unavailable');
        if(value.httpStatus===200){
          memory.set(key,value);while(memory.size>32)memory.delete(memory.keys().next().value!);
          if(edge){try{await boundedCache(edge.put(new Request(key),Response.json(value,{headers:{'Cache-Control':`public, max-age=${Math.max(1,Math.floor((Date.parse(value.snapshot.expires_at!)-now())/1000))}`}})));}catch{/* Still valid central data may be served if a regional cache write fails. */}}
        }
        return value;
      })();inflight.set(key,pending);void pending.finally(()=>{if(inflight.get(key)===pending)inflight.delete(key);}).catch(()=>undefined);
    }
    const value=await pending;
    if(!validBrokerSnapshot(value,kind,target,now())){memory.delete(key);throw new TransitBrokerFailure('broker_unavailable');}
    return {snapshot:structuredClone(value.snapshot),httpStatus:value.httpStatus};
  };
  return {status,query};
}
