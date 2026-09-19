import {createHash} from 'node:crypto';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {validateStyleMin} from '@maplibre/maplibre-gl-style-spec';
import {map2DSourceLayers} from '../src/Map2D';
import type {Asset,LayerId} from '../shared/contracts';
import type {GeoCollection} from '../shared/geometry';
import {createMap2DFetcher,createMap2DPixelRatioController,map2DHeight,map2DRoadClass,map2DZoom,map2DOverviewPadding,map2DPixelRatio,prepareMap2DData,readFlatCamera,selectMap2DAssets,selectMap2DFeature} from '../shared/map2d';
import type {Map2DWorkerRequest,Map2DWorkerResponse} from '../src/map2d-data.worker';

const polygon=[[[127.123456789123,36,18],[127.2,36,19],[127.2,36.1,20],[127.123456789123,36,18]],[[127.15,36.01],[127.16,36.01],[127.16,36.02],[127.15,36.01]]];
const collection:GeoCollection={type:'FeatureCollection',metadata:{schema_version:1,shared:{source_id:'osm',description:'공통 원천 설명',evidence_type:'source_attribute'},rows:[{name:'단지 이름',source_record_id:'way/11',original_nested:{verified:false}}]},features:[
  {type:'Feature',id:'osm-way/11',properties:{metadata_index:0,kind:'industrial_land'},geometry:{type:'MultiPolygon',coordinates:[polygon]}},
  {type:'Feature',id:0,properties:{name:'원래 도로 이름',kind:'road',highway:'motorway_link',ref:'1'},geometry:{type:'LineString',coordinates:[[127,36,10],[127.1,36.1,11]]}},
  {type:'Feature',properties:{kind:'railway',name:'원래 철도 이름'},geometry:{type:'GeometryCollection',geometries:[{type:'Point',coordinates:[127.01,36.01]},{type:'MultiLineString',coordinates:[[[127,36],[127.2,36.2]]]}]}},
]};
function fixture(data:GeoCollection=collection,extra:Partial<Asset>={}){
  const bytes=new TextEncoder().encode(JSON.stringify(data)).buffer;
  const asset:Asset={id:'fixture',layer:'infrastructure',format:'geojson',url:'/data/fixture.geojson',bbox:[126,35,128,37],source_id:'osm',version:'fixture',sha256:createHash('sha256').update(Buffer.from(bytes)).digest('hex'),count:data.features.length,feature_count:data.features.length,vertex_count:13,byte_length:bytes.byteLength,...extra};
  return {asset,bytes};
}
const allLayers=Object.fromEntries(['terrain','buildings','infrastructure','rail','bus','depth','radar','satellite','sun'].map(key=>[key,true])) as Record<LayerId,boolean>;
afterEach(()=>{vi.unstubAllGlobals();});

