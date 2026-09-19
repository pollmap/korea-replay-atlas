import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import * as C from 'cesium';
import {LABEL_GENERATION_CODE_POINTS,SceneLabelScheduler} from '../src/label-scheduler';

const position=C.Cartesian3.fromDegrees(127,36,7);
function fixture(limits?:{labels:number;codePoints:number}){
  let destroyed=false;
  const scene={primitives:new C.PrimitiveCollection(),preRender:new C.Event(),postRender:new C.Event(),requestRender:vi.fn(),isDestroyed:()=>destroyed,frameState:{mode:C.SceneMode.SCENE3D}} as unknown as C.Scene;
  const scheduler=new SceneLabelScheduler(scene,limits);
  const frame=()=>{scene.preRender.raiseEvent();scene.postRender.raiseEvent();};
  return {scene,scheduler,frame,collection:()=>scene.primitives.get(0) as C.LabelCollection,markDestroyed:()=>{destroyed=true;}};
}
beforeEach(()=>{
  const css:Record<string,string>={'line-height':'normal','font-family':'sans-serif','font-size':'12px','font-style':'normal','font-weight':'normal'};
  vi.stubGlobal('document',{createElement:()=>({style:{}}),body:{appendChild:()=>{},removeChild:()=>{}},defaultView:{getComputedStyle:()=>({getPropertyValue:(name:string)=>css[name]??''})}});
});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});

