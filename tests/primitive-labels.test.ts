import {afterAll,afterEach,beforeAll,beforeEach,describe,expect,it,vi} from 'vitest';
import * as C from 'cesium';
import {buildPrimitives,PrimitivePickRegistry} from '../src/primitive-renderer';
import {SceneLabelScheduler} from '../src/label-scheduler';
import {compileGeometry,type GeoCollection} from '../shared/geometry';
import {FrameWorkBudget} from '../shared/map-performance';
import type {Asset} from '../shared/contracts';

const asset:Asset={id:'label-tile',layer:'infrastructure',format:'geojson',url:'/data/label-tile.geojson',bbox:[126,35,128,37],source_id:'osm',version:'source-version',count:3,sha256:'source-hash'};
const feature=(id:string):GeoCollection['features'][number]=>({type:'Feature',id,properties:{kind:'station',name:id,source_record_id:`node/${id}`},geometry:{type:'Point',coordinates:[127,36,7]}});
const tick=()=>new Promise<void>(resolve=>setImmediate(resolve));
// Test-only WebGL context limits, normally initialized by a real Viewer.
const limits=(C as unknown as {ContextLimits:{_minimumAliasedLineWidth:number;_maximumAliasedLineWidth:number}}).ContextLimits;
const previousLimits={...limits};
beforeAll(()=>{limits._minimumAliasedLineWidth=1;limits._maximumAliasedLineWidth=1;});
afterAll(()=>Object.assign(limits,previousLimits));
function fixture(){
  let height=20;
  const scene={primitives:new C.PrimitiveCollection(),preRender:new C.Event(),postRender:new C.Event(),requestRender:vi.fn(),isDestroyed:()=>false,frameState:{mode:C.SceneMode.SCENE3D},globe:{getHeight:()=>height}} as unknown as C.Scene;
  return {scene,viewer:{scene,isDestroyed:()=>false} as C.Viewer,scheduler:new SceneLabelScheduler(scene),frame:()=>{scene.preRender.raiseEvent();scene.postRender.raiseEvent();},setHeight:(value:number)=>{height=value;}};
}
beforeEach(()=>{
  const css:Record<string,string>={'line-height':'normal','font-family':'sans-serif','font-size':'12px','font-style':'normal','font-weight':'normal'};
  vi.stubGlobal('document',{createElement:()=>({style:{}}),body:{appendChild:()=>{},removeChild:()=>{}},defaultView:{getComputedStyle:()=>({getPropertyValue:(name:string)=>css[name]??''})}});
});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});

