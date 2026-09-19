import {afterAll,beforeAll,describe,it,expect,vi} from 'vitest';
import * as C from 'cesium';
import {buildPrimitives,PrimitivePickRegistry,selectionFromProperties,type PrimitivePick} from '../src/primitive-renderer';
import {compileGeometry,type GeoCollection} from '../shared/geometry';
import {FrameWorkBudget} from '../shared/map-performance';
import {PrimitiveAdmission} from '../shared/primitive-admission';
import type {Asset} from '../shared/contracts';
const asset:Asset={id:'tile',layer:'infrastructure',format:'geojson',url:'/data/tile.geojson',bbox:[126,35,128,37],source_id:'osm',version:'snapshot',count:2,sha256:'hash'};
const fixture:GeoCollection={type:'FeatureCollection',features:[{type:'Feature',id:'road-original',properties:{name:'도로',kind:'road',source_record_id:'way/10',description:'원문'},geometry:{type:'LineString',coordinates:[[127,36],[127.1,36.1]]}},{type:'Feature',id:'land-original',properties:{name:'산업용지',kind:'industrial_land'},geometry:{type:'Polygon',coordinates:[[[127,36],[127.1,36],[127.1,36.1],[127,36]]]}}]};
// Geometry-construction test only: a real Viewer initializes these WebGL limits.
const limits=(C as unknown as {ContextLimits:{_minimumAliasedLineWidth:number;_maximumAliasedLineWidth:number}}).ContextLimits;
const previous={...limits};
beforeAll(()=>{limits._minimumAliasedLineWidth=1;limits._maximumAliasedLineWidth=1;});
afterAll(()=>Object.assign(limits,previous));
describe('static primitive batches',()=>{
  it('defers complete dictionary attributes until selection and reuses them after cache reactivation',async()=>{
    let detailReads=0;
    const source:GeoCollection={type:'FeatureCollection',metadata:{schema_version:1,shared:{source_id:'official',dataset_version:'source-version',kind:'road',evidence_type:'source_attribute',quality_flags:['source_flag'],get description(){detailReads++;return '긴 원본 상세 정보';}},rows:[{source_record_id:'way/dictionary',class:'primary'}]},features:[{...fixture.features[0],id:undefined,properties:{metadata_index:0,name:'원본 이름'}}]};
    const encoded=compileGeometry(source,false,{preserveMetadata:true}),picks=new PrimitivePickRegistry();
    const viewer={scene:{globe:{getHeight:()=>0}},isDestroyed:()=>false} as unknown as C.Viewer;
    const bundle=await buildPrimitives(encoded,asset,viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap:picks});
    const key=String((bundle.root.get(2).geometryInstances as C.GeometryInstance[])[0].id),pick=picks.get(key)!;
    expect(detailReads).toBe(0);expect(pick.sourceId).toBe('way/dictionary');expect(picks.has(key)).toBe(true);expect(detailReads).toBe(0);
    const properties=pick.properties;
    expect(detailReads).toBe(1);expect(properties).toMatchObject({description:'긴 원본 상세 정보',source_id:'official',source_record_id:'way/dictionary',quality_flags:['source_flag'],name:'원본 이름'});expect(properties).not.toHaveProperty('metadata_index');
    expect(selectionFromProperties(pick.sourceId,properties,pick.asset)).toMatchObject({name:'원본 이름',detail:'긴 원본 상세 정보',provenance:{source_id:'official',source_record_id:'way/dictionary',dataset_version:'source-version'}});
    bundle.setActive(false);expect(picks.get(key)).toBeUndefined();bundle.setActive(true);
    expect(picks.get(key)).toBe(pick);expect(pick.properties).toBe(properties);expect(detailReads).toBe(1);bundle.destroy();expect(picks.size).toBe(0);
  });
  it('builds identical source shapes, colors and verified heights from compact or expanded attributes',async()=>{
    const source:GeoCollection={type:'FeatureCollection',metadata:{schema_version:1,shared:{source_id:'osm',height:999,render_eligible:false,render_height:null,raw_height:.01,quality_flags:['source_height_below_1m_review']},rows:[{source_record_id:'way/review',kind:'building',base_height:-2.5},{source_record_id:'way/verified',kind:'building',base_height:7,min_height:5,render_min_height:3,render_eligible:true,render_height:18}]},features:[{...fixture.features[1],id:'review',properties:{metadata_index:0,name:'검토 외곽'}},{...fixture.features[1],id:'verified',properties:{metadata_index:1,name:'확인 높이'}}]};
    const viewer={scene:{globe:{getHeight:()=>0}},isDestroyed:()=>false} as unknown as C.Viewer,bundles=[];
    for(const preserveMetadata of [false,true])bundles.push(await buildPrimitives(compileGeometry(source,false,{preserveMetadata}),{...asset,layer:'buildings'},viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap:new PrimitivePickRegistry()}));
    try{
      expect(bundles[0].root.length).toBe(4);expect(bundles[1].root.length).toBe(4);
      expect(bundles[1].root.get(2)).toBeInstanceOf(C.GroundPolylinePrimitive);expect(bundles[1].root.get(3)).toBeInstanceOf(C.Primitive);
      for(let i=2;i<4;i++){
        const before=bundles[0].root.get(i).geometryInstances as C.GeometryInstance[],after=bundles[1].root.get(i).geometryInstances as C.GeometryInstance[];
        expect(after.map(instance=>instance.geometry)).toEqual(before.map(instance=>instance.geometry));
        expect(after.map(instance=>instance.attributes)).toEqual(before.map(instance=>instance.attributes));
      }
      expect(bundles[1].featureCount).toBe(bundles[0].featureCount);expect(bundles[1].vertexCount).toBe(bundles[0].vertexCount);
    }finally{for(const bundle of bundles)bundle.destroy();}
  });
  it('builds real Cesium geometry batches without Entity allocation and preserves pick provenance',async()=>{
    const pickMap=new Map<string,PrimitivePick>(),controller=new AbortController();
    const viewer={scene:{globe:{getHeight:()=>20}},isDestroyed:()=>false} as unknown as C.Viewer;
    const bundle=await buildPrimitives(compileGeometry(fixture),asset,viewer,{signal:controller.signal,budget:new FrameWorkBudget(()=>1,async()=>{}),labelLimit:0,pickMap});
    expect(bundle.featureCount).toBe(2);expect(bundle.vertexCount).toBe(6);expect(pickMap.size).toBe(2);
    const pick=[...pickMap.values()][0];
    expect(Object.keys(pick).sort()).toEqual(['asset','properties','sourceId']);
    expect(selectionFromProperties(pick.sourceId,pick.properties,pick.asset)).toMatchObject({sourceId:'road-original',detail:'원문',properties:{source_record_id:'way/10'},provenance:{source_record_id:'way/10',source_id:'osm',dataset_version:'snapshot'}});
    expect(bundle.root.length).toBe(4); // Point/label collections plus ground line and polygon batches.
    bundle.destroy();expect(pickMap.size).toBe(0);expect(bundle.root.isDestroyed()).toBe(true);
  });
  it('removes metadata and partial batches when cancelled',async()=>{
    const pickMap=new Map<string,PrimitivePick>(),controller=new AbortController();controller.abort();
    const viewer={scene:{globe:{getHeight:()=>0}}} as unknown as C.Viewer;
    await expect(buildPrimitives(compileGeometry(fixture),asset,viewer,{signal:controller.signal,budget:new FrameWorkBudget(()=>1,async()=>{}),labelLimit:0,pickMap})).rejects.toMatchObject({name:'AbortError'});
    expect(pickMap.size).toBe(0);
  });
  it('preserves supplied provenance rather than replacing it with inferred height data',()=>{
    const provenance={source_id:'osm',source_record_id:'way/99',evidence_type:'unverified'};
    const selected=selectionFromProperties('original',{name:'시설',height_m:null,provenance},asset);
    expect(selected.height).toBeUndefined();expect(selected.provenance).toBe(provenance);
  });
  it('keeps an unresolved positive raw height as an outline and exposes its review reason',async()=>{
    const feature={...fixture.features[1],properties:{name:'검토 건물',height:.01,raw_height:.01,render_height:null,render_eligible:false,quality_state:'review_required',quality_flags:['source_height_below_1m_review'],source_record_id:'source-building'}};
    const pickMap=new Map<string,PrimitivePick>(),viewer={scene:{globe:{getHeight:()=>20}},isDestroyed:()=>false} as unknown as C.Viewer;
    const bundle=await buildPrimitives(compileGeometry({type:'FeatureCollection',features:[feature]}),{...asset,layer:'buildings'},viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>1,async()=>{}),labelLimit:0,pickMap});
    expect(bundle.root.length).toBe(3);expect(bundle.root.get(2)).toBeInstanceOf(C.GroundPolylinePrimitive);
    const pick=[...pickMap.values()][0],selected=selectionFromProperties(pick.sourceId,pick.properties,pick.asset);expect(selected.height).toBeUndefined();expect(selected.rawHeight).toBe(.01);expect(selected.qualityFlags).toEqual(['source_height_below_1m_review']);
    bundle.destroy();
  });
  it('reads the string-encoded GLB quality metadata without replacing empty heights with zero',()=>{
    const selected=selectionFromProperties('source-building',{height:18,render_height:'18.0',raw_height:'18.0',render_eligible:'True',quality_flags:'["upstream_schema_semantics_conflict"]',height_semantics:'ground_to_top',quality_state:'verified_semantics'},asset);
    expect(selected).toMatchObject({height:18,rawHeight:18,heightSemantics:'ground_to_top',qualityFlags:['upstream_schema_semantics_conflict']});
    expect(selectionFromProperties('unknown',{height:9,render_height:'',render_eligible:'False',raw_height:'',quality_state:'review_required'},asset)).toMatchObject({height:undefined,rawHeight:null});
  });
  it('falls back through blank glTF string metadata without losing source identifiers',()=>{
    expect(selectionFromProperties('building-1',{height_source:'',source_id:'official-buildings',source_record_id:'',dataset_version:''},asset).provenance).toMatchObject({source_id:'official-buildings',source_record_id:'building-1',dataset_version:'snapshot'});
    expect(selectionFromProperties('building-2',{height_source:'  ',source_id:''},asset).provenance?.source_id).toBe('osm');
    expect(selectionFromProperties('building-3',{height_source:'ghsl',source_id:'osm'},asset).provenance?.dataset_version).toBe('2018 / R2023A');
    expect(selectionFromProperties('building-4',{source_record_id:123},asset).provenance?.source_record_id).toBe('123');
  });
  it('splits real terrain refreshes into shared single-point jobs and yields before the first sample',async()=>{
    let now=0,height=0,samples=0,sinceYield=0,peakSamples=0;
    const budget=new FrameWorkBudget(()=>now,async()=>{peakSamples=Math.max(peakSamples,sinceYield);sinceYield=0;now+=16;},4);
    const viewer={scene:{globe:{getHeight:()=>{samples++;sinceYield++;now+=3;return height;}},requestRender:()=>{}},isDestroyed:()=>false} as unknown as C.Viewer;
    const pointData=compileGeometry({type:'FeatureCollection',features:Array.from({length:20},(_,i)=>({type:'Feature',id:`point-${i}`,properties:{kind:'facility'},geometry:{type:'Point',coordinates:[127+i*.001,36]}}))});
    const controller=new AbortController();
    const bundles=await Promise.all([0,1,2].map(i=>buildPrimitives(pointData,{...asset,id:`tile-${i}`},viewer,{signal:controller.signal,budget,labelLimit:0,pickMap:new Map()})));
    expect(samples).toBe(60);height=15;
    for(const bundle of bundles)bundle.refreshGround();
    expect(samples).toBe(60);
    await new Promise(resolve=>setImmediate(resolve));
    expect(samples).toBe(120);expect(Math.max(peakSamples,sinceYield)).toBeLessThanOrEqual(2);
    expect(budget.snapshot()).toMatchObject({chunkMaxMs:6,operationMaxMs:3});
    for(const bundle of bundles){const position=bundle.root.get(0).get(0).position;expect(C.Cartographic.fromCartesian(position).height).toBeCloseTo(15,5);}
    for(const bundle of bundles){bundle.refreshGround();bundle.setActive(false);}controller.abort();
    await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(120);
    for(const bundle of bundles)bundle.destroy();
  });
  it('splits long lines with shared endpoints while preserving all source vertices and the pick ID',async()=>{
    const coordinates=Array.from({length:1400},(_,i)=>[127+i*.00001,36+i*.00001]);
    const data=compileGeometry({type:'FeatureCollection',features:[{type:'Feature',id:'long-line',properties:{kind:'road'},geometry:{type:'LineString',coordinates}}]});
    const pickMap=new Map<string,PrimitivePick>(),viewer={scene:{globe:{getHeight:()=>0}},isDestroyed:()=>false} as unknown as C.Viewer;
    const bundle=await buildPrimitives(data,{...asset,vertex_count:coordinates.length},viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap});
    const instances=bundle.root.get(2).geometryInstances as C.GeometryInstance[];
    expect(instances).toHaveLength(3);expect(new Set(instances.map(instance=>instance.id)).size).toBe(1);expect(pickMap.size).toBe(1);
    const lines=instances.map(instance=>(instance.geometry as unknown as {_positions:C.Cartesian3[]})._positions);
    expect(lines.every(line=>line.length<=513)).toBe(true);expect(C.Cartesian3.equals(lines[0].at(-1)!,lines[1][0])).toBe(true);
    const reconstructed=lines.flatMap((line,index)=>index?line.slice(1):line);
    expect(reconstructed).toHaveLength(coordinates.length);
    for(let i=0;i<coordinates.length;i++)expect(C.Cartesian3.equals(reconstructed[i],C.Cartesian3.fromDegrees(...coordinates[i] as [number,number]))).toBe(true);
    bundle.destroy();
  });
  it('keeps same-asset variants distinct and unregisters dormant picks without altering source properties',async()=>{
    const pickMap=new PrimitivePickRegistry(),viewer={scene:{globe:{getHeight:()=>0}},isDestroyed:()=>false} as unknown as C.Viewer;
    const options={signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap};
    const first=await buildPrimitives(compileGeometry(fixture),asset,viewer,options);
    const second=await buildPrimitives(compileGeometry(fixture),asset,viewer,options);
    const firstId=(first.root.get(2).geometryInstances as C.GeometryInstance[])[0].id as string;
    const secondId=(second.root.get(2).geometryInstances as C.GeometryInstance[])[0].id as string;
    expect(firstId).not.toBe(secondId);expect(pickMap.size).toBe(4);
    const original=pickMap.get(firstId)!;expect(original.properties.source_record_id).toBe('way/10');
    first.setActive(false);expect(first.root.show).toBe(false);expect(pickMap.has(firstId)).toBe(false);expect(pickMap.has(secondId)).toBe(true);expect(pickMap.size).toBe(2);
    first.setActive(true);expect(first.root.show).toBe(true);expect(pickMap.get(firstId)).toBe(original);expect(pickMap.size).toBe(4);
    second.setActive(false);second.destroy();expect(pickMap.get(firstId)).toBe(original);expect(pickMap.size).toBe(2);
    first.destroy();expect(pickMap.size).toBe(0);
  });
  it('does no terrain work while dormant and reuses terrain placement until its revision changes',async()=>{
    let samples=0,height=12;
    const pickMap=new PrimitivePickRegistry(),controller=new AbortController();
    const viewer={scene:{globe:{getHeight:()=>{samples++;return height;}},requestRender:()=>{}},isDestroyed:()=>false} as unknown as C.Viewer;
    const data=compileGeometry({type:'FeatureCollection',features:[{type:'Feature',id:'point-original',properties:{kind:'facility',source_record_id:'node/1'},geometry:{type:'Point',coordinates:[127,36]}}]});
    const bundle=await buildPrimitives(data,asset,viewer,{signal:controller.signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap,groundRevision:1});
    const point=bundle.root.get(0).get(0) as C.PointPrimitive;
    expect(samples).toBe(1);bundle.refreshGround(1);expect(samples).toBe(1);
    bundle.refreshGround(2);bundle.setActive(false);controller.abort();bundle.refreshGround(3);
    await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(1);expect(pickMap.size).toBe(0);
    height=31;bundle.setActive(true);bundle.refreshGround(3);bundle.refreshGround(4);
    await new Promise(resolve=>setImmediate(resolve));
    expect(bundle.root.get(0).get(0)).toBe(point);expect(C.Cartographic.fromCartesian(point.position).height).toBeCloseTo(31,5);expect(pickMap.size).toBe(1);
    const afterRestore=samples;expect(afterRestore).toBeGreaterThan(1);
    bundle.refreshGround(4);await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(afterRestore);
    bundle.setActive(false);bundle.setActive(true);bundle.refreshGround(4);
    await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(afterRestore);
    bundle.destroy();expect(pickMap.size).toBe(0);
  });
  it('cancels terrain refresh during camera motion and resumes only the latest settled revision without hiding points',async()=>{
    let samples=0,height=12;
    const viewer={scene:{globe:{getHeight:()=>{samples++;return height;}},requestRender:()=>{}},isDestroyed:()=>false} as unknown as C.Viewer;
    const data=compileGeometry({type:'FeatureCollection',features:[{type:'Feature',id:'point',properties:{kind:'facility'},geometry:{type:'Point',coordinates:[127,36]}}]});
    const picks=new PrimitivePickRegistry();
    const bundle=await buildPrimitives(data,asset,viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap:picks,groundRevision:1});
    const original=bundle.root.get(0).get(0).position.clone();
    bundle.refreshGround(2);bundle.setGroundRefreshPaused(true);height=30;
    for(let revision=3;revision<=8;revision++)bundle.refreshGround(revision);
    await new Promise(resolve=>setImmediate(resolve));
    expect(samples).toBe(1);expect(bundle.root.show).toBe(true);expect(picks.size).toBe(1);expect(C.Cartesian3.equals(bundle.root.get(0).get(0).position,original)).toBe(true);
    bundle.setGroundRefreshPaused(false);bundle.refreshGround(8);
    await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(2);expect(C.Cartographic.fromCartesian(bundle.root.get(0).get(0).position).height).toBeCloseTo(30,5);
    bundle.refreshGround(8);await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(2);bundle.destroy();
  });
  it('rejects understated vertex metadata before allocating scene objects or sampling terrain',async()=>{
    let samples=0;const viewer={scene:{globe:{getHeight:()=>{samples++;return 0;}}}} as unknown as C.Viewer;
    const picks=new PrimitivePickRegistry();
    await expect(buildPrimitives(compileGeometry(fixture),{...asset,vertex_count:5},viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(),labelLimit:0,pickMap:picks})).rejects.toThrow('정점 예산 불일치');
    expect(samples).toBe(0);expect(picks.size).toBe(0);
  });
  it('keeps source height while initial sampling is paused and completes that same revision after settling',async()=>{
    let samples=0;
    const viewer={scene:{globe:{getHeight:()=>{samples++;return 42;}},requestRender:()=>{}},isDestroyed:()=>false} as unknown as C.Viewer;
    const data=compileGeometry({type:'FeatureCollection',features:[{type:'Feature',id:'point',properties:{kind:'facility',source_height:-2.75},geometry:{type:'Point',coordinates:[127,36,-2.75]}}]});
    const originalCoordinates=Array.from(data.coordinates),picks=new PrimitivePickRegistry();
    const bundle=await buildPrimitives(data,asset,viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap:picks,groundRevision:7,groundRefreshPaused:true});
    const point=bundle.root.get(0).get(0) as C.PointPrimitive;
    expect(samples).toBe(0);expect(C.Cartographic.fromCartesian(point.position).height).toBeCloseTo(-2.75,5);expect(bundle.root.show).toBe(true);expect(picks.size).toBe(1);
    bundle.refreshGround(7);await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(0);
    bundle.setGroundRefreshPaused(false);bundle.refreshGround(7);await new Promise(resolve=>setImmediate(resolve));
    expect(samples).toBe(1);expect(bundle.root.get(0).get(0)).toBe(point);expect(point.show).toBe(true);expect(C.Cartographic.fromCartesian(point.position).height).toBeCloseTo(42,5);
    expect(Array.from(data.coordinates)).toEqual(originalCoordinates);expect(picks.get(point.id)!.properties.source_height).toBe(-2.75);
    bundle.refreshGround(7);await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(1);bundle.destroy();
  });
  it('checks the latest motion gate for every initial point and samples only unfinished points on resume',async()=>{
    let samples=0,paused=false;
    const viewer={scene:{globe:{getHeight:()=>{samples++;paused=true;return 30;}},requestRender:()=>{}},isDestroyed:()=>false} as unknown as C.Viewer;
    const data=compileGeometry({type:'FeatureCollection',features:[0,1].map(i=>({type:'Feature',id:`point-${i}`,properties:{kind:'facility'},geometry:{type:'Point',coordinates:[127+i*.001,36,8]}}))});
    const bundle=await buildPrimitives(data,asset,viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap:new PrimitivePickRegistry(),groundRevision:3,groundRefreshPaused:false,groundState:()=>({revision:3,paused})});
    const first=bundle.root.get(0).get(0),second=bundle.root.get(0).get(1),firstPosition=first.position.clone();
    expect(samples).toBe(1);expect(C.Cartographic.fromCartesian(first.position).height).toBeCloseTo(30,5);expect(C.Cartographic.fromCartesian(second.position).height).toBeCloseTo(8,5);
    bundle.setGroundRefreshPaused(true);bundle.refreshGround(3);await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(1);
    paused=false;bundle.setGroundRefreshPaused(false);bundle.refreshGround(3);await new Promise(resolve=>setImmediate(resolve));
    expect(samples).toBe(2);expect(C.Cartesian3.equals(first.position,firstPosition)).toBe(true);expect(C.Cartographic.fromCartesian(second.position).height).toBeCloseTo(30,5);expect(first.show&&second.show&&bundle.root.show).toBe(true);bundle.destroy();
  });
  it('retries an unavailable initial terrain height in the same revision without an automatic retry loop',async()=>{
    let samples=0,height:number|undefined=undefined;
    const viewer={scene:{globe:{getHeight:()=>{samples++;return height;}},requestRender:()=>{}},isDestroyed:()=>false} as unknown as C.Viewer;
    const data=compileGeometry({type:'FeatureCollection',features:[{type:'Feature',id:'point',properties:{kind:'facility'},geometry:{type:'Point',coordinates:[127,36,9]}}]});
    const bundle=await buildPrimitives(data,asset,viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap:new PrimitivePickRegistry(),groundRevision:4});
    const point=bundle.root.get(0).get(0),before=point.position.clone();expect(samples).toBe(1);
    bundle.refreshGround(4);await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(2);expect(C.Cartesian3.equals(point.position,before)).toBe(true);
    height=18;bundle.refreshGround(4);await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(3);expect(C.Cartographic.fromCartesian(point.position).height).toBeCloseTo(18,5);expect(point.show&&bundle.root.show).toBe(true);
    bundle.refreshGround(4);await new Promise(resolve=>setImmediate(resolve));expect(samples).toBe(3);bundle.destroy();
  });
});