describe('scene label scheduling',()=>{
  it('shares one collection and admits at most two complete source labels per actual frame',()=>{
    const {scene,scheduler,frame,collection}=fixture();
    const names=['서울역','대전역','세종역','청주역','부산역'];
    const handles=names.map((text,i)=>scheduler.add({position,id:`source-${i}`,text,font:'12px sans-serif'}));
    expect(scene.primitives.length).toBe(0);expect(scheduler.snapshot()).toMatchObject({owned:5,queued:5,created:0});
    frame();const shared=collection();expect(shared.length).toBe(2);
    frame();expect(collection()).toBe(shared);expect(shared.length).toBe(4);
    frame();expect(scene.primitives.length).toBe(1);expect(shared.length).toBe(5);
    for(let i=0;i<names.length;i++)expect(shared.get(i)).toMatchObject({text:names[i],id:`source-${i}`,position});
    expect(scheduler.snapshot()).toMatchObject({owned:5,queued:0,created:5,failed:0,maxPerFrame:2});
    for(const handle of handles)handle.destroy();scheduler.destroy();scene.primitives.destroy();
  });
  it('keeps a single frame budget across duplicate callbacks and jobs enqueued mid-frame',()=>{
    const {scene,scheduler,collection}=fixture();
    for(let i=0;i<5;i++)scheduler.add({position,text:String(i)});
    scene.preRender.raiseEvent();expect(collection().length).toBe(2);
    scheduler.add({position,text:'new'});scene.preRender.raiseEvent();scene.preRender.raiseEvent();
    expect(collection().length).toBe(2);
    scene.postRender.raiseEvent();expect(scene.requestRender).toHaveBeenCalled();
    scene.preRender.raiseEvent();expect(collection().length).toBe(4);
    scene.postRender.raiseEvent();scheduler.destroy();scene.primitives.destroy();
  });
  it('keeps final admission preparing until postRender without claiming GPU atlas readiness',()=>{
    const {scene,scheduler}=fixture();
    expect(scheduler.snapshot().preparing).toBe(false);
    scheduler.add({position,text:'최종'});expect(scheduler.snapshot()).toMatchObject({preparing:true,queued:1,materialized:0});
    scene.preRender.raiseEvent();expect(scheduler.snapshot()).toMatchObject({preparing:true,queued:0,materialized:1});
    scene.postRender.raiseEvent();expect(scheduler.snapshot().preparing).toBe(false);
    scheduler.destroy();scene.primitives.destroy();
  });
  it('accounts for Unicode code points and preserves an oversized name without starving it',()=>{
    const {scene,scheduler,frame,collection}=fixture({labels:2,codePoints:4});
    for(const text of ['🚆🚆','역역','잘리지않는긴역사원문','끝'])scheduler.add({position,text});
    frame();expect(collection().length).toBe(2); // Two emoji are two code points, not four UTF-16 units.
    frame();expect(collection().length).toBe(3);expect(collection().get(2).text).toBe('잘리지않는긴역사원문');
    frame();expect(collection().get(3).text).toBe('끝');expect(scheduler.snapshot().queued).toBe(0);
    scheduler.destroy();scene.primitives.destroy();
  });
  it('skips dormant owners without blocking other jobs and reactivates the original labels',()=>{
    const {scene,scheduler,frame,collection}=fixture();
    const sleeping=scheduler.add({position,id:'sleeping-source',text:'휴면',show:false});
    const first=scheduler.add({position,id:'first',text:'먼저'}),second=scheduler.add({position,id:'second',text:'다음'});
    second.show=false;frame();expect(collection().length).toBe(1);expect(scheduler.snapshot().queued).toBe(0);
    second.show=true;sleeping.show=true;frame();expect(collection().length).toBe(3);
    const original=collection().get(0);first.show=false;expect(original.show).toBe(false);
    first.show=true;frame();expect(collection().get(0)).toBe(original);expect(original.show).toBe(true);
    expect(collection().get(2).id).toBe('sleeping-source');expect(scheduler.snapshot().created).toBe(3);
    scheduler.destroy();scene.primitives.destroy();
  });
  it('keeps the latest terrain position before admission and updates an existing label in place',()=>{
    const {scene,scheduler,frame,collection}=fixture();
    const handle=scheduler.add({position,id:'original',text:'위치'}),latest=C.Cartesian3.fromDegrees(127,36,42);
    handle.position=latest;latest.x+=100;
    frame();const label=collection().get(0);expect(C.Cartographic.fromCartesian(label.position).height).toBeCloseTo(42,5);
    handle.position=C.Cartesian3.fromDegrees(127,36,73);expect(collection().get(0)).toBe(label);
    expect(C.Cartographic.fromCartesian(label.position).height).toBeCloseTo(73,5);
    scheduler.destroy();scene.primitives.destroy();
  });
  it('does not accumulate unprocessed labels while the scene primitive collection is hidden',()=>{
    const {scene,scheduler,frame,collection}=fixture();
    for(let i=0;i<5;i++)scheduler.add({position,text:String(i)});
    scene.primitives.show=false;for(let i=0;i<6;i++)frame();
    expect(scene.primitives.length).toBe(0);expect(scheduler.snapshot().queued).toBe(5);
    scene.primitives.show=true;frame();expect(collection().length).toBe(2);
    scheduler.destroy();scene.primitives.destroy();
  });
  it('cancels pending owners immediately and releases the atlas when its final owner is gone',()=>{
    const {scene,scheduler,frame,collection}=fixture();
    const cancelled=scheduler.add({position,text:'취소'});cancelled.destroy();
    frame();expect(scene.primitives.length).toBe(0);expect(scheduler.snapshot()).toMatchObject({owned:0,queued:0,created:0});
    const current=scheduler.add({position,text:'표시'});frame();const old=collection();
    current.destroy();current.destroy();expect(scene.primitives.length).toBe(0);expect(old.isDestroyed()).toBe(true);
    scheduler.add({position,text:'다시'});frame();expect(collection()).not.toBe(old);expect(collection().get(0).text).toBe('다시');
    scheduler.destroy();scene.primitives.destroy();
  });
  it('isolates scenes and removes listeners before destroying resources and invalidating handles',()=>{
    const a=fixture(),b=fixture();
    const stale=a.scheduler.add({position,text:'장면가'});b.scheduler.add({position,text:'장면나'});
    a.frame();expect(a.scene.primitives.length).toBe(1);expect(b.scene.primitives.length).toBe(0);
    const collection=a.collection(),original=collection.destroy.bind(collection),destroy=vi.spyOn(collection,'destroy').mockImplementation(()=>{
      expect(a.scene.preRender.numberOfListeners).toBe(0);expect(a.scene.postRender.numberOfListeners).toBe(0);
      expect(a.scheduler.snapshot()).toMatchObject({owned:0,queued:0});return original();
    });
    a.scheduler.destroy();a.scheduler.destroy();expect(destroy).toHaveBeenCalledTimes(1);
    stale.show=true;stale.position=position;stale.destroy();a.frame();expect(a.scene.primitives.length).toBe(0);
    b.frame();expect(b.collection().get(0).text).toBe('장면나');expect(b.scene.preRender.numberOfListeners).toBe(1);
    expect(()=>a.scheduler.add({position,text:'종료뒤'})).toThrow('종료');
    b.scheduler.destroy();a.scene.primitives.destroy();b.scene.primitives.destroy();
  });
  it('records a failed label without throwing out other source jobs or retrying every frame',()=>{
    const {scene,scheduler,frame,collection}=fixture();
    const add=vi.spyOn(C.LabelCollection.prototype,'add');add.mockImplementationOnce(()=>{throw new Error('label allocation failed');});
    scheduler.add({position,id:'failed-source',text:'실패'});scheduler.add({position,id:'good-source',text:'성공'});
    expect(()=>frame()).not.toThrow();expect(collection().get(0).id).toBe('good-source');
    frame();expect(scheduler.snapshot()).toMatchObject({owned:2,queued:0,created:1,failed:1});expect(add).toHaveBeenCalledTimes(2);
    scheduler.destroy();scene.primitives.destroy();
  });
  it('handles scene destruction before explicit scheduler teardown without touching dead collections',()=>{
    const {scene,scheduler,frame,collection,markDestroyed}=fixture();
    scheduler.add({position,text:'이전'});frame();const labels=collection();
    scene.primitives.destroy();markDestroyed();expect(labels.isDestroyed()).toBe(true);
    expect(()=>scheduler.destroy()).not.toThrow();expect(scene.preRender.numberOfListeners).toBe(0);expect(scene.postRender.numberOfListeners).toBe(0);
  });
  it('keeps a fixed nationwide label from pinning the glyph admission of every later city',()=>{
    const {scene,scheduler,frame,collection}=fixture();
    const fixed=scheduler.add({position,id:'nationwide-source',text:'전국'});frame();
    const fixedGeneration=collection(),fixedLabel=fixedGeneration.get(0);let rotated=false;
    for(let city=0;city<350;city++){
      const text=`도시-${String(city).padStart(8,'0')}`,temporary=scheduler.add({position,id:`city-${city}`,text});
      frame();
      const latest=scene.primitives.get(scene.primitives.length-1) as C.LabelCollection;
      if(latest!==fixedGeneration)rotated=true;
      expect(scheduler.snapshot().collections).toBeLessThanOrEqual(2);
      expect(scheduler.snapshot().generationCodePoints).toBeLessThanOrEqual(LABEL_GENERATION_CODE_POINTS+text.length);
      temporary.destroy();
      if(latest!==fixedGeneration)expect(latest.isDestroyed()).toBe(true);
      expect(scheduler.snapshot()).toMatchObject({collections:1,materialized:1,owned:1,queued:0});
      expect(scheduler.snapshot().generationCodePoints).toBeLessThanOrEqual(LABEL_GENERATION_CODE_POINTS);
      expect(collection()).toBe(fixedGeneration);expect(fixedGeneration.get(0)).toBe(fixedLabel);
      expect(fixedLabel.id).toBe('nationwide-source');expect(fixedLabel.text).toBe('전국');
    }
    expect(rotated).toBe(true);expect(scheduler.snapshot().generationCodePoints).toBe(LABEL_GENERATION_CODE_POINTS);fixed.destroy();
    expect(scheduler.snapshot()).toMatchObject({collections:0,generationCodePoints:0,materialized:0});
    scheduler.destroy();scene.primitives.destroy();
  });
  it('never refunds the admission of removed labels while another owner pins their generation',()=>{
    const {scene,scheduler,frame,collection}=fixture();
    const fixed=scheduler.add({position,text:'고정'});frame();const pinned=collection();
    const old=scheduler.add({position,text:'가'.repeat(LABEL_GENERATION_CODE_POINTS-2)});frame();old.destroy();
    expect(scheduler.snapshot()).toMatchObject({collections:1,materialized:1,generationCodePoints:LABEL_GENERATION_CODE_POINTS});
    const newCity=scheduler.add({position,text:'다음도시'});frame();
    expect(scheduler.snapshot()).toMatchObject({collections:2,generationCodePoints:LABEL_GENERATION_CODE_POINTS+4});
    const city=scene.primitives.get(1) as C.LabelCollection;newCity.destroy();
    expect(city.isDestroyed()).toBe(true);expect(pinned.isDestroyed()).toBe(false);
    expect(scheduler.snapshot().generationCodePoints).toBe(LABEL_GENERATION_CODE_POINTS);
    fixed.destroy();scheduler.destroy();scene.primitives.destroy();
  });
  it('applies one scene-wide label and code-point budget when admission crosses generations',()=>{
    const {scene,scheduler,frame}=fixture();
    scheduler.add({position,text:'가'.repeat(LABEL_GENERATION_CODE_POINTS-8)});frame();
    scheduler.add({position,id:'fills-old',text:'나'.repeat(8)});
    scheduler.add({position,id:'new-generation',text:'다'.repeat(9)});
    scheduler.add({position,id:'same-new-generation',text:'라'});
    frame();expect(scheduler.snapshot()).toMatchObject({collections:1,materialized:2,queued:2,generationCodePoints:LABEL_GENERATION_CODE_POINTS});
    scene.preRender.raiseEvent();expect(scheduler.snapshot()).toMatchObject({collections:2,materialized:4,queued:0,maxPerFrame:2});
    scene.preRender.raiseEvent();expect(scheduler.snapshot().materialized).toBe(4);
    scene.postRender.raiseEvent();
    expect((scene.primitives.get(1) as C.LabelCollection).get(0).id).toBe('new-generation');
    expect(scheduler.snapshot().generationCodePoints).toBe(LABEL_GENERATION_CODE_POINTS+10);
    scheduler.destroy();scene.primitives.destroy();
  });
  it('does not restart the two-label frame allowance when a new generation opens in that frame',()=>{
    const {scene,scheduler,frame}=fixture();
    scheduler.add({position,text:'가'.repeat(LABEL_GENERATION_CODE_POINTS-1)});frame();
    for(const text of ['끝','새','다'])scheduler.add({position,text});
    scene.preRender.raiseEvent();
    expect(scheduler.snapshot()).toMatchObject({collections:2,materialized:3,queued:1,maxPerFrame:2,generationCodePoints:LABEL_GENERATION_CODE_POINTS+1});
    scene.preRender.raiseEvent();expect(scheduler.snapshot().queued).toBe(1);
    scene.postRender.raiseEvent();frame();
    expect(scheduler.snapshot()).toMatchObject({collections:2,materialized:4,queued:0,generationCodePoints:LABEL_GENERATION_CODE_POINTS+2});
    scheduler.destroy();scene.primitives.destroy();
  });
  it('gives a whole over-cap Unicode name a dedicated generation without admitting later labels to it',()=>{
    const {scene,scheduler,frame,collection}=fixture();
    const fixed=scheduler.add({position,text:'기준'});frame();const normal=collection();
    const text='🚆'.repeat(LABEL_GENERATION_CODE_POINTS+1),long=scheduler.add({position,id:'full-long-name',text});
    frame();const dedicated=scene.primitives.get(1) as C.LabelCollection;
    expect(dedicated.length).toBe(1);expect(dedicated.get(0).text).toBe(text);expect(dedicated.get(0).id).toBe('full-long-name');
    const next=scheduler.add({position,text:'근처역'});frame();
    expect(dedicated.length).toBe(1);expect(normal.length).toBe(2);
    expect(scheduler.snapshot()).toMatchObject({collections:2,generationCodePoints:LABEL_GENERATION_CODE_POINTS+6});
    fixed.destroy();next.destroy();expect(normal.isDestroyed()).toBe(true);
    scheduler.add({position,text:'새도시'});frame();expect(dedicated.length).toBe(1);
    long.destroy();expect(dedicated.isDestroyed()).toBe(true);expect(scheduler.snapshot().generationCodePoints).toBe(3);
    scheduler.destroy();scene.primitives.destroy();
  });
  it('assigns no generation to a queued or dormant handle and reuses its materialized generation after caching',()=>{
    const {scene,scheduler,frame,collection}=fixture();
    const pending=scheduler.add({position,text:'아직대기'}),cached=scheduler.add({position,id:'cached-source',text:'휴면',show:false});
    expect(scheduler.snapshot()).toMatchObject({collections:0,generationCodePoints:0,owned:2});
    pending.destroy();frame();expect(scheduler.snapshot()).toMatchObject({collections:0,generationCodePoints:0});
    cached.show=true;frame();const generation=collection(),label=generation.get(0),before=scheduler.snapshot();
    cached.show=false;frame();cached.show=true;frame();
    expect(collection()).toBe(generation);expect(generation.get(0)).toBe(label);expect(label.id).toBe('cached-source');
    expect(scheduler.snapshot()).toMatchObject({created:before.created,generationCodePoints:before.generationCodePoints,collections:1,queued:0});
    cached.destroy();expect(generation.isDestroyed()).toBe(true);scheduler.destroy();scene.primitives.destroy();
  });
  it('releases every generation after listeners are detached and leaves failed empty generations unowned',()=>{
    const {scene,scheduler,frame}=fixture();
    scheduler.add({position,text:'가'.repeat(LABEL_GENERATION_CODE_POINTS)});frame();
    const add=vi.spyOn(C.LabelCollection.prototype,'add');add.mockImplementationOnce(()=>{throw new Error('failed generation');});
    const failed=scheduler.add({position,text:'실패'});frame();
    expect(scheduler.snapshot()).toMatchObject({collections:1,generationCodePoints:LABEL_GENERATION_CODE_POINTS,failed:1});
    scheduler.add({position,text:'다음'});frame();expect(scheduler.snapshot().collections).toBe(2);
    const generations=[scene.primitives.get(0),scene.primitives.get(1)] as C.LabelCollection[];
    const spies=generations.map(collection=>{const original=collection.destroy.bind(collection);return vi.spyOn(collection,'destroy').mockImplementation(()=>{
      expect(scene.preRender.numberOfListeners).toBe(0);expect(scene.postRender.numberOfListeners).toBe(0);return original();
    });});
    scheduler.destroy();failed.destroy();
    for(const spy of spies)expect(spy).toHaveBeenCalledTimes(1);
    expect(scheduler.snapshot()).toMatchObject({collections:0,generationCodePoints:0,materialized:0,owned:0});
    scene.primitives.destroy();
  });
});
