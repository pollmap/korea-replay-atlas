import {afterEach,describe,expect,it,vi} from 'vitest';
import {currentPanelFrame,decodeLiveTargets,decodeLiveTransit,decodeLiveWeather,startLivePoll,type LiveResource,type VisibilitySource} from '../src/live-client';
import type {LiveTransitSnapshot} from '../shared/live-transit';
import type {LiveWeatherManifest} from '../shared/live-weather';

const now=Date.parse('2026-09-17T02:00:00Z');
class Visibility implements VisibilitySource {
  hidden=false;listeners=new Set<()=>void>();
  addEventListener(_type:'visibilitychange',listener:()=>void){this.listeners.add(listener);}
  removeEventListener(_type:'visibilitychange',listener:()=>void){this.listeners.delete(listener);}
  set(hidden:boolean){this.hidden=hidden;for(const listener of this.listeners)listener();}
}
const snapshot=():LiveTransitSnapshot=>({schema_version:1,mode:'live',kind:'bus',status:'available',target:{id:'route',label:'노선'},retrieved_at:new Date(now).toISOString(),served_at:new Date(now).toISOString(),expires_at:new Date(now+90000).toISOString(),refresh_after_seconds:90,max_source_age_seconds:null,
  source:{id:'tago',page_url:'https://www.data.go.kr/data/15098531/openapi.do',license:'출처표시',access:'official-key'},coverage:{scope:'selected-route',complete:true},counts:{upstream:1,accepted:1,invalid:0,stale:0,duplicate:0,ambiguous:0},quota:{daily_limit:1000,local_budget:100,guard:'isolate-and-regional-cache',global_enforced:false},
  vehicles:[{kind:'bus',id:'bus-1',label:'버스',observed_at:null,retrieved_at:new Date(now).toISOString(),source_received_at:null,route_id:'route',city_code:'25',position:{lon:127.4,lat:36.3,crs:'EPSG:4326',method:'provider-map-matched'},station_id:null,station_name:null,station_order:null}]});
const weather=(kind:'radar'|'satellite'='radar'):LiveWeatherManifest=>({schema_version:1,mode:'live',kind,status:'available',checked_at:new Date(now).toISOString(),served_at:new Date(now).toISOString(),latest_observed_at:new Date(now).toISOString(),max_age_seconds:1200,refresh_after_seconds:60,frames:[],presentation:'panel-image',
  source:{name:'기상청',page_url:'https://www.weather.go.kr',copyright_url:'https://www.weather.go.kr',access:'verified',permission_reference:'fixture'},interpretation:{representation:'official-color',numeric_inversion:false,background_meaning:'source-rendering-not-classified'},
  panel_frames:[kind==='radar'?{kind:'radar',product:'CMP_WRC',representation:'panel-image',map_overlay:false,projection:null,time:new Date(now).toISOString(),source_time_kst:'202609171100',url:'/api/v1/live/weather/radar/cmp-wrc/frames/202609171100.png',source_url:'https://www.weather.go.kr',image_size:[635,620]}:
    {kind:'satellite',product:'GK2A_IR105_KO',representation:'panel-image',map_overlay:false,projection:null,time:new Date(now).toISOString(),source_time_kst:'202609171100',source_time_utc:'202609170200',url:'/api/v1/live/weather/satellite/gk2a-ir105-ko/frames/202609170200.png',source_url:'https://www.weather.go.kr',image_size:null}]});
afterEach(()=>vi.useRealTimers());

describe('current observation client boundaries',()=>{
  it('accepts the approved nationwide route reference and rejects malformed configured flags',()=>{
    const targets={schema_version:1,mode:'live',bus_routes:[],subway_lines:[],continuous_collection:false,global_quota_enforced:false,
      bus_catalog:{url:'/data/live-transit/routes/0123456789abcdef/manifest.json',sha256:'a'.repeat(64),byte_length:100,configured:true}};
    expect(decodeLiveTargets(targets).bus_catalog?.configured).toBe(true);
    expect(()=>decodeLiveTargets({...targets,bus_catalog:{...targets.bus_catalog,configured:'true'}})).toThrow();
  });
  it('preserves source coordinates and unknown measurement time, rejecting invalid map positions',()=>{
    const data=snapshot();expect(decodeLiveTransit(data)).toBe(data);
    expect(()=>decodeLiveTransit({...data,vehicles:[{...data.vehicles[0],position:{lon:0,lat:0,crs:'EPSG:4326',method:'provider-map-matched'}}]})).toThrow();
    expect(()=>decodeLiveTransit({...data,vehicles:[{...data.vehicles[0],observed_at:data.retrieved_at}]})).toThrow();
  });
  it('handles radar and satellite panel contracts without inventing satellite dimensions or a map overlay',()=>{
    for(const kind of ['radar','satellite'] as const){const data=weather(kind);expect(decodeLiveWeather(data)).toBe(data);expect(currentPanelFrame(data,now)).toBe(data.panel_frames![0]);}
    const satellite=weather('satellite');expect(currentPanelFrame(satellite,now)?.image_size).toBeNull();
    expect(()=>decodeLiveWeather({...satellite,panel_frames:[{...satellite.panel_frames![0],map_overlay:true}]})).toThrow();
    expect(()=>decodeLiveWeather({...satellite,panel_frames:[{...satellite.panel_frames![0],url:'https://external.test/secret.png'}]})).toThrow();
  });
  it('does not retain expired, unverified, unavailable, or different-time weather images',()=>{
    const data=weather();expect(currentPanelFrame(data,now+1200000)).toBeNull();
    expect(currentPanelFrame({...data,status:'unavailable'},now)).toBeNull();
    expect(currentPanelFrame({...data,presentation:'none'},now)).toBeNull();
    expect(currentPanelFrame({...data,latest_observed_at:new Date(now-60000).toISOString()},now)).toBeNull();
  });
});