describe('progressive scene ownership',()=>{
  const roads=(count:number):GeoCollection['features']=>Array.from({length:count},(_,i)=>({type:'Feature',id:`road-${i}`,properties:{kind:'road',source_record_id:`way/${i}`},geometry:{type:'LineString',coordinates:[[127+i*.00001,36],[127+i*.00001,36.001]]}}));
  const point=(id:string):GeoCollection['features'][number]=>({type:'Feature',id,properties:{kind:'station',name:id,source_record_id:`node/${id}`},geometry:{type:'Point',coordinates:[127,36,7]}});
  const turn=()=>new Promise<void>(resolve=>setImmediate(resolve));

  it('stages once, exposes complete pick metadata, and defers every batch while points and labels stay hidden',async()=>{
    // Label construction measures CSS fonts; GPU glyph creation is not part of this test.
    const css:Record<string,string>={'line-height':'normal','font-family':'sans-serif','font-size':'12px','font-style':'normal','font-weight':'normal'};
    vi.stubGlobal('document',{createElement:()=>({style:{}}),body:{appendChild:()=>{},removeChild:()=>{}},defaultView:{getComputedStyle:()=>({getPropertyValue:(name:string)=>css[name]??''})}});
    const data=compileGeometry({type:'FeatureCollection',features:[point('first-point'),...roads(129),fixture.features[1],point('last-point')]}),coordinates=Array.from(data.coordinates);
    const owner=new C.PrimitiveCollection({destroyPrimitives:false}),picks=new PrimitivePickRegistry(),controller=new AbortController(),resume:(()=>void)[]=[];
    const requestRender=vi.fn(),viewer={scene:{frameState:{mode:C.SceneMode.SCENE3D},globe:{getHeight:()=>20},requestRender},isDestroyed:()=>false} as unknown as C.Viewer;
    const budget=new FrameWorkBudget(()=>0,()=>new Promise<void>(resolve=>resume.push(resolve)));
    let root:C.PrimitiveCollection|undefined,resolveStage!:()=>void;
    const staged=new Promise<void>(resolve=>{resolveStage=resolve;});
    const cleanup=vi.fn(()=>{if(root)owner.remove(root);});
    const stageRoot=vi.fn((value:C.PrimitiveCollection)=>{root=value;owner.add(value);resolveStage();return cleanup;});
    const building=buildPrimitives(data,{...asset,count:data.features.length,vertex_count:data.vertexCount},viewer,{signal:controller.signal,budget,labelLimit:2,pickMap:picks,stageRoot});
    let bundle:Awaited<typeof building>|undefined;
    try{
      await Promise.race([staged,building]);await turn();
      expect(stageRoot).toHaveBeenCalledTimes(1);expect(root).toBeDefined();expect(owner.contains(root!)).toBe(true);
      expect(root!.length).toBe(3);expect(picks.size).toBe(data.features.length);
      const firstBatch=root!.get(2) as C.GroundPolylinePrimitive,firstInstances=firstBatch.geometryInstances as C.GeometryInstance[];
      expect(firstInstances).toHaveLength(128);
      const original=picks.get(String(firstInstances[0].id))!;
      expect(selectionFromProperties(original.sourceId,original.properties,original.asset)).toMatchObject({sourceId:'road-0',provenance:{source_record_id:'way/0',source_id:'osm'}});
      const points=root!.get(0) as C.PointPrimitiveCollection,labels=root!.get(1) as C.LabelCollection;
      expect(points.length).toBe(1);expect(labels.length).toBe(1);expect(points.show).toBe(false);expect(labels.show).toBe(false);
      expect(resume).toHaveLength(1);expect(requestRender).toHaveBeenCalledTimes(1);
      resume.shift()!();await turn();
      expect(stageRoot).toHaveBeenCalledTimes(1);expect(root!.get(2)).toBe(firstBatch);expect(root!.length).toBe(5);
      expect(points.length).toBe(2);expect(labels.length).toBe(2);expect(points.show).toBe(false);expect(labels.show).toBe(false);
      expect(resume).toHaveLength(1);expect(requestRender).toHaveBeenCalledTimes(2);
      resume.shift()!();bundle=await building;
      expect(bundle.root).toBe(root);expect(bundle.featureCount).toBe(data.features.length);expect(bundle.vertexCount).toBe(data.vertexCount);
      expect(points.show).toBe(true);expect(labels.show).toBe(true);expect(requestRender).toHaveBeenCalledTimes(3);
      expect(cleanup).not.toHaveBeenCalled();expect(owner.contains(root!)).toBe(true);expect(picks.size).toBe(data.features.length);
      const secondInstances=(root!.get(3) as C.GroundPolylinePrimitive).geometryInstances as C.GeometryInstance[];
      expect(secondInstances).toHaveLength(1);
      const instances=[...firstInstances,...secondInstances];
      expect(instances.map(instance=>picks.get(String(instance.id))!.sourceId)).toEqual(roads(129).map(feature=>feature.id));
      for(let i=0;i<instances.length;i++){
        const positions=(instances[i].geometry as unknown as {_positions:C.Cartesian3[]})._positions;
        expect(C.Cartesian3.equals(positions[0],C.Cartesian3.fromDegrees(127+i*.00001,36))).toBe(true);
        expect(C.Cartesian3.equals(positions[1],C.Cartesian3.fromDegrees(127+i*.00001,36.001))).toBe(true);
      }
      expect(picks.get(String(points.get(1).id))!.sourceId).toBe('last-point');expect(Array.from(data.coordinates)).toEqual(coordinates);
      owner.remove(root!);bundle.destroy();expect(cleanup).not.toHaveBeenCalled();expect(picks.size).toBe(0);
    }finally{
      controller.abort();for(const release of resume.splice(0))release();
      await building.catch(()=>undefined);if(root)owner.remove(root);bundle?.destroy();owner.destroy();vi.unstubAllGlobals();
    }
  });

  it('detaches cancelled staged roots before destruction and never destroys them twice when the owner removes them',async()=>{
    const owner=new C.PrimitiveCollection(),picks=new PrimitivePickRegistry(),controller=new AbortController(),events:string[]=[];
    const requestRender=vi.fn(),viewer={scene:{globe:{getHeight:()=>0},requestRender},isDestroyed:()=>false} as unknown as C.Viewer;
    let root:C.PrimitiveCollection|undefined,release!:()=>void,resolveStage!:()=>void,destroyCalls=0;
    const staged=new Promise<void>(resolve=>{resolveStage=resolve;});
    const budget=new FrameWorkBudget(()=>0,()=>new Promise<void>(resolve=>{release=resolve;}));
    const stageRoot=vi.fn((value:C.PrimitiveCollection)=>{
      root=value;owner.add(root);events.push('attach');
      const destroy=root.destroy.bind(root);
      root.destroy=()=>{destroyCalls++;events.push('destroy');expect(owner.contains(root!)).toBe(false);return destroy();};
      resolveStage();
      return()=>{events.push('detach');expect(picks.size).toBe(0);expect(root!.isDestroyed()).toBe(false);expect(owner.remove(root!)).toBe(true);};
    });
    const building=buildPrimitives(compileGeometry(fixture),asset,viewer,{signal:controller.signal,budget,labelLimit:0,pickMap:picks,stageRoot});
    await Promise.race([staged,building]);await turn();
    expect(owner.length).toBe(1);expect(picks.size).toBe(2);expect(root!.get(0).show).toBe(false);expect(root!.get(1).show).toBe(false);
    controller.abort();release();
    await expect(building).rejects.toMatchObject({name:'AbortError'});
    expect(events).toEqual(['attach','detach','destroy']);expect(destroyCalls).toBe(1);expect(owner.length).toBe(0);expect(picks.size).toBe(0);
    expect(root!.isDestroyed()).toBe(true);expect(stageRoot).toHaveBeenCalledTimes(1);expect(requestRender).toHaveBeenCalledTimes(1);owner.destroy();
  });

  it('cleans up a partial scene and registered picks when construction fails after the first batch',async()=>{
    const owner=new C.PrimitiveCollection({destroyPrimitives:false}),picks=new PrimitivePickRegistry(),requestRender=vi.fn();
    const viewer={scene:{globe:{getHeight:()=>{throw new Error('height lookup failed');}},requestRender},isDestroyed:()=>false} as unknown as C.Viewer;
    const data=compileGeometry({type:'FeatureCollection',features:[...roads(128),point('failed-point')]});
    let root:C.PrimitiveCollection|undefined;
    const cleanup=vi.fn(()=>{expect(picks.size).toBe(0);expect(root!.isDestroyed()).toBe(false);expect(root!.get(0).show).toBe(false);owner.remove(root!);});
    const stageRoot=vi.fn((value:C.PrimitiveCollection)=>{root=value;owner.add(value);return cleanup;});
    await expect(buildPrimitives(data,{...asset,count:data.features.length},viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap:picks,stageRoot})).rejects.toThrow('height lookup failed');
    expect(stageRoot).toHaveBeenCalledTimes(1);expect(cleanup).toHaveBeenCalledTimes(1);expect(owner.length).toBe(0);expect(root!.isDestroyed()).toBe(true);expect(picks.size).toBe(0);expect(requestRender).toHaveBeenCalledTimes(1);owner.destroy();
  });

  it('returns visible point-only bundles without prematurely attaching an empty geometry root',async()=>{
    const stageRoot=vi.fn(()=>vi.fn()),requestRender=vi.fn(),picks=new PrimitivePickRegistry();
    const viewer={scene:{globe:{getHeight:()=>20},requestRender},isDestroyed:()=>false} as unknown as C.Viewer;
    const data=compileGeometry({type:'FeatureCollection',features:[point('point-only')]}),coordinates=Array.from(data.coordinates);
    const bundle=await buildPrimitives(data,asset,viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap:picks,stageRoot});
    expect(stageRoot).not.toHaveBeenCalled();expect(bundle.root.length).toBe(2);expect(bundle.pointCount).toBe(1);
    expect(bundle.root.get(0).show).toBe(true);expect(bundle.root.get(1).show).toBe(true);expect(requestRender).toHaveBeenCalledTimes(1);
    expect(picks.get(String(bundle.root.get(0).get(0).id))!.sourceId).toBe('point-only');expect(Array.from(data.coordinates)).toEqual(coordinates);bundle.destroy();
  });

  it('preserves detached construction and collection order when staging is not requested',async()=>{
    const nextFrame=vi.fn(async()=>{}),requestRender=vi.fn(),picks=new PrimitivePickRegistry();
    const viewer={scene:{globe:{getHeight:()=>0},requestRender},isDestroyed:()=>false} as unknown as C.Viewer;
    const data=compileGeometry({type:'FeatureCollection',features:roads(129)});
    const bundle=await buildPrimitives(data,{...asset,count:data.features.length,vertex_count:data.vertexCount},viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,nextFrame),labelLimit:0,pickMap:picks});
    expect(nextFrame).not.toHaveBeenCalled();expect(requestRender).not.toHaveBeenCalled();expect(bundle.root.length).toBe(4);
    expect(bundle.root.get(0)).toBeInstanceOf(C.PointPrimitiveCollection);expect(bundle.root.get(1)).toBeInstanceOf(C.LabelCollection);
    expect((bundle.root.get(2).geometryInstances as C.GeometryInstance[]).length).toBe(128);expect((bundle.root.get(3).geometryInstances as C.GeometryInstance[]).length).toBe(1);
    expect(bundle.root.get(0).show).toBe(true);expect(bundle.root.get(1).show).toBe(true);expect(picks.size).toBe(129);bundle.destroy();
  });
});

