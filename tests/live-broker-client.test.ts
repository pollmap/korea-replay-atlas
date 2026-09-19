import {afterEach,describe,expect,it,vi} from 'vitest';
import {createTransitBrokerClient,validBrokerSnapshot,type TransitBrokerBinding} from '../worker/live-broker-client';
import {createLiveTransitHandler,fetchCanonicalTransitSnapshot,type LiveTransitEnv} from '../worker/live-transit';
import {decodeLiveTargets,decodeLiveTransit} from '../src/live-client';
import type {BrokerResult,BrokerStatus} from '../shared/live-broker';

const START=Date.parse('2026-09-19T12:00:00Z'),route={id:'busan-1001',city_code:'21',route_id:'BSB1001',label:'부산 1001'};
const line={id:'seoul-line-1',subway_id:'1001',name:'1호선'},quota={daily_limit:10000,local_budget:8000,budget_limit:8000,guard:'durable-object',global_enforced:true,scope:'broker-mediated-requests',window:'rolling-24h-conservative'} as const;
const health=():BrokerStatus=>({protocol:1,ok:true,httpStatus:200,configured:{bus:true,subway:true},guard:'durable-object',global_enforced:true,scope:'broker-mediated-requests',window:'rolling-24h-conservative',ttl_seconds:{bus:90,subway:120},daily_limits:{bus:10000,subway:1000},budget_limits:{bus:8000,subway:800},reserved:{bus:0,subway:0},active_leases:0});
async function fixture(at=START):Promise<Extract<BrokerResult,{ok:true}>> {
  const source=await fetchCanonicalTransitSnapshot({kind:'bus',target:{...route,id:'broker-bus',label:route.route_id},env:{DATA_GO_KR_SERVICE_KEY:'fixture-private-broker-key'},ttlSeconds:90,
    now:()=>at,signal:new AbortController().signal,fetcher:async()=>Response.json({response:{header:{resultCode:'00'},body:{pageNo:1,numOfRows:100,totalCount:1,items:{item:[{vehicleno:'test-bus',gpslong:129.1,gpslati:35.2}]}}}})});
  return {protocol:1,ok:true,...source,snapshot:{...source.snapshot,quota}};
}
class Cache {
  values=new Map<string,Response>();
  async match(request:Request){return this.values.get(request.url)?.clone();}
  async put(request:Request,response:Response){this.values.set(request.url,response.clone());}
}
function binding(handler:(request:Request)=>Promise<Response>){
  const calls:Pick<Request,'url'|'method'|'json'>[]=[];
  const service:TransitBrokerBinding={fetch:vi.fn(async(input:RequestInfo|URL,init?:RequestInit)=>{const request=(input instanceof Request?input:new Request(input,init)) as Request;calls.push(request.clone());return handler(request);}) as Fetcher['fetch']};
  return {service,calls};
}
afterEach(()=>vi.useRealTimers());

