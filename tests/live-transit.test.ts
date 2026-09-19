import {describe,it,expect,vi} from 'vitest';
import {createLiveTransitHandler,parseTransitKstTime,type LiveTransitEnv,type LiveTransitOptions,type LiveTransitCache} from '../worker/live-transit';
import {LIVE_TRANSIT_API_PREFIX,liveTransitVehicles,isLiveTransitFresh,type LiveBusRoute,type LiveTransitSnapshot,type LiveTransitTargets} from '../shared/live-transit';

const instant=Date.parse('2026-09-16T16:02:22Z');
const routes=[{id:'sejong-b2',city_code:'12',route_id:'SJB293000077',label:'세종 B2'},
  {id:'daejeon-202',city_code:'25',route_id:'DJB30300052',label:'대전 202'},
  {id:'cheongju-747',city_code:'33010',route_id:'CJB270011200',label:'청주 747'}];
const lines=[{id:'line-1',name:'1호선',subway_id:'1001'}];
const env:LiveTransitEnv={DATA_GO_KR_SERVICE_KEY:'fixture-private-key',SEOUL_SUBWAY_API_KEY:'testseoulkey12345'};
type Row=Record<string,unknown>;
const bus=(extra:Row={}):Row=>({vehicleno:'세종TEST0001',gpslong:'127.289',gpslati:'36.49',nodeid:'SJB0001',nodenm:'정부세종청사',nodeord:3,...extra});
const train=(extra:Row={}):Row=>({subwayId:'1001',trainNo:'0914',statnId:'1001000127',statnNm:'동묘앞',statnTnm:'청량리',updnLine:'0',trainSttus:'1',recptnDt:'2026-09-17 01:01:20',...extra});
const tago=(rows:Row[]=[],extra:Row={})=>({response:{header:{resultCode:'00'},body:{pageNo:1,numOfRows:100,totalCount:rows.length,items:rows.length?{item:rows}:'',...extra}}});
const seoul=(rows:Row[]=[],extra:Row={})=>({errorMessage:{code:'INFO-000',total:rows.length},realtimePositionList:rows,...extra});
function setup(payload:unknown=tago([bus()]),options:LiveTransitOptions={}){
  const fetcher=vi.fn<typeof fetch>(async()=>Response.json(payload));
  const handler=createLiveTransitHandler({fetcher,now:()=>instant,busRoutes:routes,subwayLines:lines,...options});
  const call=async(path='/bus?route=sejong-b2',settings=env,init?:RequestInit)=>{
    const response=await handler(new Request(`https://replay.test${LIVE_TRANSIT_API_PREFIX}${path}`,init),settings);
    if(!response)throw new Error('Expected transit response');
    return {response,snapshot:await response.json() as LiveTransitSnapshot};
  };
  return {fetcher,handler,call};
}
function memoryCache():LiveTransitCache&{keys:string[]}{
  const saved=new Map<string,Response>(),keys:string[]=[];
  return {keys,async match(request){return saved.get(request.url)?.clone();},async put(request,response){keys.push(request.url);saved.set(request.url,response.clone());}};
}
const nationalRoute={...routes[0],id:'tago-12-sjb293000077',label:'세종 B2 · 전국 목록의 오송 방면'};
function routeCatalog(entries:LiveBusRoute[]=[nationalRoute]):LiveTransitOptions['busCatalog']{
  return async()=>({reference:{url:'/data/live-transit/routes/aaaaaaaaaaaaaaaa/manifest.json',sha256:'a'.repeat(64),byte_length:100},
    resolve:async id=>entries.find(route=>route.id===id)??null});
}