describe('2D source-faithful worker preparation',()=>{
  it('fits the national view above the mobile analysis sheet and beside the desktop panel',()=>{
    const phone=map2DOverviewPadding(390,844,true),desktop=map2DOverviewPadding(1280,720,true),focus=map2DOverviewPadding(390,844,false,true);
    expect(phone.bottom).toBeGreaterThanOrEqual(844*.4);expect(phone.top).toBeGreaterThanOrEqual(205);
    expect(844-phone.top-phone.bottom).toBeGreaterThan(200);expect(desktop.left).toBeGreaterThan(368);
    expect(focus.top+focus.bottom).toBeLessThan(phone.top+phone.bottom);
    const smallPhone=map2DOverviewPadding(320,568,true);
    expect(smallPhone.top).toBeGreaterThanOrEqual(205);expect(smallPhone.bottom).toBeGreaterThanOrEqual(568*.4);
    for(const [w,h] of [[320,568],[844,390],[160,160]]){const padding=map2DOverviewPadding(w,h,true);expect(w-padding.left-padding.right).toBeGreaterThan(0);expect(h-padding.top-padding.bottom).toBeGreaterThanOrEqual(64);}
  });
  it('preserves original ID/coordinates/holes/heights and restores shared attributes only on selection',async()=>{
    const {asset,bytes}=fixture(),prepared=await prepareMap2DData(asset,bytes);
    const render=JSON.parse(new TextDecoder().decode(prepared.bytes)) as GeoCollection;
    expect(render.features.map(feature=>feature.geometry)).toEqual(collection.features.map(feature=>feature.geometry));
    expect(render.features.map(feature=>feature.id)).toEqual(['osm-way/11',0,'feature-2']);
    expect(prepared.vertexCount).toBe(13);
    expect(render.features[0].properties).toEqual({map2d_index:0,map2d_category:'facility',map2d_road:'other'});
    expect(render.features[1].properties).toMatchObject({map2d_road:'motorway'});
    const selected=selectMap2DFeature(asset,prepared,0);
    expect(selected).toMatchObject({sourceId:'osm-way/11',name:'단지 이름',properties:{source_record_id:'way/11',source_id:'osm',description:'공통 원천 설명',original_nested:{verified:false}}});
    expect(selected?.properties).not.toHaveProperty('metadata_index');
    expect(selectMap2DFeature(asset,prepared,1)?.sourceId).toBe('0');
    expect(selectMap2DFeature(asset,prepared,-1)).toBeNull();
    expect(collection.metadata?.rows[0]).toHaveProperty('original_nested');
  });
  it('rejects byte/hash/count/vertex mismatches and unsafe paths instead of showing altered data',async()=>{
    const {asset,bytes}=fixture();
    await expect(prepareMap2DData({...asset,sha256:'0'.repeat(64)},bytes)).rejects.toThrow('해시');
    await expect(prepareMap2DData({...asset,byte_length:1},bytes)).rejects.toThrow('크기');
    await expect(prepareMap2DData({...asset,feature_count:1},bytes)).rejects.toThrow('객체 수');
    await expect(prepareMap2DData({...asset,vertex_count:2},bytes)).rejects.toThrow('정점');
    for(const url of ['https://example.com/file.geojson','/data/../secret','/data/file.geojson?key=private'])await expect(prepareMap2DData({...asset,url},bytes)).rejects.toThrow('경로');
  });
  it('rejects invalid coordinates/compact rows and stops aborted preparation',async()=>{
    const invalid=fixture({...collection,features:[{...collection.features[0],geometry:{type:'Point',coordinates:[181,36]}}]});
    await expect(prepareMap2DData(invalid.asset,invalid.bytes)).rejects.toThrow('좌표');
    const badRow=fixture({...collection,features:[{...collection.features[0],properties:{metadata_index:42}}]});
    await expect(prepareMap2DData(badRow.asset,badRow.bytes)).rejects.toThrow('행 번호');
    const {asset,bytes}=fixture(),controller=new AbortController();controller.abort();
    await expect(prepareMap2DData(asset,bytes,controller.signal)).rejects.toMatchObject({name:'AbortError'});
  });
  it('keeps source road classes distinct and does not infer Korean legal grades',()=>{
    expect(['motorway','motorway_link','trunk','primary','secondary','국도','unknown'].map(map2DRoadClass)).toEqual(['motorway','motorway','trunk','primary','secondary','other','other']);
  });
  it('validates real MapLibre style expressions and orders land/water/facilities below roads and points',()=>{
    const {asset}=fixture();
    for(const [source_id,layer,anchor] of [['natural-earth','terrain','land'],['osm','terrain','water'],['osm','infrastructure','area']] as const){
      const generated=map2DSourceLayers('fixture',{...asset,source_id,layer});
      expect(validateStyleMin({version:8,sources:{fixture:{type:'geojson',data:{type:'FeatureCollection',features:[]}}},layers:generated.map(row=>row.layer)})).toEqual([]);
      expect(generated.map(row=>row.before)).toEqual([`map2d-anchor-${anchor}`,'map2d-anchor-road','map2d-anchor-point']);
    }
  });
  it('worker releases complete selection records and never returns a released record',async()=>{
    const replies:Map2DWorkerResponse[]=[],scope:{onmessage?:((event:MessageEvent<Map2DWorkerRequest>)=>void);postMessage:(value:Map2DWorkerResponse)=>void}={postMessage:value=>replies.push(value)};
    vi.stubGlobal('self',scope);await import('../src/map2d-data.worker');
    const {asset,bytes}=fixture();scope.onmessage!({data:{type:'load',id:41,asset,bytes}} as MessageEvent<Map2DWorkerRequest>);
    await vi.waitFor(()=>expect(replies.some(reply=>reply.id===41&&reply.type==='loaded')).toBe(true));
    scope.onmessage!({data:{type:'pick',id:42,record:41,index:0}} as MessageEvent<Map2DWorkerRequest>);
    expect(replies.at(-1)).toMatchObject({id:42,type:'picked',selection:{sourceId:'osm-way/11',name:'단지 이름'}});
    scope.onmessage!({data:{type:'release',id:41}} as MessageEvent<Map2DWorkerRequest>);
    scope.onmessage!({data:{type:'pick',id:43,record:41,index:0}} as MessageEvent<Map2DWorkerRequest>);
    expect(replies.at(-1)).toEqual({id:43,type:'picked',selection:null});
    scope.onmessage!({data:{type:'load',id:44,asset,bytes}} as MessageEvent<Map2DWorkerRequest>);
    scope.onmessage!({data:{type:'cancel',id:44}} as MessageEvent<Map2DWorkerRequest>);
    await new Promise(resolve=>setTimeout(resolve,10));
    expect(replies.some(reply=>reply.id===44)).toBe(false);
  });
});