describe('source bundles with shared deferred labels',()=>{
  it('finishes geometry independently, preserves every source ID, and shares label admission across bundles',async()=>{
    const {scene,viewer,scheduler,frame}=fixture(),picks=new PrimitivePickRegistry();
    const names=['서울','대전','청주'],source={type:'FeatureCollection' as const,features:names.map(feature)},data=compileGeometry(source),coordinates=Array.from(data.coordinates);
    const options={signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:3,pickMap:picks,labelScheduler:scheduler};
    const bundles=await Promise.all(['a','b'].map(id=>buildPrimitives(data,{...asset,id},viewer,options)));
    expect(picks.size).toBe(6);expect(scheduler.snapshot()).toMatchObject({owned:6,queued:6,materialized:0});
    for(const bundle of bundles){expect(bundle.root.length).toBe(1);expect(bundle.featureCount).toBe(3);expect(bundle.vertexCount).toBe(3);expect(bundle.pointCount).toBe(3);}
    expect(Array.from(data.coordinates)).toEqual(coordinates);expect(data.features.map(item=>item.id)).toEqual(names);
    frame();const labels=scene.primitives.get(0) as C.LabelCollection;expect(labels.length).toBe(2);
    frame();frame();expect(scene.primitives.length).toBe(1);expect(labels.length).toBe(6);
    for(let i=0;i<labels.length;i++){
      const label=labels.get(i),pick=picks.get(String(label.id));
      expect(pick?.sourceId).toBe(label.text);expect(pick?.properties.source_record_id).toBe(`node/${label.text}`);
      expect(pick?.asset.version).toBe('source-version');expect(label.font).toBe('12px sans-serif');
      expect(C.Cartographic.fromCartesian(label.position).height).toBeCloseTo(20,5);
    }
    for(const bundle of bundles)bundle.destroy();expect(picks.size).toBe(0);expect(scheduler.snapshot()).toMatchObject({owned:0,queued:0,materialized:0});
    scheduler.destroy();scene.primitives.destroy();
  });
  it('keeps dormant labels out of the queue, restores their original handles, and applies the latest ground height',async()=>{
    const {scene,viewer,scheduler,frame,setHeight}=fixture(),picks=new PrimitivePickRegistry();
    const data=compileGeometry({type:'FeatureCollection',features:['첫역','둘역','셋역'].map(feature)});
    const bundle=await buildPrimitives(data,asset,viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:3,pickMap:picks,labelScheduler:scheduler,groundRevision:1});
    bundle.setActive(false);expect(scheduler.snapshot().queued).toBe(0);frame();expect(scene.primitives.length).toBe(0);expect(picks.size).toBe(0);
    bundle.setActive(true,1);setHeight(55);bundle.refreshGround(2);await tick();
    frame();const labels=scene.primitives.get(0) as C.LabelCollection;expect(labels.length).toBe(1);const original=labels.get(0),id=original.id;
    expect(original.text).toBe('첫역');expect(C.Cartographic.fromCartesian(original.position).height).toBeCloseTo(55,5);expect(picks.get(String(id))?.sourceId).toBe('첫역');
    bundle.setActive(false);expect(original.show).toBe(false);expect(scheduler.snapshot().queued).toBe(0);
    bundle.setActive(true,3);frame();expect(labels.length).toBe(3);expect(labels.get(0)).toBe(original);expect(original.id).toBe(id);expect(original.show).toBe(true);
    bundle.setActive(true,1);expect(labels.get(1).show).toBe(false);expect(labels.get(2).show).toBe(false);
    bundle.setActive(true,3);frame();expect(scheduler.snapshot().created).toBe(3);
    for(let i=0;i<labels.length;i++)expect(C.Cartographic.fromCartesian(labels.get(i).position).height).toBeCloseTo(55,5);
    expect(picks.size).toBe(3);bundle.destroy();scheduler.destroy();scene.primitives.destroy();
  });
  it('releases hidden pending labels and picks immediately when a staged build is cancelled before the next frame',async()=>{
    const {scene,viewer,scheduler,frame}=fixture(),picks=new PrimitivePickRegistry(),controller=new AbortController();
    const roads:GeoCollection['features']=Array.from({length:128},(_,i)=>({type:'Feature',id:`road-${i}`,properties:{kind:'road'},geometry:{type:'LineString',coordinates:[[127,36],[127.001,36.001]]}}));
    const data=compileGeometry({type:'FeatureCollection',features:[feature('취소할역'),...roads]});
    let resolveStage!:()=>void;const staged=new Promise<void>(resolve=>{resolveStage=resolve;});
    const building=buildPrimitives(data,{...asset,count:data.features.length,vertex_count:data.vertexCount},viewer,{signal:controller.signal,budget:new FrameWorkBudget(()=>0,()=>new Promise<void>(()=>{})),labelLimit:1,pickMap:picks,labelScheduler:scheduler,stageRoot:root=>{scene.primitives.add(root);resolveStage();return()=>{scene.primitives.remove(root);};}});
    await Promise.race([staged,building]);expect(scheduler.snapshot()).toMatchObject({owned:1,queued:0,created:0});
    const rejected=expect(building).rejects.toMatchObject({name:'AbortError'});controller.abort();await rejected;
    expect(picks.size).toBe(0);expect(scene.primitives.length).toBe(0);expect(scheduler.snapshot()).toMatchObject({owned:0,queued:0,created:0});
    frame();expect(scene.primitives.length).toBe(0);scheduler.destroy();scene.primitives.destroy();
  });
  it('drops queued labels when a completed bundle is removed before its first label frame',async()=>{
    const {scene,viewer,scheduler,frame}=fixture(),picks=new PrimitivePickRegistry();
    const data=compileGeometry({type:'FeatureCollection',features:[feature('원문역')]});
    const bundle=await buildPrimitives(data,{...asset,count:1},viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:1,pickMap:picks,labelScheduler:scheduler});
    scene.primitives.add(bundle.root);expect(scheduler.snapshot()).toMatchObject({queued:1,owned:1,created:0});
    scene.primitives.remove(bundle.root);expect(bundle.root.isDestroyed()).toBe(true);
    bundle.destroy();expect(scheduler.snapshot()).toMatchObject({queued:0,owned:0,preparing:false});expect(picks.size).toBe(0);
    frame();expect(scene.primitives.length).toBe(0);scheduler.destroy();scene.primitives.destroy();
  });
});
