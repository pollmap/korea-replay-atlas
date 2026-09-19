import {afterEach,describe,expect,it,vi} from 'vitest';
import {compileGeometry,resolveFeatureProperties,type GeoCollection,type RenderGeometry} from '../shared/geometry';
import type {GeometryRequest} from '../src/geometry.worker';

const source:GeoCollection={type:'FeatureCollection',metadata:{schema_version:1,shared:{source_id:'osm',description:'전송할 원본 설명',evidence_type:'source_attribute'},rows:[{source_record_id:'node/one',name:'원본 역',quality_flags:['original_flag']}]},features:[{type:'Feature',properties:{metadata_index:0,kind:'station'},geometry:{type:'Point',coordinates:[127.123456789,36.123456789,-2.5]}}]};
afterEach(()=>{vi.unstubAllGlobals();vi.resetModules();});
describe('the geometry worker transport',()=>{
  it('sends the dictionary once and transfers source coordinate ownership without UI expansion',async()=>{
    let complete!:(result:{id:number;geometry:RenderGeometry})=>void;
    const received=new Promise<{id:number;geometry:RenderGeometry}>(resolve=>{complete=resolve;});
    const posted=vi.fn((result:{id:number;geometry:RenderGeometry},options:StructuredSerializeOptions)=>{
      const cloned=structuredClone(result,options);expect(result.geometry.coordinates.byteLength).toBe(0);complete(cloned);
    });
    const scope={onmessage:undefined as ((event:MessageEvent<GeometryRequest>)=>void)|undefined,postMessage:posted};
    vi.stubGlobal('self',scope);vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>structuredClone(source)})));
    await import('../src/geometry.worker');scope.onmessage!({data:{id:7,url:'/data/source.geojson',omitRoads:false}} as MessageEvent<GeometryRequest>);
    const {id,geometry}=await received,expanded=compileGeometry(source);
    expect(id).toBe(7);expect(posted).toHaveBeenCalledTimes(1);expect(geometry.metadata).toEqual(source.metadata);
    expect(geometry.features[0].properties).toEqual({metadata_index:0,kind:'station'});
    expect(resolveFeatureProperties(geometry.features[0],geometry.metadata)).toEqual(expanded.features[0].properties);
    expect(geometry.coordinates).toEqual(expanded.coordinates);expect(geometry.parts).toEqual(expanded.parts);
  });

  it('does not transfer decoded metadata for a cancelled request',async()=>{
    let finish!:(value:GeoCollection)=>void,signal:AbortSignal|undefined;
    const decoded=new Promise<GeoCollection>(resolve=>{finish=resolve;});
    const scope={onmessage:undefined as ((event:MessageEvent<GeometryRequest>)=>void)|undefined,postMessage:vi.fn()};
    vi.stubGlobal('self',scope);vi.stubGlobal('fetch',vi.fn(async(_url:string,options:{signal:AbortSignal})=>{signal=options.signal;return {ok:true,json:()=>decoded};}));
    await import('../src/geometry.worker');scope.onmessage!({data:{id:9,url:'/data/source.geojson',omitRoads:false}} as MessageEvent<GeometryRequest>);
    await Promise.resolve();scope.onmessage!({data:{id:9,cancel:true}} as MessageEvent<GeometryRequest>);
    expect(signal?.aborted).toBe(true);finish(source);await new Promise(resolve=>setImmediate(resolve));expect(scope.postMessage).not.toHaveBeenCalled();
  });
});
