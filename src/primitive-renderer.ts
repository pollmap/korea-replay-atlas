import * as C from 'cesium';
import type {Asset} from '../shared/contracts';
import {assertGeometryVertexBudget,renderFeatureProperties,resolveFeatureProperties,type GeoMetadata,type GeoProperties,type RenderFeature,type RenderGeometry} from '../shared/geometry';
import {FrameWorkBudget} from '../shared/map-performance';
import type {PrimitiveAdmission,PreparingPrimitive,PrimitiveLease} from '../shared/primitive-admission';
import {displayedBuildingHeight,finiteProperty,selectedProperties,type MapSelection} from '../shared/selection';
import type {SceneLabelScheduler,ScheduledLabel} from './label-scheduler';

export interface PrimitiveSelection extends MapSelection {sourceId:string;properties:GeoProperties;}
export interface PrimitivePick {sourceId:string;properties:GeoProperties;asset:Asset;}
/** Pick owners retain only attribute records, never the worker's coordinate buffer. */
class DeferredPrimitivePick implements PrimitivePick {
  sourceId:string;asset:Asset;
  private feature:RenderFeature;private metadata:GeoMetadata;private resolved:GeoProperties|undefined;
  constructor(feature:RenderFeature,metadata:GeoMetadata,asset:Asset){this.feature=feature;this.metadata=metadata;this.asset=asset;this.sourceId=feature.id;}
  get properties():GeoProperties{return this.resolved??=resolveFeatureProperties(this.feature,this.metadata);}
}
/** Active bundle registration is O(1); dormant metadata stays owned by its bounded bundle. */
export class PrimitivePickRegistry {
  private bundles=new Map<string,readonly PrimitivePick[]>();private count=0;
  get size(){return this.count;}
  register(namespace:string,picks:readonly PrimitivePick[]):void {this.unregister(namespace);this.bundles.set(namespace,picks);this.count+=picks.length;}
  unregister(namespace:string):void {const picks=this.bundles.get(namespace);if(picks){this.count-=picks.length;this.bundles.delete(namespace);}}
  get(key:string):PrimitivePick|undefined {const at=key.lastIndexOf(':'),index=Number(key.slice(at+1));return Number.isInteger(index)&&index>=0?this.bundles.get(key.slice(0,at))?.[index]:undefined;}
  has(key:string):boolean {return this.get(key)!==undefined;}
  clear():void {this.bundles.clear();this.count=0;}
}
export interface PrimitiveBundle {root:C.PrimitiveCollection;featureCount:number;vertexCount:number;pointCount:number;readonly preparationReady:boolean;setActive:(active:boolean,labelLimit?:number)=>void;setGroundRefreshPaused:(paused:boolean)=>void;refreshGround:(terrainRevision?:number)=>void;destroy:()=>void;}
interface GroundSamplingState {revision?:number;paused:boolean;}
let nextPrimitiveNamespace=0;
export function selectionFromProperties(id:string,p:GeoProperties,asset:Asset):PrimitiveSelection{
  return {...selectedProperties(id,p,asset),sourceId:id,properties:p};
}
const colors=new Map<string,C.Color>();
const color=(value:string)=>{let result=colors.get(value);if(!result){result=C.Color.fromCssColorString(value);colors.set(value,result);}return result;};
function fill(p:GeoProperties,asset:Asset):C.Color{return color(p.kind==='water'?'#a8ced4':p.kind==='industrial_land'?'#c9c6b7':String(p.kind).startsWith('airport')?'#c7cbd0':p.kind==='port'?'#bbcec6':asset.layer==='buildings'?'#e5e1d3':'#dce4cd');}
function stroke(p:GeoProperties,asset:Asset):C.Color{return color(asset.layer==='rail'?'#a79571':p.kind==='ferry_route'?'#699ba8':p.kind==='airport_runway'?'#8a9299':'#f5f2df');}
const major=(p:GeoProperties)=>['airport','port','settlement','ferry_terminal','bus_station','station'].includes(String(p.kind));

