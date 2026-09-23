import {describe,it,expect,vi} from 'vitest';
import {assertGeometryVertexBudget,compileGeometry,geometryVertexEstimate,type GeoCollection} from '../shared/geometry';
import {FrameWorkBudget,QUALITY,QualityGovernor,sceneQuality,selectViewAssets,TerrainRevisionGate} from '../shared/map-performance';
import {prepareTrack,preparedTrackPosition} from '../shared/replay-performance';
import {trackPosition} from '../shared/time';
import type {Asset,LayerId,Track} from '../shared/contracts';
const polygon=[[127,36],[127.1,36],[127.1,36.1],[127,36]];
const properties={name:'원본 시설',kind:'industrial_land',source_record_id:'way/123',evidence_type:'source_attribute'};
const collection:GeoCollection={type:'FeatureCollection',features:[{type:'Feature',id:'osm-way/123',properties,geometry:{type:'Polygon',coordinates:[polygon,[[127.02,36.02],[127.03,36.02],[127.03,36.03],[127.02,36.02]]]}},{type:'Feature',id:'line',properties:{kind:'road'},geometry:{type:'MultiLineString',coordinates:[[[127,36],[127.1,36.1]],[[127.2,36.2,12],[127.3,36.3,13]]]}},{type:'Feature',id:'point',properties:{kind:'station'},geometry:{type:'Point',coordinates:[127.11,36.12]}}]};
describe('transferable source geometry',()=>{
  it('preserves polygon holes, multipart lines, coordinates, IDs and source properties',()=>{
    const data=compileGeometry(collection);
    expect(data.features.map(f=>f.id)).toEqual(['osm-way/123','line','point']);
    expect(data.features[0].properties).toEqual(properties);
    expect(data.parts.map(p=>p.kind)).toEqual(['polygon','line','line','point']);
    expect(data.parts[0].rings).toEqual([4,4]);expect(data.vertexCount).toBe(13);
    expect(Array.from(data.coordinates.slice(0,12))).toEqual(polygon.flatMap(([x,y])=>[x,y,0]));
    expect(Array.from(data.coordinates.slice(30,36))).toEqual([127.2,36.2,12,127.3,36.3,13]);
  });
  it('restores compact shared/row properties and excludes only requested overview roads',()=>{
    const data=compileGeometry({...collection,metadata:{schema_version:1,shared:{source_id:'osm',name:'default'},rows:[{description:'원본 설명',name:'row'}]},features:[{...collection.features[0],properties:{metadata_index:0,...properties}},collection.features[1]]},true);
    expect(data.features).toHaveLength(1);expect(data.features[0].properties).toMatchObject({source_id:'osm',description:'원본 설명',name:'원본 시설',source_record_id:'way/123'});
    expect(()=>compileGeometry({...collection,features:[{...collection.features[0],properties:{metadata_index:9}}]})).toThrow('행 번호');
  });
  it('rejects malformed coordinates instead of manufacturing geometry',()=>{
    expect(()=>compileGeometry({...collection,features:[{...collection.features[0],geometry:{type:'Point',coordinates:[127,NaN]}}]})).toThrow('좌표');
    expect(()=>compileGeometry({...collection,features:[{...collection.features[0],geometry:{type:'LineString',coordinates:[[127,36]]}}]})).toThrow('부족');
  });
  it('treats an old feature-count estimate as a ceiling and refuses an understated vertex contract',()=>{
    const legacyWater={count:540};expect(geometryVertexEstimate(legacyWater)).toBe(8640);
    expect(()=>assertGeometryVertexBudget({vertexCount:97352},legacyWater)).toThrow('정점 예산 불일치');
    expect(()=>assertGeometryVertexBudget({vertexCount:97352},{...legacyWater,vertex_count:97352})).not.toThrow();
    expect(()=>assertGeometryVertexBudget({vertexCount:11},{count:1,vertex_count:10})).toThrow('정점 예산 불일치');
    // An overview may deliberately omit roads while retaining the same conservative source budget.
    expect(()=>assertGeometryVertexBudget({vertexCount:4},{count:1,vertex_count:10})).not.toThrow();
    expect(geometryVertexEstimate({count:1,vertex_count:-1})).toBe(Infinity);
  });
});
const layers=Object.fromEntries(['terrain','buildings','infrastructure','rail','bus','depth','satellite','radar','sun'].map(key=>[key,true])) as Record<LayerId,boolean>;
const asset=(id:string,extra:Partial<Asset>={}):Asset=>({id,layer:'infrastructure',format:'geojson',url:`/data/${id}.geojson`,bbox:[126,35,128,37],source_id:'osm',version:'1',sha256:id,count:100,byte_length:1024,vertex_count:1000,...extra});
const view={bbox:[126,35,128,37] as [number,number,number,number],height:10000,layers,mode:'sun' as const,dayStart:0,dayEnd:86400000,quality:'low' as const};
describe('viewport work admission and quality hysteresis',()=>{
  it('ignores repeated imagery loads and settles a changed terrain view only after movement and loading finish',()=>{
    const gate=new TerrainRevisionGate();expect(gate.pending).toBe(true);expect(gate.settle(true,false)).toBe(1);
    for(let radarFrame=0;radarFrame<13;radarFrame++){
      expect(gate.settle(false,false)).toBeUndefined();expect(gate.settle(true,false)).toBeUndefined();
    }
    expect(gate.revision).toBe(1);expect(gate.pending).toBe(false);
    gate.invalidate(); // camera stop reserves a new view
    gate.invalidate(); // the restored terrain SSE shares that reservation
    expect(gate.settle(true,true)).toBeUndefined();expect(gate.settle(false,false)).toBeUndefined();
    expect(gate.pending).toBe(true);expect(gate.settle(true,false)).toBe(3);
    expect(gate.settle(true,false)).toBeUndefined();expect(gate.pending).toBe(false);
    gate.invalidate(); // provider changes are allowed to refresh without camera motion
    expect(gate.settle(false,false)).toBeUndefined();expect(gate.settle(true,false)).toBe(4);
  });
  it('enforces bytes, vertices and files and reserves an overview for deferred detail',()=>{
    const assets=Array.from({length:80},(_,i)=>asset(`detail-${i}`,{byte_length:1024*1024,vertex_count:12000}));
    assets.push(asset('overview',{detail_level:'overview',min_camera_height:60000,max_camera_height:350000}));
    const result=selectViewAssets(assets,view);
    expect(result.deferred).toBeGreaterThan(0);expect(result.fallback).toBe(true);expect(result.assets[0].id).toBe('overview');
    expect(result.bytes).toBeLessThanOrEqual(QUALITY.low.bytes);expect(result.vertices).toBeLessThanOrEqual(QUALITY.low.vertices);expect(result.assets.length).toBeLessThanOrEqual(QUALITY.low.files);
  });
  it('skips duplicate roads before fetching, respects time/layer gates and does not render index assets',()=>{
    const result=selectViewAssets([asset('infra'),asset('duplicate',{source_id:'overture-transportation',layer:'terrain'}),asset('index',{format:'asset-index'}),asset('search',{format:'search-index'}),asset('future',{format:'replay',layer:'rail',from:'2030-01-01T00:00:00Z',to:'2030-01-01T01:00:00Z'})],view);
    expect(result.assets.map(a=>a.id)).toEqual(['infra']);
  });
  it('retains the national building hierarchy while close-only footprints stay out of the distant view',()=>{
    const result=selectViewAssets([asset('national',{layer:'buildings',format:'3d-tiles',detail_level:'overview'}),asset('outline',{layer:'buildings',max_camera_height:60000}),asset('legacy-flat-3d',{layer:'buildings',format:'3d-tiles'})],{...view,height:500000});
    expect(result.assets.map(a=>a.id)).toEqual(['national']);
  });
  it('reserves a selected underground layer before road detail and coarse fallback exhaust the budget',()=>{
    const roads=Array.from({length:80},(_,i)=>asset(`road-${i}`,{byte_length:1024*1024,vertex_count:12000}));
    roads.push(asset('road-overview',{detail_level:'overview',min_camera_height:60000,byte_length:12*1024*1024,vertex_count:130000}));
    const depth=asset('seoul-depth',{layer:'depth',count:731,byte_length:2*1024*1024,vertex_count:10000});
    const result=selectViewAssets([...roads,depth],view);
    expect(result.assets[0].id).toBe('seoul-depth');expect(result.assets.some(a=>a.id==='road-overview')).toBe(true);
    expect(result.bytes).toBeLessThanOrEqual(QUALITY.low.bytes);expect(result.vertices).toBeLessThanOrEqual(QUALITY.low.vertices);
    expect(selectViewAssets([...roads,depth],{...view,layers:{...layers,depth:false}}).assets.some(a=>a.layer==='depth')).toBe(false);
  });
  it('keeps nearby street detail before additional overview files consume the entire budget',()=>{
    const roads=Array.from({length:10},(_,i)=>asset(`detail-${i}`,{detail_level:'detail',byte_length:2*1024*1024,vertex_count:10000}));
    const overviews=Array.from({length:3},(_,i)=>asset(`overview-${i}`,{detail_level:'overview',min_camera_height:60000,byte_length:5*1024*1024,vertex_count:30000}));
    const result=selectViewAssets([...roads,...overviews],view);
    expect(result.assets[0].id).toBe('overview-0');
    expect(result.assets[1].id).toBe('detail-0');
    expect(result.assets.filter(a=>a.id==='detail-0')).toHaveLength(1);
    expect(result.assets.filter(a=>a.id==='overview-0')).toHaveLength(1);
    expect(result.fallback).toBe(true);expect(result.deferred).toBeGreaterThan(0);
    expect(result.bytes).toBeLessThanOrEqual(QUALITY.low.bytes);
    expect(result.vertices).toBeLessThanOrEqual(QUALITY.low.vertices);
    expect(result.assets.length).toBeLessThanOrEqual(QUALITY.low.files);
  });
  it('reduces transient camera work and restores the same settled quality after movement',()=>{
    expect(sceneQuality('high',true,true)).toMatchObject({resolution:.75,sse:1024,shadows:false});
    expect(sceneQuality('high',false,true)).toMatchObject({resolution:1,sse:16,shadows:true});
    expect(sceneQuality('low',true,true)).toMatchObject({resolution:.7,sse:1024,terrainSse:8,shadows:false});
    expect(sceneQuality('low',false,true)).toMatchObject({sse:40,terrainSse:4});
    expect(sceneQuality('high',false,true)).toMatchObject({terrainSse:2});
    expect(sceneQuality('balanced',true,true)).toMatchObject({sse:1024,terrainSse:6});
    expect(sceneQuality('balanced',false,true)).toMatchObject({sse:24,terrainSse:3});
  });
  it('reduces pixel multisampling with sustained quality tiers without reallocating it on every camera move',()=>{
    expect(sceneQuality('high',false,true).msaaSamples).toBe(4);
    expect(sceneQuality('balanced',false,true).msaaSamples).toBe(2);
    expect(sceneQuality('low',false,true).msaaSamples).toBe(1);
    for(const quality of ['high','balanced','low'] as const){
      expect(sceneQuality(quality,true,true).msaaSamples).toBe(sceneQuality(quality,false,true).msaaSamples);
      expect(sceneQuality(quality,false,true).shadows).toBe(true);
    }
  });
  it('counts the full water polygon source before admitting nearby roads and rejects invalid counts',()=>{
    const water=asset('water',{layer:'terrain',count:540,vertex_count:97352,byte_length:2609440});
    const road=asset('road',{vertex_count:80000});
    const result=selectViewAssets([water,road,asset('invalid',{vertex_count:-1})],view);
    expect(result.vertices).toBeLessThanOrEqual(QUALITY.low.vertices);expect(result.assets).toHaveLength(1);expect(result.deferred).toBe(2);
  });
  it('restores the sunlight toggle after movement even when automatic quality has degraded',()=>{
    for(const quality of ['high','balanced','low'] as const){
      expect(sceneQuality(quality,true,true,6500).shadows).toBe(false);
      expect(sceneQuality(quality,false,true,6500).shadows).toBe(true);
      expect(sceneQuality(quality,false,false,6500).shadows).toBe(false);
    }
    expect(sceneQuality('high',false,true,6500)).toMatchObject({shadowSize:2048,shadowDistance:11700});
    expect(sceneQuality('low',false,true,1000000)).toMatchObject({shadowSize:512,shadowDistance:15000,shadows:false});
    expect(sceneQuality('balanced',false,true,500)).toMatchObject({shadowSize:1024,shadowDistance:5000});
  });
  it('ignores idle gaps, degrades under sustained pressure and recovers only after 20 healthy seconds',()=>{
    const governor=new QualityGovernor();
    for(let i=0;i<60;i++)governor.observe(3,null,i*1000);
    expect(governor.quality).toBe('high');
    for(let i=0;i<100;i++)governor.observe(30,50,60000+i*50);
    expect(governor.quality).not.toBe('high');
    const slowQuality=governor.quality;
    for(let i=0;i<100;i++)governor.observe(3,16,70000+i*16);
    expect(governor.quality).toBe(slowQuality);
    for(let i=0;i<1600;i++)governor.observe(3,16,72000+i*16);
    expect(governor.quality).not.toBe('low');
  });
  it.each(['checkpoint','defer'] as const)('cancels only one %s waiter while a suspended shared frame remains reusable',async method=>{
    let now=0,calls=0,survived=false,operations=0;const frames:Array<()=>void>=[];
    const budget=new FrameWorkBudget(()=>now,()=>{calls++;return new Promise<void>(resolve=>frames.push(resolve));});
    const finish=budget.startTask();now=5;
    const cancelled=new AbortController(),survivor=new AbortController();
    const cancelledAdd=vi.spyOn(cancelled.signal,'addEventListener'),cancelledRemove=vi.spyOn(cancelled.signal,'removeEventListener');
    const survivorAdd=vi.spyOn(survivor.signal,'addEventListener'),survivorRemove=vi.spyOn(survivor.signal,'removeEventListener');
    const stopped=expect(budget[method](cancelled.signal)).rejects.toMatchObject({name:'AbortError'});
    const waiting=budget[method](survivor.signal).then(()=>{survived=true;});
    expect(calls).toBe(1);cancelled.abort();await stopped;
    // No frame has resolved: cancellation must release its caller immediately.
    expect(survived).toBe(false);expect(frames).toHaveLength(1);
    expect(cancelledRemove).toHaveBeenCalledExactlyOnceWith('abort',cancelledAdd.mock.calls[0][1]);
    expect(survivorRemove).not.toHaveBeenCalled();
    const joined=budget.run('late-job',()=>{operations++;});
    expect(calls).toBe(1);expect(operations).toBe(0);
    now=20;frames.shift()!();await Promise.all([waiting,joined]);
    expect(survived).toBe(true);expect(operations).toBe(1);
    expect(cancelledRemove).toHaveBeenCalledTimes(1);
    expect(survivorRemove).toHaveBeenCalledExactlyOnceWith('abort',survivorAdd.mock.calls[0][1]);
    survivor.abort();expect(survivorRemove).toHaveBeenCalledTimes(1);
    // The shared slot becomes available again after the suspended frame resumes.
    const next=budget.defer();expect(calls).toBe(2);now=36;frames.shift()!();await next;finish();
  });
  it('rejects already cancelled input without scheduling frames, listeners or synchronous work',async()=>{
    const nextFrame=vi.fn(async()=>{}),operation=vi.fn();
    const budget=new FrameWorkBudget(()=>0,nextFrame),controller=new AbortController();controller.abort();
    const add=vi.spyOn(controller.signal,'addEventListener');
    await expect(budget.defer(controller.signal)).rejects.toMatchObject({name:'AbortError'});
    await expect(budget.checkpoint(controller.signal)).rejects.toMatchObject({name:'AbortError'});
    await expect(budget.run('cancelled',operation,controller.signal)).rejects.toMatchObject({name:'AbortError'});
    expect(nextFrame).not.toHaveBeenCalled();expect(operation).not.toHaveBeenCalled();expect(add).not.toHaveBeenCalled();
  });
  it('removes abort listeners when the shared frame itself rejects',async()=>{
    let rejectFrame!:(error:Error)=>void;
    const budget=new FrameWorkBudget(()=>0,()=>new Promise<void>((_resolve,reject)=>{rejectFrame=reject;}));
    const controller=new AbortController(),add=vi.spyOn(controller.signal,'addEventListener'),remove=vi.spyOn(controller.signal,'removeEventListener');
    const error=new Error('frame failed'),waiting=expect(budget.defer(controller.signal)).rejects.toBe(error);
    rejectFrame(error);await waiting;
    expect(remove).toHaveBeenCalledExactlyOnceWith('abort',add.mock.calls[0][1]);
    controller.abort();expect(remove).toHaveBeenCalledTimes(1);
  });
  it('does not hide long moving frames from the performance result',()=>{
    const governor=new QualityGovernor();let result;
    for(let i=0;i<20;i++)result=governor.observe(2,i%5===0?400:16,i*16);
    expect(result?.frameP95Ms).toBe(400);
  });
  it('retains slow-frame measurements when already at the lowest quality',()=>{
    const governor=new QualityGovernor('low');let result;
    for(let i=0;i<180;i++)result=governor.observe(35,120,1000+i*120);
    expect(result).toMatchObject({quality:'low',renderP95Ms:35,frameP95Ms:120,sampleCount:90});
  });
  it('retains healthy measurements when already at the highest quality',()=>{
    const governor=new QualityGovernor('high');let result;
    for(let i=0;i<2400;i++)result=governor.observe(3,16,1000+i*16);
    expect(result).toMatchObject({quality:'high',renderP95Ms:3,frameP95Ms:16,sampleCount:90});
  });
  it('excludes network idle gaps from reported cooperative construction chunks',async()=>{
    let now=0;const budget=new FrameWorkBudget(()=>now,async()=>{now+=16;},8);
    let done=budget.startTask();now=4;await budget.checkpoint();done();
    now=10000;done=budget.startTask();now+=8;await budget.checkpoint();now+=2;done();
    expect(budget.snapshot()).toMatchObject({chunkP95Ms:8,chunkMaxMs:8,chunkCount:3});
  });
  it.each([3,15])('excludes shared preparation waits while retaining another task\'s %ims operation',async operationMs=>{
    let now=0,frames=0,releaseFirst!:()=>void,releaseSecond!:()=>void;
    const budget=new FrameWorkBudget(()=>now,async()=>{frames++;now+=16;});
    const first=budget.startTask(),second=budget.startTask();
    await budget.run('first-task',()=>{now+=2;});
    const firstWait=first.waitFor(new Promise<void>(resolve=>{releaseFirst=resolve;}));
    await budget.run('second-task',()=>{now+=operationMs;});
    const secondWait=second.waitFor(new Promise<void>(resolve=>{releaseSecond=resolve;}));
    now+=2572;releaseFirst();await firstWait;
    await budget.run('resumed-task',()=>{now+=1;});
    releaseSecond();await secondWait;first();second();
    expect(budget.snapshot()).toMatchObject({chunkMaxMs:2+operationMs,operationMaxMs:operationMs,operationMaxPhase:'second-task',chunkCount:2});
    // Waiting is excluded only from telemetry: the expired shared deadline still yields.
    expect(frames).toBe(1);
  });
  it('keeps the same shared deadline when an admission promise is already resolved',async()=>{
    let now=0,frames=0;const budget=new FrameWorkBudget(()=>now,async()=>{frames++;now+=16;});
    const finish=budget.startTask();
    await budget.run('first',()=>{now+=3;});await finish.waitFor(Promise.resolve());
    await budget.run('second',()=>{now+=3;});await budget.run('after-deadline',()=>{now+=1;});finish();
    expect(frames).toBe(1);expect(budget.snapshot()).toMatchObject({chunkMaxMs:6,operationMaxMs:3,chunkCount:2});
  });
  it('does not resurrect an ended task when its suspended promise rejects',async()=>{
    let now=0,rejectWait!:(reason:Error)=>void;const budget=new FrameWorkBudget(()=>now,async()=>{now+=16;});
    const finish=budget.startTask();await budget.run('before-abort',()=>{now+=2;});
    const waiting=finish.waitFor(new Promise<void>((_resolve,reject)=>{rejectWait=reject;}));
    const stopped=expect(waiting).rejects.toMatchObject({name:'AbortError'});
    now+=5000;finish();finish();rejectWait(new DOMException('Aborted','AbortError'));await stopped;
    now+=5000;const following=budget.startTask();await budget.run('following',()=>{now+=1;});following();
    expect(budget.snapshot()).toMatchObject({chunkMaxMs:2,chunkCount:2,operationMaxMs:2});
  });
  it('rechecks one shared deadline before every concurrent synchronous unit',async()=>{
    let now=0,calls=0;const waits:Array<()=>void>=[];
    const budget=new FrameWorkBudget(()=>now,()=>new Promise<void>(resolve=>waits.push(()=>{now+=16;resolve();})),4);
    const done=budget.startTask();
    const jobs=Array.from({length:10},()=>budget.run('height-sample',()=>{calls++;now+=3;}));
    expect(calls).toBe(2);expect(waits).toHaveLength(1);
    for(let frame=0;frame<4;frame++){
      waits.shift()!();for(let microtask=0;microtask<12;microtask++)await Promise.resolve();
      expect(calls).toBe(Math.min(10,(frame+2)*2));
    }
    await Promise.all(jobs);done();
    expect(budget.snapshot()).toMatchObject({chunkMaxMs:6,operationMaxMs:3,operationMaxPhase:'height-sample'});
  });
  it('defers event-driven work before sampling and never starts a cancelled unit',async()=>{
    let now=0,resolve!:()=>void,calls=0;
    const budget=new FrameWorkBudget(()=>now,()=>new Promise<void>(done=>{resolve=done;}));
    const controller=new AbortController();
    const work=(async()=>{await budget.defer(controller.signal);const finish=budget.startTask();try{await budget.run('height',()=>{calls++;now+=1;},controller.signal);}finally{finish();}})();
    const stopped=expect(work).rejects.toMatchObject({name:'AbortError'});
    expect(calls).toBe(0);now=100;controller.abort();await stopped;
    expect(calls).toBe(0);expect(budget.snapshot().chunkCount).toBe(0);
    resolve();await budget.checkpoint();expect(calls).toBe(0);
  });
  it('reports an indivisible slow Cesium operation honestly instead of clamping the measurement',async()=>{
    let now=0;const budget=new FrameWorkBudget(()=>now,async()=>{now+=16;});
    const finish=budget.startTask();await budget.run('terrain-height',()=>{now+=15;});finish();
    expect(budget.snapshot()).toMatchObject({chunkMaxMs:15,operationMaxMs:15,operationMaxPhase:'terrain-height'});
  });
});
describe('prepared replay compatibility',()=>{
  it('matches exact points, interpolation, end points and observation gaps',()=>{
    const track={id:'train',max_gap_seconds:90,points:[{time:'2026-09-09T00:00:00Z',lon:127,lat:36,height:10},{time:'2026-09-09T00:01:00Z',lon:127.1,lat:36.1,height:20},{time:'2026-09-09T00:10:00Z',lon:127.2,lat:36.2}]} as Track;
    const prepared=prepareTrack(track),start=Date.parse(track.points[0].time);
    for(const offset of [-1,0,1000,30000,60000,70000,599000,600000,600001])expect(preparedTrackPosition(prepared,start+offset)).toEqual(trackPosition(track,start+offset));
  });
  it('rejects unordered observations and invalid source coordinates before constructing visible tracks',()=>{
    const good={max_gap_seconds:90,points:[{time:'2026-09-09T00:00:00Z',lon:127,lat:36},{time:'2026-09-09T00:01:00Z',lon:127.1,lat:36.1}]} as Track;
    expect(()=>prepareTrack({...good,points:[...good.points].reverse()})).toThrow('시각 또는 좌표');
    expect(()=>prepareTrack({...good,points:[{...good.points[0],height:NaN}]})).toThrow('시각 또는 좌표');
    expect(()=>prepareTrack({...good,max_gap_seconds:Infinity})).toThrow('형식');
  });
});