describe('map to private transit broker',()=>{
  it('uses broker status without map credentials and omits private counters from public targets',async()=>{
    const {service,calls}=binding(async()=>Response.json(health()));const source=vi.fn<typeof fetch>();
    const handle=createLiveTransitHandler({busRoutes:[route],subwayLines:[line],fetcher:source,now:()=>START});
    const response=await handle(new Request('https://map.test/api/v1/live/transit/targets'),{LIVE_TRANSIT_MODE:'broker',LIVE_TRANSIT_BROKER:service});
    const value=decodeLiveTargets(await response!.json());expect(value).toMatchObject({global_quota_enforced:true,global_quota_scope:'broker-mediated-requests',continuous_collection:false});
    expect(value.bus_routes[0]).toMatchObject({configured:true,refresh_after_seconds:90});expect(value.subway_lines[0].configured).toBe(true);
    expect(calls[0].method).toBe('GET');expect(new URL(calls[0].url).pathname).toBe('/v1/status');expect(source).not.toHaveBeenCalled();
    expect(value).not.toHaveProperty('reserved');
  });
  it('restores public aliases while sharing the canonical regional response',async()=>{
    const payload=await fixture(),{service,calls}=binding(async()=>Response.json(payload));
    const alias={...route,id:'busan-alias',label:'같은 노선'},source=vi.fn<typeof fetch>();
    const handle=createLiveTransitHandler({busRoutes:[route],fetcher:source,now:()=>START,busCatalog:async()=>({reference:{url:'/data/live-transit/routes/0123456789abcdef/manifest.json',sha256:'a'.repeat(64),byte_length:300},resolve:async()=>alias})});
    const env:LiveTransitEnv={LIVE_TRANSIT_MODE:'broker',LIVE_TRANSIT_BROKER:service};
    const one=await handle(new Request(`https://map.test/api/v1/live/transit/bus?route=${route.id}`),env);
    const two=await handle(new Request('https://map.test/api/v1/live/transit/bus?route=tago-21-bsb1001'),env);
    expect(decodeLiveTransit(await one!.json())).toMatchObject({target:{id:route.id,label:route.label},quota:{global_enforced:true}});
    expect(decodeLiveTransit(await two!.json())).toMatchObject({target:{id:alias.id,label:alias.label},vehicles:[{label:alias.label,route_id:route.route_id}]});
    expect(calls).toHaveLength(1);expect(await calls[0].json()).toEqual({protocol:1,target:{kind:'bus',city_code:'21',route_id:'BSB1001'}});expect(source).not.toHaveBeenCalled();
  });
  it.each([undefined,{fetch:async()=>{throw new Error('fixture-secret');}},{fetch:async()=>new Response('bad',{status:502})}])('never bypasses a missing or broken broker with direct credentials',async service=>{
    const source=vi.fn<typeof fetch>(),handle=createLiveTransitHandler({busRoutes:[route],fetcher:source,now:()=>START});
    const response=await handle(new Request(`https://map.test/api/v1/live/transit/bus?route=${route.id}`),{LIVE_TRANSIT_MODE:'broker',LIVE_TRANSIT_BROKER:service as TransitBrokerBinding|undefined,DATA_GO_KR_SERVICE_KEY:'fixture-direct-key'});
    expect(response!.status).toBe(503);const body=await response!.text();expect(body).toContain('broker_unavailable');expect(body).not.toContain('fixture-secret');expect(source).not.toHaveBeenCalled();
  });
  it('validates public selectors before invoking the private service',async()=>{
    const {service,calls}=binding(async()=>Response.json(health())),handle=createLiveTransitHandler({busRoutes:[route]});
    for(const path of ['/bus?route=unknown','/bus?route=busan-1001&cityCode=99','/targets?status=1'])expect((await handle(new Request(`https://map.test/api/v1/live/transit${path}`),{LIVE_TRANSIT_MODE:'broker',LIVE_TRANSIT_BROKER:service}))!.status).toBe(400);
    expect(calls).toHaveLength(0);
  });
  it('deduplicates simultaneous requests and retains per-request copies',async()=>{
    const payload=await fixture();let resolve!:()=>void;const gate=new Promise<void>(r=>resolve=r);
    const {service,calls}=binding(async()=>{await gate;return Response.json(payload);}),client=createTransitBrokerClient(service,{now:()=>START});
    const one=client.query('bus',route),two=client.query('bus',{...route,id:'another'});resolve();const [a,b]=await Promise.all([one,two]);
    expect(calls).toHaveLength(1);a.snapshot.vehicles.length=0;expect(b.snapshot.vehicles).toHaveLength(1);
  });
  it('shares valid regional cache between isolates without extending expiry',async()=>{
    const cache=new Cache(),payload=await fixture();let now=START;
    const {service,calls}=binding(async()=>Response.json(await fixture(now)));
    const first=createTransitBrokerClient(service,{cache,now:()=>now});await first.query('bus',route);now+=60_000;
    const second=createTransitBrokerClient(service,{cache,now:()=>now});const hit=await second.query('bus',route);
    expect(hit.snapshot.expires_at).toBe(payload.snapshot.expires_at);expect(calls).toHaveLength(1);
    now+=31_000;await second.query('bus',route);expect(calls).toHaveLength(2);
  });
  it('rejects an invalid cached target and retrieves a correct central snapshot',async()=>{
    const cache=new Cache(),payload=await fixture(),{service,calls}=binding(async()=>Response.json(payload));
    await createTransitBrokerClient(service,{cache,now:()=>START}).query('bus',route);
    const key=[...cache.values.keys()][0],bad=structuredClone(payload);bad.snapshot.vehicles[0]={...bad.snapshot.vehicles[0],kind:'bus',route_id:'WRONG'} as typeof bad.snapshot.vehicles[0];cache.values.set(key,Response.json(bad));
    await createTransitBrokerClient(service,{cache,now:()=>START}).query('bus',route);expect(calls).toHaveLength(2);
  });
  it.each(['quota_exceeded','upstream_auth','busy'] as const)('preserves sanitized broker failure %s and its retry interval',async code=>{
    const status=code==='upstream_auth'?503:429,{service}=binding(async()=>Response.json({protocol:1,ok:false,httpStatus:status,quota,error:{code,retry_after_seconds:300}},{status}));
    const handle=createLiveTransitHandler({busRoutes:[route],now:()=>START}),result=await handle(new Request(`https://map.test/api/v1/live/transit/bus?route=${route.id}`),{LIVE_TRANSIT_MODE:'broker',LIVE_TRANSIT_BROKER:service});
    expect(result!.status).toBe(status);expect(await result!.json()).toMatchObject({status:'unavailable',vehicles:[],error:{code},refresh_after_seconds:300,quota:{guard:'durable-object',global_enforced:true}});
  });
  it('bounds the whole body and ends a stalled body at the binding deadline',async()=>{
    vi.useFakeTimers();
    const cancelled=vi.fn(),{service}=binding(async()=>new Response(new ReadableStream({cancel:cancelled}))),client=createTransitBrokerClient(service,{timeoutMs:20});
    const pending=client.query('bus',route);const result=expect(pending).rejects.toMatchObject({code:'broker_unavailable'});await vi.advanceTimersByTimeAsync(21);await result;expect(cancelled).toHaveBeenCalled();
    const large=binding(async()=>new Response('x'.repeat(524289))).service;
    await expect(createTransitBrokerClient(large).query('bus',route)).rejects.toMatchObject({code:'broker_unavailable'});
  });
  it('rejects malformed quota, source URL, coordinates and stale response lifetimes',async()=>{
    const payload=await fixture();expect(validBrokerSnapshot(payload,'bus',route,START)).toBe(true);
    const changes=[{quota:{...quota,global_enforced:false}},{source:{...payload.snapshot.source,page_url:'https://attacker.test'}},{expires_at:new Date(START).toISOString()},
      {vehicles:[{...payload.snapshot.vehicles[0],position:{lon:0,lat:0,crs:'EPSG:4326',method:'provider-map-matched'}}]}];
    for(const change of changes)expect(validBrokerSnapshot({...payload,snapshot:{...payload.snapshot,...change}},'bus',route,START)).toBe(false);
    expect(()=>decodeLiveTransit({...payload.snapshot,quota:{...quota,scope:'all-account-requests'}})).toThrow();
    expect(()=>decodeLiveTargets({...health(),schema_version:1,mode:'live',bus_routes:[],subway_lines:[],continuous_collection:false,global_quota_enforced:true})).toThrow();
  });
  it('rejects an empty snapshot for another canonical route',async()=>{
    const payload=await fixture();payload.snapshot.status='empty';payload.snapshot.vehicles=[];payload.snapshot.counts={upstream:0,accepted:0,invalid:0,stale:0,duplicate:0,ambiguous:0};
    expect(validBrokerSnapshot(payload,'bus',route,START)).toBe(true);payload.snapshot.target.label='OTHER';
    expect(validBrokerSnapshot(payload,'bus',route,START)).toBe(false);
  });
  it('rechecks expiry after a regional write and never revives expired vehicles',async()=>{
    let instant=START;const payload=await fixture(),{service}=binding(async()=>Response.json(payload));
    const cache={match:async()=>undefined,put:async()=>{instant=START+90_001;}};
    await expect(createTransitBrokerClient(service,{cache,now:()=>instant}).query('bus',route)).rejects.toMatchObject({code:'broker_unavailable'});
  });
  it('bounds nonresponsive cache match and put without delaying a valid result indefinitely',async()=>{
    const payload=await fixture();vi.useFakeTimers();const {service}=binding(async()=>Response.json(payload));
    const never=()=>new Promise<undefined>(()=>undefined),cache={match:never,put:never};
    const result=createTransitBrokerClient(service,{cache,now:()=>START}).query('bus',route);
    await vi.advanceTimersByTimeAsync(501);expect(await result).toMatchObject({httpStatus:200});
  });
  it('ends an unresponsive binding before headers even if it ignores AbortSignal',async()=>{
    vi.useFakeTimers();const {service}=binding(()=>new Promise<Response>(()=>undefined));
    const result=expect(createTransitBrokerClient(service,{timeoutMs:20}).query('bus',route)).rejects.toMatchObject({code:'broker_unavailable'});
    await vi.advanceTimersByTimeAsync(21);await result;
  });
});