/** Static batched geometry, without thousands of Entity/PropertyBag/Visualizer objects. */
export async function buildPrimitives(data:RenderGeometry,asset:Asset,viewer:C.Viewer,options:{signal:AbortSignal;budget:FrameWorkBudget;labelLimit:number;pickMap:Map<string,PrimitivePick>|PrimitivePickRegistry;groundRevision?:number;groundRefreshPaused?:boolean;groundState?:()=>GroundSamplingState;stageRoot?:(root:C.PrimitiveCollection)=>()=>void;labelScheduler?:SceneLabelScheduler;admission?:PrimitiveAdmission}):Promise<PrimitiveBundle>{
  if(options.admission&&!options.stageRoot)throw new Error('Primitive admission requires a rendered staging root');
  assertGeometryVertexBudget(data,asset);
  const {signal,budget,pickMap}=options,root=new C.PrimitiveCollection(),keys:string[]=[],points=new C.PointPrimitiveCollection(),labels=options.labelScheduler?undefined:new C.LabelCollection({scene:viewer.scene});
  const labelHandles:(C.Label|ScheduledLabel)[]=[],scheduledLabels:ScheduledLabel[]=[];
  const releaseLabels=()=>{for(const label of scheduledLabels)label.destroy();};
  const namespace=`geometry:${asset.id}:${asset.sha256}:${++nextPrimitiveNamespace}`,picks:PrimitivePick[]=[];
  let picksRegistered=false,staged=false,cleanupStagedRoot:(()=>void)|undefined;
  const registerPicks=()=>{if(picksRegistered)return;if(pickMap instanceof PrimitivePickRegistry)pickMap.register(namespace,picks);else for(let i=0;i<keys.length;i++)pickMap.set(keys[i],picks[i]);picksRegistered=true;};
  const unregisterPicks=()=>{if(pickMap instanceof PrimitivePickRegistry)pickMap.unregister(namespace);else for(const key of keys)pickMap.delete(key);picksRegistered=false;};
  // Updating a growing point/label collection rebuilds its GPU buffers repeatedly.
  if(options.stageRoot){points.show=false;if(labels)labels.show=false;}
  root.add(points);if(labels)root.add(labels);
  const groundPoints:{point:C.PointPrimitive;label?:C.Label|ScheduledLabel;position:C.Cartographic;groundSampled:boolean;groundRevision?:number}[]=[];
  let lines:C.GeometryInstance[]=[],polygons:C.GeometryInstance[]=[],solid:C.GeometryInstance[]=[],batchVertices=0,labelCount=0;
  const batchPreparations=new Set<PreparingPrimitive>(),preparationLeases=new Set<PrimitiveLease>();
  const releasePreparation=()=>{for(const lease of preparationLeases)lease.release();preparationLeases.clear();batchPreparations.clear();};
  const addBatch=async(name:string,create:()=>PreparingPrimitive)=>{
    const lease=await (options.admission?finishTask.waitFor(options.admission.acquire(signal)):undefined);
    if(lease)preparationLeases.add(lease);
    try{
      await budget.run(name,()=>{const primitive=root.add(create());batchPreparations.add(primitive);lease?.track(primitive);},signal);
      // Each batch must enter the scene before acquiring another slot, otherwise
      // a mixed line/polygon flush could wait for readiness of an unrendered root.
      if(options.stageRoot){
        if(!staged){registerPicks();cleanupStagedRoot=options.stageRoot(root);staged=true;}
        if(options.admission)viewer.scene.requestRender();
      }
    }catch(error){lease?.release();if(lease)preparationLeases.delete(lease);throw error;}
  };
  const flush=async()=>{
    const hasBatches=lines.length+polygons.length+solid.length>0;
    if(lines.length){await addBatch('flush-ground-lines',()=>new C.GroundPolylinePrimitive({geometryInstances:lines,appearance:new C.PolylineColorAppearance(),classificationType:C.ClassificationType.TERRAIN,asynchronous:true,releaseGeometryInstances:true}));lines=[];}
    if(polygons.length){await addBatch('flush-ground-polygons',()=>new C.GroundPrimitive({geometryInstances:polygons,appearance:new C.PerInstanceColorAppearance({flat:true,translucent:false}),classificationType:C.ClassificationType.TERRAIN,asynchronous:true,releaseGeometryInstances:true}));polygons=[];}
    if(solid.length){await addBatch('flush-solid-polygons',()=>new C.Primitive({geometryInstances:solid,appearance:new C.PerInstanceColorAppearance({flat:false,closed:true,translucent:false}),asynchronous:true,releaseGeometryInstances:true,shadows:C.ShadowMode.ENABLED}));solid=[];}
    batchVertices=0;
    if(hasBatches&&options.stageRoot){
      // Let Cesium start geometry workers/uploads before the next batch arrives.
      if(!options.admission)viewer.scene.requestRender();
      await budget.defer(signal);
    }
  };
  const flushIfFull=async()=>{if(lines.length+polygons.length+solid.length>=128||batchVertices>=8192)await flush();};
  const positions=async(offset:number,count:number)=>{
    const result:C.Cartesian3[]=[];
    for(let start=offset;start<offset+count;start+=32){
      await budget.run('coordinate-conversion',()=>{
        for(let at=start;at<Math.min(start+32,offset+count);at++){
          const i=at*3;result.push(C.Cartesian3.fromDegrees(data.coordinates[i],data.coordinates[i+1],data.coordinates[i+2]));
        }
      },signal);
    }
    return result;
  };
  const addLine=async(line:C.Cartesian3[],id:string,lineColor:C.Color,width:number)=>{
    // A long line keeps every original vertex; overlapping endpoints preserve its exact path.
    for(let start=0;start<line.length-1;start+=512){
      const segment=line.slice(start,Math.min(start+513,line.length));
      await budget.run('line-instance',()=>{lines.push(new C.GeometryInstance({id,geometry:new C.GroundPolylineGeometry({positions:segment,width}),attributes:{color:C.ColorGeometryInstanceAttribute.fromColor(lineColor)}}));batchVertices+=segment.length;},signal);
      await flushIfFull();
    }
  };
  const finishTask=budget.startTask();
  try{
    for(let start=0;start<data.features.length;start+=32){
      await budget.run('pick-metadata',()=>{for(let index=start;index<Math.min(start+32,data.features.length);index++){
        const feature=data.features[index],key=`${namespace}:${index}`;
        const pick=data.metadata?new DeferredPrimitivePick(feature,data.metadata,asset):{sourceId:feature.id,properties:feature.properties,asset};keys.push(key);picks.push(pick);
        if(pickMap instanceof Map)pickMap.set(key,pick);
      }},signal);
    }
    for(const part of data.parts){
      const p=renderFeatureProperties(data.features[part.feature],data.metadata),id=keys[part.feature];
      if(part.kind==='point'){
        await budget.run('point-instance',()=>{
        const i=part.offset*3,sourceHeight=data.coordinates[i+2],position=C.Cartographic.fromDegrees(data.coordinates[i],data.coordinates[i+1],sourceHeight);
        // Read the gate for each scheduled point: camera/terrain state can change while construction yields.
        const ground=options.groundState?.()??{revision:options.groundRevision,paused:options.groundRefreshPaused??false};
        const terrainHeight=ground.paused?undefined:budget.measure('terrain-height',()=>viewer.scene.globe.getHeight(position));
        const groundSampled=typeof terrainHeight==='number'&&Number.isFinite(terrainHeight);
        // Terrain placement is display-only. Until it is available, keep the source coordinate unchanged.
        if(groundSampled)position.height=terrainHeight;
        const cartesian=C.Cartographic.toCartesian(position),important=major(p);
        const point=points.add({id,position:cartesian,pixelSize:important?6:3,color:color(p.kind==='airport'?'#507d87':p.kind==='station'?'#ad7555':'#86775a'),outlineColor:C.Color.WHITE,outlineWidth:1,distanceDisplayCondition:new C.DistanceDisplayCondition(0,important?1000000:18000)});
        let label:C.Label|ScheduledLabel|undefined;
        if(important&&typeof p.name==='string'&&p.name&&labelCount<options.labelLimit){
          labelCount++;
          const definition={id,position:cartesian,text:p.name,font:'12px sans-serif',fillColor:color('#3e5849'),outlineColor:C.Color.WHITE,outlineWidth:3,style:C.LabelStyle.FILL_AND_OUTLINE,pixelOffset:new C.Cartesian2(0,-16),distanceDisplayCondition:new C.DistanceDisplayCondition(0,p.kind==='station'?6000:80000)};
          if(options.labelScheduler){label=options.labelScheduler.add({...definition,show:false});scheduledLabels.push(label);}
          else label=labels!.add(definition);
          labelHandles.push(label);
        }
        groundPoints.push({point,label,position,groundSampled,groundRevision:groundSampled?ground.revision:undefined});
        },signal);
      }else if(part.kind==='line'){
        const points=await positions(part.offset,part.count);
        await addLine(points,id,stroke(p,asset),asset.layer==='rail'?2:['motorway','primary'].includes(String(p.class??p.highway))?4:2);
      }else{
        let offset=part.offset;const rings:C.Cartesian3[][]=[];
        for(const count of part.rings!){rings.push(await positions(offset,count));offset+=count;}
        const height=displayedBuildingHeight(p),building=asset.layer==='buildings';
        if(building&&height===undefined){
          // Review footprints retain raw height only as metadata. They are never
          // extruded again from that raw value or flattened into filled roofs.
          for(const ring of rings)await addLine(ring,id,color('#ad8667'),1.5);
        }else{
          const holes:C.PolygonHierarchy[]=[];
          for(let start=1;start<rings.length;start+=32)await budget.run('polygon-holes',()=>{for(let index=start;index<Math.min(start+32,rings.length);index++)holes.push(new C.PolygonHierarchy(rings[index]));},signal);
          await budget.run('polygon-instance',()=>{
          const hierarchy=new C.PolygonHierarchy(rings[0],holes);
          const base=finiteProperty(p.base_height)??0;
          const minimum=Math.max(0,finiteProperty('render_min_height' in p?p.render_min_height:p.min_height)??0);
          const geometry=new C.PolygonGeometry({polygonHierarchy:hierarchy,vertexFormat:C.PerInstanceColorAppearance.VERTEX_FORMAT,...(building?{height:base+Math.min(minimum,height!),extrudedHeight:base+height!}:{})});
          const instance=new C.GeometryInstance({id,geometry,attributes:{color:C.ColorGeometryInstanceAttribute.fromColor(fill(p,asset))}});
          if(building)solid.push(instance);else polygons.push(instance);
          batchVertices+=part.count;
          },signal);
          await flushIfFull();
        }
      }
    }
    await flush();
    const initialGroundComplete=groundPoints.every(item=>item.groundSampled&&item.groundRevision===options.groundRevision);
    let active=true,updating=false,groundPaused=options.groundRefreshPaused??false,manualRevision=0,lastGroundRevision=initialGroundComplete?options.groundRevision:undefined,requestedRevision:number|undefined,lastLabelLimit=options.labelLimit;
    let refreshController:AbortController|null=null;
    const refreshGround=(terrainRevision?:number)=>{
      if(!active||!groundPoints.length||root.isDestroyed())return;
      const revision=terrainRevision??--manualRevision;requestedRevision=revision;
      if(groundPaused||updating||lastGroundRevision===revision)return;
      updating=true;const controller=new AbortController();refreshController=controller;
      void(async()=>{
        // Do not run a first batch inline in Globe.tileLoadProgressEvent's render callback.
        await budget.defer(controller.signal);
        const finishRefresh=budget.startTask();
        try{for(const item of groundPoints){
          if(!active||groundPaused||root.isDestroyed()||controller.signal.aborted||viewer.isDestroyed())return;
          if(item.groundSampled&&item.groundRevision===revision)continue;
          await budget.run('ground-point-refresh',()=>{
            if(!active||groundPaused||root.isDestroyed()||viewer.isDestroyed())return;
            const height=budget.measure('terrain-height',()=>viewer.scene.globe.getHeight(item.position));
            if(typeof height==='number'&&Number.isFinite(height)){
              item.groundSampled=true;item.groundRevision=revision;
              if(Math.abs(height-item.position.height)>.05){item.position.height=height;const position=C.Cartographic.toCartesian(item.position);item.point.position=position;if(item.label)item.label.position=position;}
            }
          },controller.signal);
        }lastGroundRevision=groundPoints.every(item=>item.groundSampled&&item.groundRevision===revision)?revision:undefined;if(!viewer.isDestroyed())viewer.scene.requestRender();}finally{finishRefresh();}
      })().catch(()=>{}).finally(()=>{
        updating=false;if(refreshController===controller)refreshController=null;
        if(active&&!groundPaused&&!root.isDestroyed()&&requestedRevision!==lastGroundRevision&&(controller.signal.aborted||requestedRevision!==revision))refreshGround(requestedRevision);
      });
    };
    if(signal.aborted)throw new DOMException('Aborted','AbortError');
    registerPicks();
    if(options.stageRoot){points.show=true;if(labels)labels.show=true;viewer.scene.requestRender();}
    for(const label of scheduledLabels)label.show=true;
    // Successful completion transfers the staged root to the caller.
    cleanupStagedRoot=undefined;
    return {root,featureCount:data.features.length,vertexCount:data.vertexCount,pointCount:groundPoints.length,refreshGround,
      get preparationReady(){for(const primitive of batchPreparations)if(primitive.isDestroyed()||primitive.ready)batchPreparations.delete(primitive);return batchPreparations.size===0;},
      // Resumption is paired with refreshGround(currentRevision) by the settled scene.
      setGroundRefreshPaused:(paused)=>{groundPaused=paused;if(paused)refreshController?.abort();},
      setActive:(next,labelLimit)=>{
        if(root.isDestroyed())return;
        const activityChanged=next!==active,limitChanged=next&&labelLimit!==undefined&&labelLimit!==lastLabelLimit;
        if(activityChanged){active=next;root.show=next;if(next)registerPicks();else{unregisterPicks();refreshController?.abort();}}
        if(limitChanged)lastLabelLimit=labelLimit;
        if(limitChanged||(activityChanged&&options.labelScheduler))for(let index=0;index<labelHandles.length;index++)labelHandles[index].show=next&&index<lastLabelLimit;
      },
      destroy:()=>{active=false;refreshController?.abort();unregisterPicks();releaseLabels();if(!root.isDestroyed())root.destroy();releasePreparation();},
    };
  }catch(error){
    unregisterPicks();releaseLabels();
    try{cleanupStagedRoot?.();}finally{if(!root.isDestroyed())root.destroy();releasePreparation();}
    throw error;
  }
  finally{finishTask();}
}