describe('current observation polling lifecycle',()=>{
  it('expires the visible state at its deadline without waiting for the next network refresh',async()=>{
    vi.useFakeTimers();vi.setSystemTime(now);const visibility=new Visibility(),changes:LiveResource<{expires:number}>[]=[];
    const fetcher=vi.fn<typeof fetch>(async()=>Response.json({expires:now+5000}));
    const poll=startLivePoll({url:'/api/v1/live/transit/bus?route=test',decode:value=>value as {expires:number},refreshSeconds:()=>90,expiresAt:value=>value.expires,onChange:value=>changes.push(value),fetcher,visibility});
    await vi.advanceTimersByTimeAsync(0);expect(changes.at(-1)?.expired).toBe(false);
    await vi.advanceTimersByTimeAsync(5000);expect(changes.at(-1)?.expired).toBe(true);expect(fetcher).toHaveBeenCalledTimes(1);poll.stop();
  });
  it('uses the next empty observation instead of preserving previous vehicles',async()=>{
    vi.useFakeTimers();vi.setSystemTime(now);const visibility=new Visibility(),changes:LiveResource<LiveTransitSnapshot>[]=[];
    const first=snapshot(),empty={...first,status:'empty',vehicles:[]};let calls=0;
    const poll=startLivePoll({url:'/api/v1/live/transit/bus?route=test',decode:decodeLiveTransit,refreshSeconds:()=>90,onChange:value=>changes.push(value),fetcher:async()=>Response.json(calls++?empty:first),visibility});
    await vi.advanceTimersByTimeAsync(0);expect(changes.at(-1)?.data?.vehicles).toHaveLength(1);
    poll.refresh();await vi.advanceTimersByTimeAsync(0);expect(changes.at(-1)?.data?.vehicles).toHaveLength(0);expect(changes.at(-1)?.data?.status).toBe('empty');poll.stop();
  });
  it('aborts hidden requests and ignores their delayed results after a new visible request begins',async()=>{
    vi.useFakeTimers();const visibility=new Visibility(),changes:LiveResource<number>[]=[];
    const calls:{signal:AbortSignal|null|undefined;resolve:(response:Response)=>void}[]=[];
    const fetcher:typeof fetch=(_input,init)=>new Promise(resolve=>calls.push({signal:init?.signal,resolve}));
    const poll=startLivePoll({url:'/api/v1/live/weather/radar',decode:value=>value as number,refreshSeconds:()=>60,onChange:value=>changes.push(value),fetcher,visibility});
    visibility.set(true);expect(calls[0].signal?.aborted).toBe(true);expect(changes.at(-1)?.paused).toBe(true);
    visibility.set(false);expect(calls).toHaveLength(2);
    calls[1].resolve(Response.json(2));await vi.advanceTimersByTimeAsync(0);expect(changes.at(-1)?.data).toBe(2);
    calls[0].resolve(Response.json(1));await vi.advanceTimersByTimeAsync(0);expect(changes.at(-1)?.data).toBe(2);poll.stop();expect(visibility.listeners.size).toBe(0);
  });
  it('keeps only one request active and cancels all callbacks when the panel unmounts',async()=>{
    vi.useFakeTimers();const visibility=new Visibility(),changes:LiveResource<number>[]=[];let done!:(response:Response)=>void,signal:AbortSignal|null|undefined;
    const fetcher=vi.fn<typeof fetch>((_input,init)=>{signal=init?.signal;return new Promise(resolve=>{done=resolve;});});
    const poll=startLivePoll({url:'/api/v1/live/weather/radar',decode:value=>value as number,refreshSeconds:()=>60,onChange:value=>changes.push(value),fetcher,visibility});
    poll.refresh();poll.refresh();expect(fetcher).toHaveBeenCalledTimes(1);const count=changes.length;
    poll.stop();expect(signal?.aborted).toBe(true);done(Response.json(1));await vi.advanceTimersByTimeAsync(200000);expect(changes).toHaveLength(count);expect(visibility.listeners.size).toBe(0);
  });
  it('clears previous observations when a refresh fails and does not disclose upstream errors',async()=>{
    vi.useFakeTimers();const visibility=new Visibility(),changes:LiveResource<number>[]=[];let calls=0;
    const poll=startLivePoll({url:'/api/v1/live/weather/radar',decode:value=>value as number,refreshSeconds:()=>60,onChange:value=>changes.push(value),fetcher:async()=>{if(calls++)throw new Error('https://upstream.test/?key=private');return Response.json(1);},visibility});
    await vi.advanceTimersByTimeAsync(0);poll.refresh();await vi.advanceTimersByTimeAsync(0);expect(changes.at(-1)?.data).toBeNull();expect(changes.at(-1)?.error).not.toContain('private');poll.stop();
  });
});