describe('2D view contract',()=>{
  it('uses independent validated flat-camera links without interpreting ECEF as longitude',()=>{
    expect(readFlatCamera('#camera=1000000,2000000,3000000,0,0,0')).toBeNull();
    expect(readFlatCamera('#flatCamera=127.4,36.3,12.5,-30')).toEqual([127.4,36.3,12.5,-30]);
    for(const camera of ['0,36,12,0','127,41,12,0','127,36,20,0','127,36,12,181','127,36,NaN,0','127,36,12,','127,36,12,0,1'])expect(readFlatCamera(`#flatCamera=${camera}`)).toBeNull();
    for(const height of [3500,60000,1500000])expect(map2DHeight(36,map2DZoom(36,height,720),720)).toBeCloseTo(height,7);
  });
  it('keeps the exact 60km original/local water split and excludes every 3D or time source',()=>{
    const {asset}=fixture(),full={...asset,id:'water-national',layer:'terrain' as const,min_camera_height:60000},local={...asset,id:'water-local',layer:'terrain' as const,max_camera_height:60000};
    const assets=[full,local,{...asset,id:'3d',format:'3d-tiles' as const},{...asset,id:'terrain',format:'quantized-mesh' as const},{...asset,id:'depth',layer:'depth' as const},{...asset,id:'replay',format:'replay' as const}];
    const view={bbox:asset.bbox,layers:allLayers,quality:'low' as const};
    expect(selectMap2DAssets(assets,{...view,height:60000}).assets.map(row=>row.id)).toEqual(['water-national']);
    expect(selectMap2DAssets(assets,{...view,height:59999}).assets.map(row=>row.id)).toEqual(['water-local']);
  });
});