describe('live transit request boundary',()=>{
  it('publishes configured target identifiers without claiming an observed connection',async()=>{
    const {call,fetcher}=setup();
    const {snapshot}=await call('/targets');const targets=snapshot as unknown as LiveTransitTargets;
    expect(fetcher).not.toHaveBeenCalled();
    expect(targets.bus_routes).toHaveLength(3);expect(targets.bus_routes[0]).toMatchObject({configured:true,refresh_after_seconds:90});
    expect(targets.subway_lines[0].refresh_after_seconds).toBe(120);expect(targets.continuous_collection).toBe(false);
    expect(targets.global_quota_enforced).toBe(false);expect(JSON.stringify(targets)).not.toContain(env.DATA_GO_KR_SERVICE_KEY!);
  });
  it('returns null for routes owned by another handler',async()=>{
    const {handler}=setup();expect(await handler(new Request('https://replay.test/api/v1/replay'),env)).toBeNull();
  });
  it.each(['/bus','/bus?route=unknown','/bus?route=sejong-b2&route=sejong-b2','/bus?route=sejong-b2&cityCode=12','/bus?route=https://attacker.test','/targets?url=x','/unknown'])('rejects unapproved selector %s before fetching',async(path)=>{
    const {call,fetcher}=setup();const {response,snapshot}=await call(path);
    expect(response.status).toBe(400);expect(snapshot.vehicles).toEqual([]);expect(fetcher).not.toHaveBeenCalled();
  });
  it('rejects mutation methods and missing/sample credentials',async()=>{
    const {call,fetcher}=setup();expect((await call('/bus?route=sejong-b2',env,{method:'POST'})).response.status).toBe(405);
    for(const key of [undefined,'','sample'])expect((await call('/bus?route=sejong-b2',{DATA_GO_KR_SERVICE_KEY:key})).snapshot.error?.code).toBe('not_configured');
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('rejects duplicate upstream targets and unsafe identifiers without reflecting configuration',async()=>{
    for(const busRoutes of [[routes[0],{...routes[0],id:'duplicate'}],[{...routes[0],route_id:'https://attacker.test/key'}]]){
      const {call,fetcher}=setup(tago(),{busRoutes});const {snapshot}=await call();
      expect(snapshot.error?.code).toBe('invalid_configuration');expect(JSON.stringify(snapshot)).not.toContain('attacker');expect(fetcher).not.toHaveBeenCalled();
    }
  });
  it('accepts bounded env allowlists and rejects malformed JSON safely',async()=>{
    const handler=createLiveTransitHandler();const request=new Request(`https://replay.test${LIVE_TRANSIT_API_PREFIX}/targets`);
    const good=await handler(request,{...env,LIVE_TRANSIT_BUS_ROUTES:JSON.stringify(routes)});
    expect((await good!.json() as LiveTransitTargets).bus_routes).toHaveLength(3);
    const bad=await handler(request,{...env,LIVE_TRANSIT_BUS_ROUTES:'private malformed key'});
    expect(bad!.status).toBe(503);expect(await bad!.text()).not.toContain('private malformed key');
  });
});

describe('official bus location normalization',()=>{
  it('retains WGS84 source coordinates, hashes vehicle identity, and keeps unknown observation time null',async()=>{
    const {call,fetcher}=setup();const {response,snapshot}=await call();const vehicle=snapshot.vehicles[0];
    expect(response.status).toBe(200);expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(snapshot.status).toBe('available');expect(snapshot.coverage.complete).toBe(true);expect(snapshot.quota.global_enforced).toBe(false);
    expect(vehicle).toMatchObject({kind:'bus',position:{lon:127.289,lat:36.49,method:'provider-map-matched'},observed_at:null,source_received_at:null,retrieved_at:new Date(instant).toISOString()});
    expect(vehicle.id).toMatch(/^[0-9a-f]{24}$/);expect(JSON.stringify(snapshot)).not.toContain('세종TEST0001');
    const url=new URL(String(fetcher.mock.calls[0][0]));expect(url.origin).toBe('https://apis.data.go.kr');expect(url.searchParams.get('routeId')).toBe(routes[0].route_id);
    expect(fetcher.mock.calls[0][1]?.redirect).toBe('manual');expect(snapshot.expires_at).toBe(new Date(instant+90_000).toISOString());
  });
  it('distinguishes a successful zero-vehicle report from bad coordinates',async()=>{
    expect((await setup(tago()).call()).snapshot.status).toBe('empty');
    const partial=await setup(tago([bus(),bus({vehicleno:'bad',gpslong:0})])).call();
    expect(partial.snapshot.status).toBe('partial');expect(partial.snapshot.counts).toMatchObject({upstream:2,accepted:1,invalid:1});
    const invalid=await setup(tago([bus({gpslati:100})])).call();expect(invalid.snapshot.status).toBe('unavailable');expect(invalid.snapshot.error?.code).toBe('upstream_invalid');
  });
  it('rejects reflected credentials in optional source fields',async()=>{
    const {snapshot}=await setup(tago([bus({nodenm:env.DATA_GO_KR_SERVICE_KEY,nodeid:encodeURIComponent(env.DATA_GO_KR_SERVICE_KEY!)})])).call();
    expect(snapshot.vehicles[0]).toMatchObject({station_name:null,station_id:null});expect(JSON.stringify(snapshot)).not.toContain(env.DATA_GO_KR_SERVICE_KEY!);
  });
  it('validates every page before publishing the combined snapshot',async()=>{
    const rows=Array.from({length:101},(_,i)=>bus({vehicleno:`v${i}`}));
    const fetcher=vi.fn<typeof fetch>(async(input)=>{const page=Number(new URL(String(input)).searchParams.get('pageNo'));return Response.json(tago(rows.slice((page-1)*100,page*100),{pageNo:page,totalCount:101}));});
    const {snapshot}=await setup(undefined,{fetcher}).call();expect(snapshot.vehicles).toHaveLength(101);expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it.each([{totalCount:201},{pageNo:2},{numOfRows:99},{totalCount:2}])('refuses oversized or inconsistent page metadata %j',async(extra)=>{
    const {snapshot}=await setup(tago([bus()],extra)).call();expect(snapshot.vehicles).toEqual([]);expect(snapshot.error?.code).toMatch(/response_limit|upstream_inconsistent/);
  });
  it('refuses changing totals or duplicated vehicles across pages instead of publishing a partial page set',async()=>{
    for(const mode of ['total','duplicate']){
      const rows=Array.from({length:100},(_,i)=>bus({vehicleno:`v${i}`}));
      const fetcher=vi.fn<typeof fetch>(async(input)=>{
        const page=Number(new URL(String(input)).searchParams.get('pageNo'));
        return Response.json(tago(page===1?rows:[bus({vehicleno:'v0'})],{pageNo:page,totalCount:page===2&&mode==='total'?102:101}));
      });
      const {snapshot}=await setup(undefined,{fetcher}).call();expect(snapshot.error?.code).toBe('upstream_inconsistent');expect(snapshot.retrieved_at).toBeNull();expect(snapshot.vehicles).toEqual([]);
    }
  });
});

describe('bounded upstream, error handling, and cache behavior',()=>{
  it.each([401,403,429,500,302])('does not reflect source HTTP %s bodies or follow redirects',async(status)=>{
    const fetcher=vi.fn<typeof fetch>(async()=>new Response(`secret=${env.DATA_GO_KR_SERVICE_KEY}`,{status,headers:{Location:'https://attacker.test/'}}));
    const {snapshot}=await setup(undefined,{fetcher}).call();expect(snapshot.vehicles).toEqual([]);expect(JSON.stringify(snapshot)).not.toContain(env.DATA_GO_KR_SERVICE_KEY!);expect(fetcher).toHaveBeenCalledTimes(1);
    expect(snapshot.error?.code).toBe(status===429?'quota_exceeded':[401,403].includes(status)?'upstream_auth':'upstream_http');
  });
  it('classifies official XML/JSON gateway auth and quota failures without exposing raw messages',async()=>{
    for(const value of ['<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>',JSON.stringify({OpenAPI_ServiceResponse:{cmmMsgHeader:{returnReasonCode:'22',errMsg:env.DATA_GO_KR_SERVICE_KEY}}})]){
      const {snapshot}=await setup(undefined,{fetcher:async()=>new Response(value)}).call();expect(snapshot.error?.code).toMatch(/upstream_auth|quota_exceeded/);expect(JSON.stringify(snapshot)).not.toContain(env.DATA_GO_KR_SERVICE_KEY!);
    }
  });
  it('caps declared and streamed body sizes and rejects malformed UTF8 or JSON',async()=>{
    const factories=[()=>new Response('x',{headers:{'Content-Length':String(300_000)}}),()=>new Response(new Uint8Array(300_000)),()=>new Response(new Uint8Array([255])),()=>new Response('{broken')];
    for(const factory of factories){const {snapshot}=await setup(undefined,{fetcher:async()=>factory()}).call();expect(snapshot.error?.code).toMatch(/response_limit|upstream_invalid/);expect(snapshot.vehicles).toEqual([]);}
  });
  it('times out a stalled response body and cancels the reader',async()=>{
    const cancel=vi.fn();const fetcher=vi.fn<typeof fetch>(async()=>new Response(new ReadableStream({cancel})));
    const {snapshot}=await setup(undefined,{fetcher,timeoutMs:15}).call();expect(snapshot.error?.code).toBe('upstream_timeout');expect(cancel).toHaveBeenCalled();
  });
  it('returns static safe errors when fetch throws a URL containing a secret',async()=>{
    const {snapshot}=await setup(undefined,{fetcher:async()=>{throw new Error(`url https://provider.test?serviceKey=${env.DATA_GO_KR_SERVICE_KEY}`);}}).call();
    expect(snapshot.error?.code).toBe('upstream_http');expect(JSON.stringify(snapshot)).not.toMatch(/provider.test|fixture-private-key|stack/);
  });
  it('deduplicates concurrent queries and keeps original retrieval time on memory/edge hits',async()=>{
    let clock=instant;const cache=memoryCache();const {call,fetcher}=setup(tago([bus()]),{now:()=>clock,cache});
    const [a,b]=await Promise.all([call(),call()]);expect(fetcher).toHaveBeenCalledTimes(1);expect(a.snapshot).toEqual(b.snapshot);
    clock+=1000;const c=await call();expect(c.snapshot.retrieved_at).toBe(a.snapshot.retrieved_at);expect(c.snapshot.served_at).not.toBe(a.snapshot.served_at);
    const second=setup(undefined,{now:()=>clock,cache});const d=await second.call();expect(second.fetcher).not.toHaveBeenCalled();expect(d.snapshot.retrieved_at).toBe(a.snapshot.retrieved_at);
    expect(cache.keys.every(key=>!key.includes(env.DATA_GO_KR_SERVICE_KEY!))).toBe(true);
  });
  it('never falls back to expired vehicles when the next source request fails',async()=>{
    let clock=instant;const fetcher=vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json(tago([bus()]))).mockRejectedValueOnce(new Error('offline'));
    const {call}=setup(undefined,{now:()=>clock,fetcher});expect((await call()).snapshot.vehicles).toHaveLength(1);clock+=91_000;
    const failure=await call();expect(failure.snapshot.vehicles).toEqual([]);expect(failure.snapshot.retrieved_at).toBeNull();
    await call();expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('limits distinct inflight work and does not let one client abort poison another',async()=>{
    const releases:(()=>void)[]=[];const fetcher=vi.fn<typeof fetch>(()=>new Promise(resolve=>releases.push(()=>resolve(Response.json(tago([bus()]))))));
    const {call}=setup(undefined,{fetcher});const controller=new AbortController();const a=call('/bus?route=sejong-b2',env,{signal:controller.signal});const other=call();const b=call('/bus?route=daejeon-202');
    await vi.waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2));const busy=await call('/bus?route=cheongju-747');expect(busy.snapshot.error?.code).toBe('busy');controller.abort();releases.forEach(release=>release());
    expect((await a).snapshot.error?.code).toBe('aborted');expect((await other).snapshot.vehicles).toHaveLength(1);expect((await b).snapshot.vehicles).toHaveLength(1);
  });
  it('guards the isolate quota and resets it at KST midnight while declaring no global guarantee',async()=>{
    let clock=instant;const {call,fetcher}=setup(tago(),{dailyLimits:{bus:2},now:()=>clock});await call();
    const exhausted=await call('/bus?route=daejeon-202');expect(exhausted.snapshot.error?.code).toBe('quota_exceeded');expect(exhausted.snapshot.quota.global_enforced).toBe(false);expect(fetcher).toHaveBeenCalledTimes(1);
    clock=Date.parse('2026-09-17T15:00:01Z');expect((await call('/bus?route=daejeon-202')).snapshot.status).toBe('empty');expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('backs off across targets after the provider rejects its quota',async()=>{
    const fetcher=vi.fn<typeof fetch>(async()=>new Response('',{status:429}));const {call}=setup(undefined,{fetcher});
    expect((await call()).snapshot.error?.code).toBe('quota_exceeded');expect((await call('/bus?route=daejeon-202')).snapshot.error?.code).toBe('quota_exceeded');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe('official bus route aliases share source work without mixing response identities',()=>{
  it('shares the memory result between recommended and national selectors while preserving each response label',async()=>{
    const {call,fetcher}=setup(tago([bus()]),{busCatalog:routeCatalog()});
    const recommended=(await call()).snapshot,national=(await call(`/bus?route=${nationalRoute.id}`)).snapshot;
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(recommended.target).toEqual({id:routes[0].id,label:routes[0].label});
    expect(national.target).toEqual({id:nationalRoute.id,label:nationalRoute.label});
    expect(national.vehicles[0]).toMatchObject({id:recommended.vehicles[0].id,label:nationalRoute.label,city_code:routes[0].city_code,route_id:routes[0].route_id});
    expect((await call()).snapshot.vehicles[0].label).toBe(routes[0].label);
    expect(national.retrieved_at).toBe(recommended.retrieved_at);
  });
  it('shares inflight work between aliases without using a third source slot or propagating one client abort',async()=>{
    const releases:(()=>void)[]=[];
    const fetcher=vi.fn<typeof fetch>(()=>new Promise(resolve=>releases.push(()=>resolve(Response.json(tago([bus()]))))));
    const {call}=setup(undefined,{fetcher,busCatalog:routeCatalog()}),controller=new AbortController();
    const recommended=call('/bus?route=sejong-b2',env,{signal:controller.signal}),national=call(`/bus?route=${nationalRoute.id}`),other=call('/bus?route=daejeon-202');
    await vi.waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(2));
    const busy=await call('/bus?route=cheongju-747');expect(busy.snapshot.error?.code).toBe('busy');
    controller.abort();releases.forEach(release=>release());
    expect((await recommended).snapshot.error?.code).toBe('aborted');
    expect((await national).snapshot).toMatchObject({target:{id:nationalRoute.id,label:nationalRoute.label},status:'available'});
    expect((await other).snapshot.target.id).toBe('daejeon-202');expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('reuses a national selector edge result from another isolate for a recommended selector',async()=>{
    const cache=memoryCache(),first=setup(tago([bus()]),{cache,busCatalog:routeCatalog()});
    const national=(await first.call(`/bus?route=${nationalRoute.id}`)).snapshot;
    const second=setup(undefined,{cache,busCatalog:routeCatalog(),now:()=>instant+1000});
    const recommended=(await second.call()).snapshot;expect(second.fetcher).not.toHaveBeenCalled();
    expect(recommended.target).toEqual({id:routes[0].id,label:routes[0].label});
    expect(recommended.vehicles[0]).toMatchObject({id:national.vehicles[0].id,label:routes[0].label,route_id:routes[0].route_id,city_code:routes[0].city_code});
    expect(recommended.retrieved_at).toBe(national.retrieved_at);expect(recommended.served_at).not.toBe(national.served_at);
    expect(cache.keys).toHaveLength(1);
    expect(cache.keys[0]).not.toMatch(/sejong-b2|tago-12-sjb293000077|fixture-private-key/);
    expect((await first.call(`/bus?route=${nationalRoute.id}`)).snapshot.vehicles[0].label).toBe(nationalRoute.label);
  });
  it.each([
    {...nationalRoute,city_code:'25',id:'tago-25-sjb293000077'},
    {...nationalRoute,route_id:'SJB293000078',id:'tago-12-sjb293000078'},
    {...nationalRoute,route_id:'sjb293000077'},
  ])('keeps different exact provider identities separate even with the same display label ($city_code/$route_id)',async distinct=>{
    const {call,fetcher}=setup(tago([bus()]),{busCatalog:routeCatalog([{...distinct,label:routes[0].label}])});
    const recommended=(await call()).snapshot,national=(await call(`/bus?route=${distinct.id}`)).snapshot;
    expect(fetcher).toHaveBeenCalledTimes(2);expect(national.vehicles[0].id).not.toBe(recommended.vehicles[0].id);
    expect(national.vehicles[0]).toMatchObject({city_code:distinct.city_code,route_id:distinct.route_id});
    const queried=new URL(String(fetcher.mock.calls[1][0]));expect(queried.searchParams.get('cityCode')).toBe(distinct.city_code);expect(queried.searchParams.get('routeId')).toBe(distinct.route_id);
  });
  it('uses the longer alias TTL when a single recommended route otherwise refreshes every 60 seconds',async()=>{
    let clock=instant;const {call,fetcher}=setup(tago([bus()]),{busRoutes:[routes[0]],busCatalog:routeCatalog(),now:()=>clock});
    const targets=(await call('/targets')).snapshot as unknown as LiveTransitTargets;expect(targets.bus_routes[0].refresh_after_seconds).toBe(90);
    const recommended=(await call()).snapshot;expect(recommended.refresh_after_seconds).toBe(90);
    clock+=61_000;const national=(await call(`/bus?route=${nationalRoute.id}`)).snapshot;
    expect(national.expires_at).toBe(recommended.expires_at);expect(national.refresh_after_seconds).toBe(90);expect(fetcher).toHaveBeenCalledTimes(1);
    clock+=30_000;await call();expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('preserves a stricter configured TTL and source quota across alias switches',async()=>{
    let clock=instant;const {call,fetcher}=setup(tago([bus()]),{busCatalog:routeCatalog(),dailyLimits:{bus:2},now:()=>clock});
    const recommended=(await call()).snapshot;expect(recommended.refresh_after_seconds).toBeGreaterThan(90);
    clock+=91_000;const national=(await call(`/bus?route=${nationalRoute.id}`)).snapshot;
    expect(national.refresh_after_seconds).toBe(recommended.refresh_after_seconds);expect(national.expires_at).toBe(recommended.expires_at);
    expect((await call('/bus?route=daejeon-202')).snapshot.error?.code).toBe('quota_exceeded');expect(fetcher).toHaveBeenCalledTimes(1);
    expect(national.quota).toMatchObject({daily_limit:2,local_budget:1,global_enforced:false});
  });
  it('shares authentication failure backoff while returning the requested target',async()=>{
    const fetcher=vi.fn<typeof fetch>(async()=>new Response('private upstream response',{status:403}));
    const {call}=setup(undefined,{fetcher,busCatalog:routeCatalog()});
    const national=await call(`/bus?route=${nationalRoute.id}`),recommended=await call();
    expect(fetcher).toHaveBeenCalledTimes(1);expect(national.response.status).toBe(503);expect(recommended.response.status).toBe(503);
    expect(recommended.snapshot).toMatchObject({target:{id:routes[0].id,label:routes[0].label},vehicles:[],error:{code:'upstream_auth'},refresh_after_seconds:300});
    expect(national.snapshot.target.id).toBe(nationalRoute.id);expect(JSON.stringify(recommended.snapshot)).not.toContain('private upstream');
  });
  it('keeps credential and quota-policy cache scopes isolated without exposing a source key',async()=>{
    const cache=memoryCache(),first=setup(tago([bus()]),{cache,busCatalog:routeCatalog()});await first.call();
    await first.call(`/bus?route=${nationalRoute.id}`,{...env,DATA_GO_KR_SERVICE_KEY:'fixture-other-private-key'});expect(first.fetcher).toHaveBeenCalledTimes(2);
    // 7,200 and 8,000 local budgets both yield a 90-second interval here; the
    // stronger quota must not inherit the weaker policy through a cache hit.
    const stricter=setup(tago([bus()]),{cache,busCatalog:routeCatalog(),dailyLimits:{bus:9000}});
    expect((await stricter.call()).snapshot.quota.daily_limit).toBe(9000);expect(stricter.fetcher).toHaveBeenCalledTimes(1);
    expect(cache.keys).toHaveLength(3);expect(cache.keys.every(key=>!key.includes('private-key'))).toBe(true);
  });
  it.each(['identity','vehicle-route'])('rejects an edge entry with mismatched %s instead of relabeling its vehicles',async mismatch=>{
    const cache=memoryCache(),first=setup(tago([bus()]),{cache,busCatalog:routeCatalog()});await first.call();
    const modified:LiveTransitCache={put:cache.put,async match(request){
      const hit=await cache.match(request);if(!hit)return undefined;
      const saved=await hit.json() as {identity:string;snapshot:LiveTransitSnapshot};
      if(mismatch==='identity')saved.identity='different-official-route';
      else if(saved.snapshot.vehicles[0].kind==='bus')saved.snapshot.vehicles[0].route_id='different-official-route';
      return Response.json(saved);
    }};
    const second=setup(tago([bus()]),{cache:modified,busCatalog:routeCatalog()});
    expect((await second.call(`/bus?route=${nationalRoute.id}`)).snapshot.vehicles[0]).toMatchObject({route_id:routes[0].route_id,label:nationalRoute.label});
    expect(second.fetcher).toHaveBeenCalledTimes(1);
  });
});

describe('Seoul station reports remain distinct from GPS',()=>{
  it('emits provider reception time and unresolved station identity with no coordinates',async()=>{
    const {snapshot}=await setup(seoul([train()])).call('/subway?line=line-1');expect(snapshot.status).toBe('available');
    expect(snapshot.vehicles[0]).toMatchObject({kind:'subway',observed_at:null,source_received_at:'2026-09-16T16:01:20.000Z',position:null,station_mapping:'unresolved',reported_status:'arrived'});
  });
  it('rejects invalid calendar dates, future reports, other lines, and old source reports',async()=>{
    const {snapshot}=await setup(seoul([train(),train({recptnDt:'2026-02-30 01:01:20'}),train({recptnDt:'2026-09-17 01:09:20'}),train({subwayId:'1002'}),train({recptnDt:'2026-09-17 00:50:20'})])).call('/subway?line=line-1');
    expect(snapshot.status).toBe('partial');expect(snapshot.counts).toMatchObject({upstream:5,accepted:1,invalid:3,stale:1});
    expect(parseTransitKstTime('2026-02-30 01:01:20')).toBeNull();expect(parseTransitKstTime('2026-09-17T01:01:20')).toBeNull();
  });
  it('keeps the latest duplicate train report and suppresses equal-time conflicting reports',async()=>{
    const {snapshot}=await setup(seoul([train({recptnDt:'2026-09-17 01:00:10'}),train(),train({trainNo:'0907'}),train({trainNo:'0907',statnId:'1001000130'})])).call('/subway?line=line-1');
    expect(snapshot.vehicles).toHaveLength(1);expect(snapshot.vehicles[0]).toMatchObject({train_no:'0914',source_received_at:'2026-09-16T16:01:20.000Z'});
    expect(snapshot.counts).toMatchObject({duplicate:2,ambiguous:1});expect(snapshot.coverage.complete).toBe(false);
  });
  it('accepts an official empty response but refuses truncated result totals',async()=>{
    const empty=await setup({errorMessage:{code:'INFO-200',total:0}}).call('/subway?line=line-1');expect(empty.snapshot.status).toBe('empty');
    const truncated=await setup(seoul([train()],{errorMessage:{code:'INFO-000',total:2}})).call('/subway?line=line-1');expect(truncated.snapshot.error?.code).toBe('upstream_inconsistent');
  });
  it('expires stale source reports even within a still-valid response cache',async()=>{
    let clock=instant;const {call,fetcher}=setup(seoul([train({recptnDt:'2026-09-17 00:57:30'})]),{now:()=>clock});
    expect((await call('/subway?line=line-1')).snapshot.vehicles).toHaveLength(1);clock+=10_000;
    const expired=await call('/subway?line=line-1');expect(expired.snapshot.vehicles).toEqual([]);expect(expired.snapshot.status).toBe('stale');expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('uses HTTPS by default and requires an explicit deployment setting for HTTP',async()=>{
    const {call,fetcher}=setup(seoul([train()]));await call('/subway?line=line-1');expect(String(fetcher.mock.calls[0][0])).toMatch(/^https:/);
    await call('/subway?line=line-1',{...env,SEOUL_SUBWAY_ALLOW_HTTP:'true'});expect(String(fetcher.mock.calls[1][0])).toMatch(/^http:/);
  });
  it('client display helpers reject expired snapshots and preserve successful empty states',async()=>{
    const {snapshot}=await setup().call();expect(liveTransitVehicles(snapshot,instant)).toHaveLength(1);expect(isLiveTransitFresh(snapshot,instant)).toBe(true);
    expect(liveTransitVehicles(snapshot,instant+90_000)).toEqual([]);expect(isLiveTransitFresh(snapshot,NaN)).toBe(false);
    const empty=(await setup(tago()).call()).snapshot;expect(isLiveTransitFresh(empty,instant)).toBe(true);expect(liveTransitVehicles(empty,instant)).toEqual([]);
  });
});
