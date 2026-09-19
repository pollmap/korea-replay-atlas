import {afterEach,describe,expect,it,vi} from 'vitest';
import * as C from 'cesium';
import {createLiveBusLayer} from '../src/live-map';
import {FrameWorkBudget} from '../shared/map-performance';
import type {LiveTransitSnapshot} from '../shared/live-transit';

const instant=Date.parse('2026-09-17T02:00:00Z');
function snapshot():LiveTransitSnapshot{return {schema_version:1,mode:'live',kind:'bus',status:'available',target:{id:'route',label:'노선'},retrieved_at:new Date(instant).toISOString(),served_at:new Date(instant).toISOString(),expires_at:new Date(instant+90000).toISOString(),refresh_after_seconds:90,max_source_age_seconds:null,
  source:{id:'tago',page_url:'https://www.data.go.kr',license:'출처표시',access:'official-key'},coverage:{scope:'selected-route',complete:true},counts:{upstream:1,accepted:1,invalid:0,stale:0,duplicate:0,ambiguous:0},quota:{daily_limit:1000,local_budget:100,guard:'isolate-and-regional-cache',global_enforced:false},
  vehicles:[{kind:'bus',id:'source-id',label:'버스',observed_at:null,retrieved_at:new Date(instant).toISOString(),source_received_at:null,route_id:'route',city_code:'25',position:{lon:127.4,lat:36.3,crs:'EPSG:4326',method:'provider-map-matched'},station_id:null,station_name:null,station_order:null}]};}
afterEach(()=>vi.useRealTimers());
describe('current bus point lifecycle',()=>{
  it('keeps source coordinates without interpolation and samples deferred terrain at the same revision after stopping',async()=>{
    vi.useFakeTimers();vi.setSystemTime(instant);let paused=true;const data=snapshot(),original=structuredClone(data),getHeight=vi.fn(()=>120);
    const viewer={scene:{globe:{getHeight},requestRender:vi.fn()},isDestroyed:()=>false} as unknown as C.Viewer;
    const layer=createLiveBusLayer(viewer,data,{budget:new FrameWorkBudget(()=>1,async()=>{}),groundState:()=>({paused,revision:1})});
    await layer.ready;expect(layer.root.length).toBe(1);expect(getHeight).not.toHaveBeenCalled();
    const initial=C.Cartographic.fromCartesian(layer.root.get(0).position);expect(initial.height).toBeCloseTo(0,5);expect(C.Math.toDegrees(initial.longitude)).toBeCloseTo(127.4,8);expect(C.Math.toDegrees(initial.latitude)).toBeCloseTo(36.3,8);
    paused=false;layer.setGroundRefreshPaused(false);layer.refreshGround(1);await vi.advanceTimersByTimeAsync(0);
    expect(getHeight).toHaveBeenCalledTimes(1);expect(C.Cartographic.fromCartesian(layer.root.get(0).position).height).toBeCloseTo(122,5);
    layer.refreshGround(1);await vi.advanceTimersByTimeAsync(0);expect(getHeight).toHaveBeenCalledTimes(1);
    expect(data).toEqual(original);expect([...layer.picks.values()][0]).toMatchObject({rawHeight:null,provenance:{observed_at:null,source_record_id:'source-id'}});layer.destroy();
  });
  it('removes points and selections exactly at expiry without needing another response',async()=>{
    vi.useFakeTimers();vi.setSystemTime(instant);const onCount=vi.fn(),viewer={scene:{globe:{getHeight:()=>0},requestRender:vi.fn()},isDestroyed:()=>false} as unknown as C.Viewer;
    const layer=createLiveBusLayer(viewer,snapshot(),{budget:new FrameWorkBudget(()=>1,async()=>{}),groundState:()=>({paused:true,revision:0}),onCount});
    await layer.ready;expect(layer.root.length).toBe(1);expect(layer.picks.size).toBe(1);
    await vi.advanceTimersByTimeAsync(90000);expect(layer.root.length).toBe(0);expect(layer.picks.size).toBe(0);expect(onCount).toHaveBeenLastCalledWith(0);layer.destroy();
  });
  it('builds no old vehicles for an empty response and can be destroyed before deferred construction starts',async()=>{
    vi.useFakeTimers();vi.setSystemTime(instant);const viewer={scene:{globe:{getHeight:vi.fn()},requestRender:vi.fn()},isDestroyed:()=>false} as unknown as C.Viewer;
    const empty=createLiveBusLayer(viewer,{...snapshot(),status:'empty',vehicles:[]},{budget:new FrameWorkBudget(()=>1,async()=>{}),groundState:()=>({paused:true,revision:0})});await empty.ready;expect(empty.root.length).toBe(0);empty.destroy();
    let resolve!:()=>void;const stopped=createLiveBusLayer(viewer,snapshot(),{budget:new FrameWorkBudget(()=>1,()=>new Promise<void>(done=>{resolve=done;})),groundState:()=>({paused:true,revision:0})});
    stopped.destroy();resolve();await stopped.ready;expect(stopped.root.isDestroyed()).toBe(true);expect(stopped.picks.size).toBe(0);expect(viewer.scene.globe.getHeight).not.toHaveBeenCalled();
  });
});