describe('2D body-lifetime download bound',()=>{
  it('holds four slots until response bodies finish and strips decoded-body encoding headers',async()=>{
    const controllers:ReadableStreamDefaultController<Uint8Array>[]=[];
    const base=vi.fn(async()=>new Response(new ReadableStream<Uint8Array>({start(controller){controllers.push(controller);}}),{headers:{'content-encoding':'gzip','content-length':'99','etag':'source'}})) as unknown as typeof fetch;
    const gate=createMap2DFetcher(base),requests=Array.from({length:5},(_,index)=>gate.fetcher(`/data/${index}.json`));
    await vi.waitFor(()=>expect(base).toHaveBeenCalledTimes(4));expect(gate.stats()).toEqual({active:4,peak:4});
    controllers[0].enqueue(new Uint8Array([1,2,3]));controllers[0].close();
    const first=await requests[0];expect(first.headers.get('content-encoding')).toBeNull();expect(first.headers.get('content-length')).toBeNull();expect(first.headers.get('etag')).toBe('source');expect([...new Uint8Array(await first.arrayBuffer())]).toEqual([1,2,3]);
    await vi.waitFor(()=>expect(base).toHaveBeenCalledTimes(5));
    for(const controller of controllers.slice(1))controller.close();await Promise.all(requests);expect(gate.stats()).toEqual({active:0,peak:4});gate.dispose();
  });
  it('cancels queued and active reads without fetching stale queued work; disposal cannot restart it',async()=>{
    let cancellations=0;
    const base=vi.fn(async()=>new Response(new ReadableStream<Uint8Array>({cancel(){cancellations++;}}))) as unknown as typeof fetch;
    const gate=createMap2DFetcher(base),controllers=Array.from({length:5},()=>new AbortController());
    const requests=controllers.map((controller,index)=>gate.fetcher(`/data/${index}.json`,{signal:controller.signal}).catch(error=>error));
    await vi.waitFor(()=>expect(base).toHaveBeenCalledTimes(4));controllers[4].abort();controllers[0].abort();gate.dispose();
    expect((await Promise.all(requests)).every(error=>error.name==='AbortError')).toBe(true);
    expect(base).toHaveBeenCalledTimes(4);expect(cancellations).toBe(4);expect(gate.stats().active).toBe(0);
    await expect(gate.fetcher('/data/new.json')).rejects.toMatchObject({name:'AbortError'});
  });
  it('rejects foreign paths before network access and releases a failed slot',async()=>{
    const base=vi.fn(async()=>{throw new TypeError('network');}) as unknown as typeof fetch,gate=createMap2DFetcher(base);
    await expect(gate.fetcher('https://example.com/data.json')).rejects.toThrow('경로');expect(base).not.toHaveBeenCalled();
    await expect(gate.fetcher('/data/one.json')).rejects.toThrow('network');expect(gate.stats().active).toBe(0);gate.dispose();
  });
});