describe('rendered batch admission integration',()=>{
  // These are actual Cesium collections/primitives. Only their public readiness
  // is controlled: no WebGL context, geometry worker, or GPU timing is simulated.
  const ready=(primitive:C.GroundPolylinePrimitive|C.GroundPrimitive|C.Primitive)=>{
    Object.defineProperty(primitive,'ready',{configurable:true,value:true});
  };
  const turn=()=>new Promise<void>(resolve=>setImmediate(resolve));
  const road:GeoCollection={type:'FeatureCollection',features:[fixture.features[0]]};
  function sceneFixture(limit:number,getHeight=()=>0){
    const admission=new PrimitiveAdmission(limit),owner=new C.PrimitiveCollection({destroyPrimitives:false}),picks=new PrimitivePickRegistry(),requestRender=vi.fn();
    const viewer={scene:{globe:{getHeight},requestRender},isDestroyed:()=>false} as unknown as C.Viewer;
    type Bundle=Awaited<ReturnType<typeof buildPrimitives>>;
    const jobs:{controller:AbortController;promise:Promise<Bundle>;get root():C.PrimitiveCollection|undefined;get bundle():Bundle|undefined}[]=[];
    const start=(source:GeoCollection=road,id='tile',budget=new FrameWorkBudget(()=>0,async()=>{}))=>{
      const controller=new AbortController(),data=compileGeometry(source);
      let root:C.PrimitiveCollection|undefined,bundle:Bundle|undefined;
      const cleanup=vi.fn(()=>{expect(root!.isDestroyed()).toBe(false);owner.remove(root!);});
      const stageRoot=vi.fn((value:C.PrimitiveCollection)=>{root=value;owner.add(value);return cleanup;});
      const promise=buildPrimitives(data,{...asset,id,count:data.features.length,vertex_count:data.vertexCount},viewer,{signal:controller.signal,budget,labelLimit:0,pickMap:picks,admission,stageRoot}).then(value=>{bundle=value;return value;});
      // A failing assertion must not turn cleanup's abort into an unhandled rejection.
      void promise.catch(()=>{});
      const job={controller,promise,stageRoot,cleanup,data,get root(){return root;},get bundle(){return bundle;}};
      jobs.push(job);return job;
    };
    const dispose=async()=>{
      for(const job of jobs)job.controller.abort();
      admission.dispose();await Promise.allSettled(jobs.map(job=>job.promise));
      for(const job of jobs){if(job.root)owner.remove(job.root);job.bundle?.destroy();}
      owner.destroy();
    };
    return {admission,owner,picks,requestRender,viewer,start,dispose};
  }

  it('excludes actual admission waits from CPU chunks without hiding synchronous source work',async()=>{
    let now=0;
    const budget=new FrameWorkBudget(()=>now,async()=>{now+=16;});
    const scene=sceneFixture(1,()=>{now+=3;return 0;});
    const source:GeoCollection={type:'FeatureCollection',features:[{type:'Feature',id:'point',properties:{kind:'station'},geometry:{type:'Point',coordinates:[127,36]}},...fixture.features]};
    const job=scene.start(source,'waiting-batch',budget),coordinates=Array.from(job.data.coordinates);
    try{
      await turn();expect(scene.admission.pending).toBe(1);expect(scene.admission.queued).toBe(1);
      expect(job.bundle).toBeUndefined();now+=2572;
      ready(job.root!.get(2));scene.admission.poll();const bundle=await job.promise;
      expect(budget.snapshot()).toMatchObject({chunkMaxMs:3,operationMaxMs:3});
      expect(bundle.featureCount).toBe(3);expect(scene.picks.size).toBe(3);expect(Array.from(job.data.coordinates)).toEqual(coordinates);
      expect(scene.admission.peakPending).toBe(1);expect(scene.admission.pending).toBe(1);expect(bundle.preparationReady).toBe(false);
    }finally{await scene.dispose();}
  });

  it('renders the first mixed batch before waiting for its single slot and retains every original pick',async()=>{
    const scene=sceneFixture(1),job=scene.start(fixture),coordinates=Array.from(job.data.coordinates);
    try{
      await turn();
      expect(job.stageRoot).toHaveBeenCalledTimes(1);expect(scene.owner.contains(job.root!)).toBe(true);
      expect(job.root!.length).toBe(3);expect(scene.admission.pending).toBe(1);expect(scene.admission.queued).toBe(1);
      expect(job.bundle).toBeUndefined();expect(scene.picks.size).toBe(2);
      const line=job.root!.get(2) as C.GroundPolylinePrimitive;
      expect(line).toBeInstanceOf(C.GroundPolylinePrimitive);expect(line.ready).toBe(false);
      expect(job.root!.get(0).show).toBe(false);expect(job.root!.get(1).show).toBe(false);
      const instance=(line.geometryInstances as C.GeometryInstance[])[0],pick=scene.picks.get(String(instance.id))!;
      expect(pick).toMatchObject({sourceId:'road-original',properties:{source_record_id:'way/10',description:'원문'}});
      scene.admission.poll();await turn();
      expect(job.root!.length).toBe(3);expect(scene.requestRender).toHaveBeenCalledTimes(1);
      ready(line);scene.admission.poll();
      const bundle=await job.promise,polygon=job.root!.get(3) as C.GroundPrimitive;
      expect(polygon).toBeInstanceOf(C.GroundPrimitive);expect(polygon.ready).toBe(false);
      expect(bundle.preparationReady).toBe(false);expect(job.stageRoot).toHaveBeenCalledTimes(1);expect(job.cleanup).not.toHaveBeenCalled();
      expect(scene.admission.pending).toBe(1);expect(scene.admission.queued).toBe(0);expect(scene.admission.peakPending).toBe(1);
      expect(scene.requestRender).toHaveBeenCalledTimes(3);expect(scene.owner.contains(bundle.root)).toBe(true);
      expect(scene.picks.get(String(instance.id))).toBe(pick);
      const polygonPick=scene.picks.get(String((polygon.geometryInstances as C.GeometryInstance[])[0].id))!;
      expect(polygonPick.sourceId).toBe('land-original');expect(Array.from(job.data.coordinates)).toEqual(coordinates);
      expect(bundle.featureCount).toBe(2);expect(bundle.vertexCount).toBe(job.data.vertexCount);
      ready(polygon);scene.admission.poll();expect(scene.admission.pending).toBe(0);expect(bundle.preparationReady).toBe(true);
    }finally{await scene.dispose();}
  });

  it('shares a single scene limit across independent builders and does not release slots at construction completion',async()=>{
    const scene=sceneFixture(2),first=scene.start(road,'first'),second=scene.start(road,'second'),third=scene.start(road,'third');
    try{
      const [a,b]=await Promise.all([first.promise,second.promise]);await turn();
      expect(scene.owner.length).toBe(2);expect(scene.admission.pending).toBe(2);expect(scene.admission.queued).toBe(1);
      expect(third.stageRoot).not.toHaveBeenCalled();expect(third.bundle).toBeUndefined();expect(scene.picks.size).toBe(2);
      expect(a.preparationReady).toBe(false);expect(b.preparationReady).toBe(false);
      const firstLine=a.root.get(2) as C.GroundPolylinePrimitive;
      ready(firstLine);scene.admission.poll();const c=await third.promise;
      expect(scene.owner.length).toBe(3);expect(scene.admission.pending).toBe(2);expect(scene.admission.queued).toBe(0);expect(scene.admission.peakPending).toBe(2);
      expect(scene.picks.size).toBe(3);expect(a.preparationReady).toBe(true);expect(b.preparationReady).toBe(false);expect(c.preparationReady).toBe(false);
      const ids=[a,b,c].map(bundle=>String((bundle.root.get(2).geometryInstances as C.GeometryInstance[])[0].id));
      expect(new Set(ids).size).toBe(3);expect(ids.map(id=>scene.picks.get(id)!.sourceId)).toEqual(['road-original','road-original','road-original']);
      for(const bundle of [b,c])ready(bundle.root.get(2));scene.admission.poll();
      expect(scene.admission.pending).toBe(0);expect(scene.admission.peakPending).toBe(2);
    }finally{await scene.dispose();}
  });

  it('aborts a staged builder waiting for another slot and immediately removes its picks, scene root, and reservations',async()=>{
    const scene=sceneFixture(1),job=scene.start(fixture);
    try{
      await turn();const root=job.root!,line=root.get(2) as C.GroundPolylinePrimitive;
      expect(line.ready).toBe(false);expect(scene.admission.pending).toBe(1);expect(scene.admission.queued).toBe(1);expect(scene.picks.size).toBe(2);
      const rejected=expect(job.promise).rejects.toMatchObject({name:'AbortError'});job.controller.abort();await rejected;
      expect(job.cleanup).toHaveBeenCalledTimes(1);expect(job.stageRoot).toHaveBeenCalledTimes(1);
      expect(scene.owner.length).toBe(0);expect(scene.picks.size).toBe(0);expect(root.isDestroyed()).toBe(true);expect(line.isDestroyed()).toBe(true);
      expect(scene.admission.pending).toBe(0);expect(scene.admission.queued).toBe(0);
      // No readiness fixture or post-render poll is needed to admit the next job.
      const following=scene.start();const bundle=await following.promise;
      expect(scene.owner.contains(bundle.root)).toBe(true);expect(scene.admission.pending).toBe(1);expect(scene.admission.peakPending).toBe(1);
    }finally{await scene.dispose();}
  });

  it('disposes a not-yet-ready completed bundle instead of retaining a hidden slot indefinitely',async()=>{
    const scene=sceneFixture(1),job=scene.start();
    try{
      const first=await job.promise,primitive=first.root.get(2) as C.GroundPolylinePrimitive;
      expect(first.preparationReady).toBe(false);expect(primitive.ready).toBe(false);
      const next=scene.start(road,'next');await turn();expect(scene.admission.queued).toBe(1);
      // This models MapScene's cache eligibility check: only prepared bundles
      // may become dormant. An unfinished bundle is detached and disposed.
      scene.owner.remove(first.root);first.destroy();const second=await next.promise;
      expect(primitive.isDestroyed()).toBe(true);expect(first.root.isDestroyed()).toBe(true);expect(scene.owner.contains(second.root)).toBe(true);
      expect(scene.admission.queued).toBe(0);expect(scene.admission.pending).toBe(1);expect(scene.admission.peakPending).toBe(1);expect(scene.picks.size).toBe(1);
      first.destroy();expect(scene.admission.pending).toBe(1);
      ready(second.root.get(2));scene.admission.poll();expect(second.preparationReady).toBe(true);expect(scene.admission.pending).toBe(0);
      const same=second.root.get(2);second.setActive(false);expect(scene.picks.size).toBe(0);second.setActive(true);
      expect(second.root.get(2)).toBe(same);expect(second.preparationReady).toBe(true);expect(scene.picks.size).toBe(1);expect(scene.admission.pending).toBe(0);
    }finally{await scene.dispose();}
  });

  it('returns an earlier rendered batch reservation immediately when later source construction throws',async()=>{
    const scene=sceneFixture(1,()=>{throw new Error('terrain failed');});
    const source:GeoCollection={type:'FeatureCollection',features:[...Array.from({length:128},(_,i)=>({...fixture.features[0],id:`road-${i}`})),{type:'Feature',id:'point',properties:{kind:'station'},geometry:{type:'Point',coordinates:[127,36]}}]};
    const job=scene.start(source);
    try{
      await expect(job.promise).rejects.toThrow('terrain failed');
      expect(job.stageRoot).toHaveBeenCalledTimes(1);expect(job.cleanup).toHaveBeenCalledTimes(1);expect(job.root!.isDestroyed()).toBe(true);
      expect(scene.owner.length).toBe(0);expect(scene.picks.size).toBe(0);expect(scene.admission.pending).toBe(0);expect(scene.admission.queued).toBe(0);
      const next=scene.start();await next.promise;
      expect(next.stageRoot).toHaveBeenCalledTimes(1);expect(scene.admission.pending).toBe(1);expect(scene.admission.peakPending).toBe(1);
    }finally{await scene.dispose();}
  });

  it('returns the current reservation when staging itself throws and unregisters the complete pick set',async()=>{
    const scene=sceneFixture(1);let failedRoot:C.PrimitiveCollection|undefined;
    try{
      await expect(buildPrimitives(compileGeometry(fixture),asset,scene.viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap:scene.picks,admission:scene.admission,stageRoot:root=>{failedRoot=root;expect(scene.picks.size).toBe(2);throw new Error('scene attach failed');}})).rejects.toThrow('scene attach failed');
      expect(failedRoot!.isDestroyed()).toBe(true);expect(scene.picks.size).toBe(0);expect(scene.admission.pending).toBe(0);expect(scene.admission.queued).toBe(0);
      await scene.start().promise;expect(scene.admission.pending).toBe(1);
    }finally{await scene.dispose();}
  });

  it('cleans a partially staged root when scene admission is disposed while a mixed batch waits',async()=>{
    const scene=sceneFixture(1),job=scene.start(fixture);
    try{
      await turn();expect(scene.admission.pending).toBe(1);expect(scene.admission.queued).toBe(1);
      const rejected=expect(job.promise).rejects.toMatchObject({name:'AbortError'});scene.admission.dispose();await rejected;
      expect(job.cleanup).toHaveBeenCalledTimes(1);expect(job.root!.isDestroyed()).toBe(true);expect(scene.owner.length).toBe(0);expect(scene.picks.size).toBe(0);
      expect(scene.admission.pending).toBe(0);expect(scene.admission.queued).toBe(0);
    }finally{await scene.dispose();}
  });

  it('rejects admission without a staging owner before it can reserve an unrenderable batch',async()=>{
    const scene=sceneFixture(1);
    try{
      await expect(buildPrimitives(compileGeometry(road),asset,scene.viewer,{signal:new AbortController().signal,budget:new FrameWorkBudget(()=>0,async()=>{}),labelLimit:0,pickMap:scene.picks,admission:scene.admission})).rejects.toThrow('rendered staging root');
      expect(scene.admission.pending).toBe(0);expect(scene.admission.queued).toBe(0);expect(scene.picks.size).toBe(0);expect(scene.owner.length).toBe(0);
    }finally{await scene.dispose();}
  });
});