describe('2D movement framebuffer resolution',()=>{
  function target(deviceRatio=2){
    let ratio=map2DPixelRatio(deviceRatio),moving=false;
    const map={getPixelRatio:()=>ratio,isMoving:()=>moving,setPixelRatio:vi.fn((value:number)=>{ratio=value;})};
    return {map,move:(value:boolean)=>{moving=value;},controller:createMap2DPixelRatioController(map,()=>deviceRatio)};
  }
  it('caps only framebuffer resolution and never upsamples low-DPR devices',()=>{
    expect([.8,1,1.25,1.5,2,3].map(dpr=>map2DPixelRatio(dpr))).toEqual([.8,1,1.25,1.5,1.5,1.5]);
    expect([.8,1,1.25,2,3].map(dpr=>map2DPixelRatio(dpr,true))).toEqual([.8,1,1,1,1]);
    for(const dpr of [0,-1,NaN,Infinity])expect(map2DPixelRatio(dpr,true)).toBe(1);
  });
  it('changes once for a programmatic flight and once after confirmed rest',()=>{
    const {map,move,controller}=target();move(true);
    controller.moveStart();
    for(let i=0;i<100;i++){controller.moveStart();controller.beforeWheel();expect(controller.restore()).toBe(false);}
    expect(map.setPixelRatio.mock.calls).toEqual([[1]]);
    move(false);expect(controller.restore()).toBe(true);expect(controller.restore()).toBe(false);
    expect(map.setPixelRatio.mock.calls).toEqual([[1],[1.5]]);
  });
  it('resizes before native input and never resizes a running drag',()=>{
    const {map,move,controller}=target();
    controller.pointerDown(5);expect(map.setPixelRatio.mock.calls).toEqual([[1]]);
    move(true);controller.moveStart({type:'mousemove'});controller.pointerDown(6);controller.beforeWheel();
    expect(controller.restore()).toBe(false);expect(map.setPixelRatio).toHaveBeenCalledTimes(1);
    move(false);controller.pointerUp(5);expect(controller.inputHeld).toBe(true);expect(controller.restore()).toBe(false);
    controller.pointerUp(6);expect(controller.inputHeld).toBe(false);expect(controller.restore()).toBe(true);
    expect(map.setPixelRatio.mock.calls).toEqual([[1],[1.5]]);
  });
  it('keeps an uncaptured native gesture at its original ratio instead of stopping it',()=>{
    const {map,move,controller}=target();move(true);
    controller.moveStart({type:'touchmove'});controller.beforeWheel();controller.pointerDown(7);
    expect(controller.restore()).toBe(false);expect(map.setPixelRatio).not.toHaveBeenCalled();
    move(false);controller.pointerUp(7);controller.restore();expect(map.setPixelRatio).not.toHaveBeenCalled();
  });
  it('avoids every allocation on devices already at DPR one or below',()=>{
    for(const dpr of [1,.8]){
      const {map,move,controller}=target(dpr);
      controller.beforeWheel();controller.pointerDown(1);controller.pointerUp(1);move(true);controller.moveStart();
      move(false);controller.restore();controller.restore();expect(map.setPixelRatio).not.toHaveBeenCalled();
    }
  });
  it('guards synchronous resize movement events, including the restore path',()=>{
    let ratio=1.5,moving=true;const during:boolean[]=[];
    const map={getPixelRatio:()=>ratio,isMoving:()=>moving,setPixelRatio:vi.fn((value:number)=>{
      ratio=value;during.push(controller.applying);
      // Public setPixelRatio emits resize/movement events synchronously. Even
      // a direct callback must not re-enter it or undo the current transition.
      controller.moveStart();controller.restore();
    })};
    const controller=createMap2DPixelRatioController(map,()=>2);
    controller.moveStart();moving=false;controller.restore();
    expect(map.setPixelRatio.mock.calls).toEqual([[1],[1.5]]);expect(during).toEqual([true,true]);
    expect(controller.applying).toBe(false);
  });
  it('reads a changed device ratio at rest and releases held inputs on hide or blur',()=>{
    let dpr=2,ratio=1.5;
    const map={getPixelRatio:()=>ratio,isMoving:()=>false,setPixelRatio:vi.fn((value:number)=>{ratio=value;})};
    const controller=createMap2DPixelRatioController(map,()=>dpr);
    controller.pointerDown(4);dpr=1.25;expect(controller.restore()).toBe(false);
    controller.releasePointers();expect(controller.inputHeld).toBe(false);controller.restore();
    expect(map.setPixelRatio.mock.calls).toEqual([[1],[1.25]]);
  });
  it('clears the reentry guard after an engine exception and ignores all work after disposal',()=>{
    const {map,move,controller}=target();move(true);
    map.setPixelRatio.mockImplementationOnce(()=>{throw new Error('resize failed');});
    expect(()=>controller.moveStart()).toThrow('resize failed');expect(controller.applying).toBe(false);
    controller.moveStart();expect(map.getPixelRatio()).toBe(1);
    controller.pointerDown(11);controller.dispose();move(false);
    controller.pointerDown(12);controller.beforeWheel();controller.moveStart();controller.restore();
    expect(controller.inputHeld).toBe(false);expect(map.setPixelRatio).toHaveBeenCalledTimes(2);
  });
  it('keeps independent maps and pointer ownership isolated',()=>{
    const a=target(),b=target();a.controller.pointerDown(1);
    expect(a.map.getPixelRatio()).toBe(1);expect(b.map.getPixelRatio()).toBe(1.5);
    expect(b.controller.pointerUp(1)).toBe(false);b.controller.dispose();
    expect(a.controller.inputHeld).toBe(true);a.controller.pointerUp(1);a.controller.restore();
    expect(a.map.getPixelRatio()).toBe(1.5);
  });
});
