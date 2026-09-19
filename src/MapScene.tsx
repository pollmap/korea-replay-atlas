import {forwardRef,useCallback,useEffect,useImperativeHandle,useMemo,useRef,useState} from 'react';
import * as C from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';
import type {Asset,BBox,Catalog,LayerId,Place,ReplayChunk,Track,WeatherFrame,WeatherManifest} from '../shared/contracts';
import {PLACES} from '../shared/sources';
import {kstDate,kstInstant} from '../shared/time';
import {AssetLoadQueue,frameBatch,tilesetBudget,setTilesetScreenSpaceError} from '../shared/asset-loader';
import type {Selection} from './App';
import {resolveSceneAssets} from './catalog';
import {GeometryClient} from './geometry-client';
import {buildPrimitives,PrimitivePickRegistry,selectionFromProperties,type PrimitiveBundle} from './primitive-renderer';
import {FrameWorkBudget,QUALITY,QualityGovernor,sceneQuality,selectViewAssets,TerrainRevisionGate,type QualityLevel,type PerformanceSnapshot} from '../shared/map-performance';
import {PrimitiveAdmission} from '../shared/primitive-admission';
import {heapUnderPressure,PRIMITIVE_CACHE_LIMITS,primitiveCacheCost,primitiveCacheKey,PrimitiveReuseCache} from '../shared/primitive-cache';
import {prepareTrack,preparedTrackPosition,type PreparedTrack} from '../shared/replay-performance';
import {selectedProperties} from '../shared/selection';
import type {LiveTransitSnapshot} from '../shared/live-transit';
import {createLiveBusLayer,type LiveBusLayer} from './live-map';
import {createCameraSettler} from './camera-settle';
import {reconcileSceneAssets,type SceneAssetResult} from './scene-assets';
import {SceneFrameAudit} from '../shared/scene-frame-audit';
import {SceneLabelScheduler} from './label-scheduler';
import {GatedFrameWorkBudget,StaticWorkGate} from './static-work-gate';

export interface MapHandle {flyTo:(place:Place,options?:{overviewPanelVisible:boolean;focused:boolean})=>void;north:()=>void;overhead:()=>void;camera:()=>number[]|null;flatCamera?:()=>[number,number,number,number]|null;viewport?:()=>Place|null;}
interface Props {catalog:Catalog;layers:Record<LayerId,boolean>;instant:number;mode:'replay'|'sun';liveTransit?:LiveTransitSnapshot|null;initialPlace:Place;initialCamera?:number[]|null;lightweight:boolean;onSelect:(value:Selection)=>void;onStatus:(value:string)=>void;onPerformance?:(value:PerformanceSnapshot)=>void;}
interface Resource {releaseId:string;asset:Asset;source?:C.GeoJsonDataSource;primitive?:PrimitiveBundle;tileset?:C.Cesium3DTileset;imagery?:C.ImageryLayer;terrain?:C.TerrainProvider;distantOverview?:boolean;}
const isBatchedGeometry=(asset:Asset)=>asset.format==='geojson'&&asset.source_id!=='natural-earth'&&asset.layer!=='depth';
const MapScene=forwardRef<MapHandle,Props>(function MapScene(props,ref){
  const element=useRef<HTMLDivElement>(null);
  const viewer=useRef<C.Viewer|null>(null);
  const labelScheduler=useRef<SceneLabelScheduler|null>(null);
  const liveBus=useRef<LiveBusLayer|null>(null);
  const resources=useRef<Resource[]>([]);
  const stagedRoots=useRef(new Set<C.PrimitiveCollection>());
  const sceneIndexLoading=useRef(false);
  const tracks=useRef<{assetId:string;track:Track;prepared:PreparedTrack;entity:C.Entity;position:C.ConstantPositionProperty;cartesian:C.Cartesian3}[]>([]);
  const frames=useRef<{asset:Asset;frame:WeatherFrame;time:number;until:number}[]>([]);
  const weatherLayers=useRef(new Map<string,{key:string;layer?:C.ImageryLayer}>());
  const [resourceRevision,setResourceRevision]=useState(0);
  const [loadQueue]=useState(()=>new AssetLoadQueue<boolean|undefined>(4));
  const [commitFrames]=useState(()=>frameBatch(()=>{
    const v=viewer.current;if(!v||v.isDestroyed())return;
    setResourceRevision(n=>n+1);v.scene.requestRender();
  },callback=>window.requestAnimationFrame(callback),id=>window.cancelAnimationFrame(id)));
  const [geometryClient]=useState(()=>new GeometryClient());
  const [workBudget]=useState(()=>new FrameWorkBudget());
  const [geometryGate]=useState(()=>new StaticWorkGate());
  const [geometryBudget]=useState(()=>new GatedFrameWorkBudget(workBudget,geometryGate));
  const primitiveAdmission=useRef<PrimitiveAdmission|null>(null);
  const pickMap=useRef(new PrimitivePickRegistry());
  const jobs=useRef(new Map<string,{controller:AbortController;promise:Promise<boolean|undefined>}>());
  const wanted=useRef(new Set<string>());
  const [automaticQuality,setAutomaticQuality]=useState<QualityLevel>(props.lightweight||window.innerWidth<800?'low':'balanced');
  const quality:QualityLevel=props.lightweight?'low':automaticQuality;
  const qualityRef=useRef(quality);qualityRef.current=quality;
  const movingRef=useRef(false);
  const tilesetCount=useRef(0);
  const terrainRevisions=useRef(new TerrainRevisionGate());
  const invalidateGround=useCallback(()=>{
    terrainRevisions.current.invalidate();
    for(const resource of resources.current)resource.primitive?.setGroundRefreshPaused(true);
    liveBus.current?.setGroundRefreshPaused(true);
    const v=viewer.current;if(v&&!v.isDestroyed())v.scene.requestRender();
  },[]);
  const settleGround=useCallback(()=>{
    const v=viewer.current;if(!v||v.isDestroyed())return;
    const revision=terrainRevisions.current.settle(v.scene.globe.tilesLoaded,movingRef.current);
    if(revision!==undefined){for(const resource of resources.current){resource.primitive?.setGroundRefreshPaused(false);resource.primitive?.refreshGround(revision);}liveBus.current?.setGroundRefreshPaused(false);liveBus.current?.refreshGround(revision);}
  },[]);
  const cachePressure=useRef(false);
  const disposePrimitive=useCallback((resource:Resource)=>{
    const primitive=resource.primitive;if(!primitive)return;
    const v=viewer.current;if(v&&!v.isDestroyed())v.scene.primitives.remove(primitive.root);
    primitive.destroy();
  },[]);
  const [primitiveCache]=useState(()=>new PrimitiveReuseCache<Resource>(PRIMITIVE_CACHE_LIMITS[quality],disposePrimitive));
  const updateCacheLimits=useCallback(()=>{
    const memory=(performance as Performance&{memory?:{usedJSHeapSize:number;jsHeapSizeLimit:number}}).memory;
    cachePressure.current=heapUnderPressure(memory);
    primitiveCache.setLimits(cachePressure.current||document.hidden?{bytes:0,vertices:0,files:0}:PRIMITIVE_CACHE_LIMITS[qualityRef.current]);
  },[primitiveCache]);
  const rebalanceTilesets=useCallback(()=>{
    const current=resources.current.flatMap(r=>r.tileset?[r.tileset]:[]);
    const budget=tilesetBudget(Math.max(tilesetCount.current,current.length),qualityRef.current==='low');
    for(const tileset of current){
      tileset.cacheBytes=budget.cacheBytes;tileset.maximumCacheOverflowBytes=budget.maximumCacheOverflowBytes;
      setTilesetScreenSpaceError(tileset,sceneQuality(qualityRef.current,movingRef.current,false).sse);
    }
  },[]);
  const [view,setView]=useState<{bbox:BBox;height:number;workRevision:number}>(()=>({bbox:[props.initialPlace.lon-.1,props.initialPlace.lat-.1,props.initialPlace.lon+.1,props.initialPlace.lat+.1],height:props.initialPlace.range,workRevision:0}));
  const coreAssets=useMemo(()=>props.catalog.assets.filter(asset=>asset.format!=='asset-index'),[props.catalog]);
  const [sceneAssetResult,setSceneAssetResult]=useState<SceneAssetResult>(()=>({releaseId:props.catalog.release_id,assets:coreAssets}));
  // A release switch must never reload the previous release's pending catalog result.
  const sceneAssets=sceneAssetResult.releaseId===props.catalog.release_id?sceneAssetResult.assets:coreAssets;
  useEffect(()=>{
    sceneIndexLoading.current=true;
    const controller=new AbortController();let pendingFrame:number|undefined,latest:{assets:Asset[];complete:boolean}|undefined;
    const publish=(assets:Asset[],complete=false)=>{
      if(controller.signal.aborted)return;
      latest={assets,complete};
      if(pendingFrame===undefined)pendingFrame=requestAnimationFrame(()=>{
        pendingFrame=undefined;
        const update=latest;
        if(!controller.signal.aborted&&update)setSceneAssetResult(previous=>reconcileSceneAssets(previous,props.catalog.release_id,update.assets,update.complete,controller.signal));
      });
    };
    void resolveSceneAssets(props.catalog,view.bbox,view.height,controller.signal,undefined,publish,props.layers).then(assets=>{if(!controller.signal.aborted)sceneIndexLoading.current=false;publish(assets,true);}).catch(error=>{
      if(controller.signal.aborted)return;
      sceneIndexLoading.current=false;
      statusCallback.current(`공간 목록 오류: ${error instanceof Error?error.message:'불러오기 실패'}`);
      // Other parallel region reads can finish after the first one fails.
      controller.abort();if(pendingFrame!==undefined)cancelAnimationFrame(pendingFrame);
    });
    return()=>{controller.abort();if(pendingFrame!==undefined)cancelAnimationFrame(pendingFrame);};
  },[props.catalog,view,props.layers]);
  const replayDay=kstDate(props.instant);
  const dayStart=kstInstant(replayDay,0),dayEnd=dayStart+86400000;
  const distantOverview=view.height>350000;
  const selection=useMemo(()=>selectViewAssets(sceneAssets,{bbox:view.bbox,height:view.height,layers:props.layers,mode:props.mode,dayStart,dayEnd,quality}),[sceneAssets,view,props.layers,props.mode,dayStart,dayEnd,quality]);
  const selectionRef=useRef(selection);selectionRef.current=selection;
  const visibleIds=selection.assets.map(a=>`${a.id}:${a.sha256}`).join('|');
  const performanceCallback=useRef(props.onPerformance);performanceCallback.current=props.onPerformance;
  const initial=useRef(props.initialPlace);
  const initialCamera=useRef(props.initialCamera);
  const selectCallback=useRef(props.onSelect);selectCallback.current=props.onSelect;
  const statusCallback=useRef(props.onStatus);statusCallback.current=props.onStatus;
  const layersRef=useRef(props.layers);layersRef.current=props.layers;
  const applyVisualQuality=useCallback(()=>{
    const v=viewer.current;if(!v||v.isDestroyed())return;
    const setting=sceneQuality(qualityRef.current,movingRef.current,layersRef.current.sun,v.camera.positionCartographic.height);
    v.shadows=setting.shadows;v.resolutionScale=setting.resolution;
    if(v.scene.msaaSamples!==setting.msaaSamples)v.scene.msaaSamples=setting.msaaSamples;
    if(v.scene.globe.maximumScreenSpaceError!==setting.terrainSse){v.scene.globe.maximumScreenSpaceError=setting.terrainSse;invalidateGround();}
    if(v.shadowMap.size!==setting.shadowSize)v.shadowMap.size=setting.shadowSize;
    v.shadowMap.maximumDistance=setting.shadowDistance;
    for(const resource of resources.current)if(resource.tileset)setTilesetScreenSpaceError(resource.tileset,setting.sse);
    v.scene.requestRender();
  },[invalidateGround]);
  const flyTo=(p:Place)=>{const v=viewer.current;if(!v||v.isDestroyed())return;v.camera.flyToBoundingSphere(new C.BoundingSphere(C.Cartesian3.fromDegrees(p.lon,p.lat),1),{duration:1.8,offset:new C.HeadingPitchRange(0,-Math.PI/3,p.range)});};
  useImperativeHandle(ref,()=>({flyTo,north:()=>{const v=viewer.current;if(v)v.camera.flyTo({destination:v.camera.positionWC,orientation:{heading:0,pitch:v.camera.pitch,roll:0},duration:.7});},overhead:()=>{const v=viewer.current;if(v)v.camera.flyTo({destination:v.camera.positionWC,orientation:{heading:0,pitch:-Math.PI/2,roll:0},duration:.7});},camera:()=>{const v=viewer.current;if(!v)return null;return [v.camera.positionWC.x,v.camera.positionWC.y,v.camera.positionWC.z,v.camera.heading,v.camera.pitch,v.camera.roll];},viewport:()=>{const v=viewer.current;if(!v)return null;const ray=v.camera.getPickRay(new C.Cartesian2(v.canvas.clientWidth/2,v.canvas.clientHeight/2)),point=ray?v.scene.globe.pick(ray,v.scene):undefined,center=point?C.Cartographic.fromCartesian(point):v.camera.positionCartographic;return {...initial.current,lon:C.Math.toDegrees(center.longitude),lat:C.Math.toDegrees(center.latitude),range:Math.max(100,Math.min(2500000,point?C.Cartesian3.distance(v.camera.positionWC,point):v.camera.positionCartographic.height))};}}),[]);
  useEffect(()=>{
    if(!element.current)return;
    const initialWeatherLayers=weatherLayers.current;
    const activeJobs=jobs.current,activePicks=pickMap.current,activeStagedRoots=stagedRoots.current;
    let cameraSettler:ReturnType<typeof createCameraSettler>|undefined;
    let sceneLabels:SceneLabelScheduler|undefined;
    let updateGeometryVisibility:(()=>void)|undefined;
    const admission=new PrimitiveAdmission(2);primitiveAdmission.current=admission;
    let v:C.Viewer;
    const previousRequests={maximumRequests:C.RequestScheduler.maximumRequests,maximumRequestsPerServer:C.RequestScheduler.maximumRequestsPerServer};
    try{
      v=new C.Viewer(element.current,{animation:false,timeline:false,baseLayerPicker:false,baseLayer:false,geocoder:false,homeButton:false,sceneModePicker:false,navigationHelpButton:false,fullscreenButton:false,selectionIndicator:false,infoBox:false,shouldAnimate:false,terrainProvider:new C.EllipsoidTerrainProvider(),contextOptions:{webgl:{alpha:false}},msaaSamples:sceneQuality(qualityRef.current,false,false).msaaSamples,requestRenderMode:true,maximumRenderTimeChange:Infinity});
      viewer.current=v;
      geometryGate.startSession(document.hidden);
      C.RequestScheduler.maximumRequests=4;C.RequestScheduler.maximumRequestsPerServer=4;
      const gl=v.canvas.getContext('webgl2')??v.canvas.getContext('webgl');
      if(gl){
        const debug=gl.getExtension('WEBGL_debug_renderer_info');
        element.current.dataset.gpuRenderer=String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL??gl.RENDERER));
        element.current.dataset.gpuVendor=String(gl.getParameter(debug?.UNMASKED_VENDOR_WEBGL??gl.VENDOR));
        element.current.dataset.webglVersion=String(gl.getParameter(gl.VERSION));
      }
      v.scene.backgroundColor=C.Color.fromCssColorString('#d8e5e5');
      v.scene.globe.baseColor=C.Color.fromCssColorString('#bfd4d6');
      v.scene.globe.enableLighting=true;
      v.scene.globe.depthTestAgainstTerrain=true;
      v.scene.fog.enabled=true;v.scene.fog.density=.00012;
      v.scene.postProcessStages.fxaa.enabled=true;
      v.scene.screenSpaceCameraController.minimumZoomDistance=80;
      v.scene.screenSpaceCameraController.maximumZoomDistance=2500000;
      v.scene.light=new C.SunLight();
      v.cesiumWidget.creditContainer.setAttribute('aria-label','지도 데이터 저작권');
      const hash=new URLSearchParams(location.hash.slice(1)), camera=initialCamera.current===undefined?hash.get('camera')?.split(',').map(Number):initialCamera.current;
      if(camera?.length===6&&camera.every(Number.isFinite)&&Math.hypot(...camera.slice(0,3))>6000000&&Math.hypot(...camera.slice(0,3))<10000000&&Math.abs(camera[3])<=2*Math.PI&&Math.abs(camera[4])<=Math.PI/2&&Math.abs(camera[5])<=2*Math.PI){v.camera.setView({destination:new C.Cartesian3(camera[0],camera[1],camera[2]),orientation:{heading:camera[3],pitch:camera[4],roll:camera[5]}});}
      else v.camera.lookAt(C.Cartesian3.fromDegrees(initial.current.lon,initial.current.lat),new C.HeadingPitchRange(0,-Math.PI/3,initial.current.range));
      v.camera.lookAtTransform(C.Matrix4.IDENTITY);
      const updateView=()=>{
        const rect=v.camera.computeViewRectangle(v.scene.globe.ellipsoid);
        const height=v.camera.positionCartographic.height,workRevision=geometryGate.reserveView();
        // Looking away from the globe can have no rectangle. Keep the last known
        // footprint, but still commit the new height/revision so work cannot stick.
        const bbox:BBox|undefined=rect?[C.Math.toDegrees(rect.west),C.Math.toDegrees(rect.south),C.Math.toDegrees(rect.east),C.Math.toDegrees(rect.north)]:undefined;
        setView(previous=>({bbox:bbox??previous.bbox,height,workRevision}));
      };
      updateGeometryVisibility=()=>{
        // Reserve a fresh selection before reopening a visible tab's queued work.
        if(!document.hidden&&!movingRef.current)updateView();
        geometryGate.setHidden(document.hidden);
      };
      document.addEventListener('visibilitychange',updateGeometryVisibility);
      updateView();
      const governor=new QualityGovernor(qualityRef.current);let moving=false,renderStart=0,lastFrame=0,lastReport=0;
      // Opt-in QA samples are independent of governor resets. A monotonically
      // increasing count lets the browser audit merge overlapping windows.
      const measuring=new URLSearchParams(location.search).get('measure')==='1',measuredFrames:number[]=[];let measuredCount=0;
      const frameAudit=measuring?new SceneFrameAudit():null;let updateStart=0,updateLoading=false;
      const geometryPending=()=>resources.current.some(resource=>{
        const root=resource.primitive?.root;if(!root||root.isDestroyed())return false;
        for(let index=0;index<root.length;index++){
          const child=root.get(index);
          // Cesium's empty label collection never creates GPU buffers or turns ready.
          if(child instanceof C.LabelCollection&&child.length===0)continue;
          if(child.ready===false)return true;
        }
        return false;
      });
      const sceneLoading=()=>sceneIndexLoading.current||jobs.current.size>0||stagedRoots.current.size>0||Boolean(sceneLabels?.snapshot().preparing)||!v.scene.globe.tilesLoaded||
        resources.current.some(resource=>resource.tileset&&!resource.tileset.tilesLoaded)||geometryPending();
      // Geometry may finish during this frame. Charge loading at either boundary.
      // Shared labels include their admission/render pass, not private atlas readiness.
      if(frameAudit)v.scene.preUpdate.addEventListener(()=>{updateStart=performance.now();updateLoading=sceneLoading();});
      cameraSettler=createCameraSettler(()=>{
        if(v.isDestroyed())return;
        moving=false;lastFrame=0;movingRef.current=false;geometryGate.endMotion();invalidateGround();applyVisualQuality();updateView();
      },350,()=>{
        if(v.isDestroyed())return;
        moving=true;movingRef.current=true;lastFrame=0;geometryGate.beginMotion();
        for(const resource of resources.current)resource.primitive?.setGroundRefreshPaused(true);
        liveBus.current?.setGroundRefreshPaused(true);applyVisualQuality();
      });
      v.scene.postUpdate.addEventListener(()=>{
        const camera=v.camera,p=camera.positionWC,d=camera.directionWC,u=camera.upWC,frustum=camera.frustum;
        cameraSettler?.observe({position:[p.x,p.y,p.z],direction:[d.x,d.y,d.z],up:[u.x,u.y,u.z],
          // Drawing-buffer aspect changes slightly when resolution is rounded.
          // CSS aspect tracks an actual viewport change without restarting motion.
          frustum:[frustum instanceof C.PerspectiveFrustum?(frustum.fov??1):1,v.canvas.clientWidth/v.canvas.clientHeight]});
      });
      v.scene.preRender.addEventListener(()=>{renderStart=performance.now();});
      // Start CPU timing before admission so the quality governor includes label work.
      sceneLabels=new SceneLabelScheduler(v.scene);labelScheduler.current=sceneLabels;
      v.scene.postRender.addEventListener(()=>{
        admission.poll();
        if(admission.pending&&!document.hidden)v.scene.requestRender();
        // Check after quadtree traversal has observed a new provider/SSE/camera.
        settleGround();
        const now=performance.now(),frame=moving&&lastFrame&&!document.hidden?now-lastFrame:null;lastFrame=now;
        if(measuring&&frame!==null){measuredCount++;measuredFrames.push(Math.round(frame*100)/100);if(measuredFrames.length>2048)measuredFrames.shift();}
        frameAudit?.record({at:now,sceneMs:now-updateStart,renderMs:now-renderStart,frameMs:frame,moving,
          loading:updateLoading||sceneLoading()});
        const snapshot=governor.observe(now-renderStart,frame,now);
        if(now-lastReport>=1000){
          updateCacheLimits();
          if(element.current){
            element.current.dataset.quality=qualityRef.current;
            if(measuring){
              element.current.dataset.measuredFrameCount=String(measuredCount);element.current.dataset.measuredFrames=measuredFrames.join(',');
              const position=v.camera.positionCartographic;
              element.current.dataset.cameraCoordinates=[C.Math.toDegrees(position.longitude),C.Math.toDegrees(position.latitude),position.height].join(',');
              element.current.dataset.cameraOrientation=[v.camera.heading,v.camera.pitch,v.camera.roll].join(',');
              element.current.dataset.sceneIndexLoading=String(sceneIndexLoading.current);
              element.current.dataset.geometryPending=String(geometryPending());
              element.current.dataset.primitivePreparing=String(admission.pending);
              element.current.dataset.primitivePreparationQueue=String(admission.queued);
              element.current.dataset.primitivePreparingPeak=String(admission.peakPending);
            }
            if(frameAudit)element.current.dataset.sceneFrameAudit=JSON.stringify(frameAudit.snapshot());
            element.current.dataset.renderP95Ms=snapshot.renderP95Ms.toFixed(2);
            element.current.dataset.frameP95Ms=snapshot.frameP95Ms.toFixed(2);
            element.current.dataset.geometryFeatures=String(resources.current.reduce((sum,r)=>sum+(r.primitive?.featureCount??0),0));
            element.current.dataset.entityCount=String(v.entities.values.length+resources.current.reduce((sum,r)=>sum+(r.source?.entities.values.length??0),0));
            element.current.dataset.tilesetBytes=String(resources.current.reduce((sum,r)=>sum+(r.tileset?.totalMemoryUsageInBytes??0),0));
            element.current.dataset.deferredAssets=String(selectionRef.current.deferred);
            element.current.dataset.geometryVertices=String(resources.current.reduce((sum,r)=>sum+(r.primitive?.vertexCount??0),0));
            element.current.dataset.geometryBytesBudget=String(selectionRef.current.bytes);
            element.current.dataset.geometryVerticesBudget=String(selectionRef.current.vertices);element.current.dataset.terrainSse=String(v.scene.globe.maximumScreenSpaceError);
            element.current.dataset.groundRevision=String(terrainRevisions.current.revision);element.current.dataset.groundRevisionPending=String(terrainRevisions.current.pending);
            const cache=primitiveCache.snapshot();
            element.current.dataset.geometryCacheFiles=String(cache.files);element.current.dataset.geometryCacheBytesEstimate=String(cache.bytes);element.current.dataset.geometryCacheVertices=String(cache.vertices);
            element.current.dataset.geometryCacheHits=String(cache.hits);element.current.dataset.geometryCacheMisses=String(cache.misses);element.current.dataset.geometryCacheEvictions=String(cache.evictions);
            element.current.dataset.geometryCacheLimitBytes=String(cache.limitBytes);element.current.dataset.geometryCacheLimitVertices=String(cache.limitVertices);element.current.dataset.geometryCacheLimitFiles=String(cache.limitFiles);
            element.current.dataset.geometryCachePressure=String(cachePressure.current);element.current.dataset.primitivePickCount=String(pickMap.current.size);
            const chunks=workBudget.snapshot();element.current.dataset.chunkP95Ms=chunks.chunkP95Ms.toFixed(2);element.current.dataset.chunkMaxMs=chunks.chunkMaxMs.toFixed(2);element.current.dataset.chunkCount=String(chunks.chunkCount);
            element.current.dataset.operationMaxMs=chunks.operationMaxMs.toFixed(2);element.current.dataset.operationMaxPhase=chunks.operationMaxPhase;
            element.current.dataset.cameraMoving=String(moving);element.current.dataset.cameraDetailPending=String(movingRef.current);element.current.dataset.assetJobs=String(jobs.current.size);
            element.current.dataset.geometryStagedRoots=String(stagedRoots.current.size);
            const workGate=geometryGate.snapshot();element.current.dataset.geometryCreationPaused=String(workGate.blocked);element.current.dataset.geometryCreationWaiters=String(workGate.waiting);
            const labels=sceneLabels?.snapshot();
            if(labels){element.current.dataset.labelOwned=String(labels.owned);element.current.dataset.labelQueued=String(labels.queued);
              element.current.dataset.labelMaterialized=String(labels.materialized);element.current.dataset.labelPreparing=String(labels.preparing);
              element.current.dataset.labelCollections=String(labels.collections);element.current.dataset.labelGenerationCodePoints=String(labels.generationCodePoints);
              element.current.dataset.labelCreated=String(labels.created);element.current.dataset.labelFailed=String(labels.failed);element.current.dataset.labelMaxPerFrame=String(labels.maxPerFrame);}
            const rect=v.canvas.getBoundingClientRect();element.current.dataset.canvasWidth=String(v.canvas.width);element.current.dataset.canvasHeight=String(v.canvas.height);
            element.current.dataset.cssWidth=String(rect.width);element.current.dataset.cssHeight=String(rect.height);element.current.dataset.devicePixelRatio=String(window.devicePixelRatio);element.current.dataset.resolutionScale=String(v.resolutionScale);
            element.current.dataset.shadows=String(v.shadows);element.current.dataset.shadowMapSize=String(v.shadowMap.size);element.current.dataset.shadowMaxDistance=String(v.shadowMap.maximumDistance);
            element.current.dataset.msaaSamples=String(v.scene.msaaSamples);
          }
          lastReport=now;setAutomaticQuality(previous=>previous===snapshot.quality?previous:snapshot.quality);performanceCallback.current?.({...snapshot,quality:qualityRef.current});}
      });
      v.scene.globe.tileLoadProgressEvent.addEventListener((remaining:number)=>{if(remaining===0)settleGround();});
      PLACES.filter(p=>p.id!=='korea').forEach(p=>v.entities.add({id:`place:${p.id}`,name:p.name,position:C.Cartesian3.fromDegrees(p.lon,p.lat,20),point:{pixelSize:5,color:C.Color.fromCssColorString('#345f53'),outlineColor:C.Color.WHITE,outlineWidth:2,disableDepthTestDistance:Number.POSITIVE_INFINITY},label:{text:p.name,font:'12px sans-serif',fillColor:C.Color.fromCssColorString('#28473c'),outlineColor:C.Color.WHITE,outlineWidth:3,style:C.LabelStyle.FILL_AND_OUTLINE,pixelOffset:new C.Cartesian2(0,-16),distanceDisplayCondition:new C.DistanceDisplayCondition(10000,1800000),disableDepthTestDistance:Number.POSITIVE_INFINITY}}));
      v.screenSpaceEventHandler.setInputAction((event:{position:C.Cartesian2})=>{
        const picked=v.scene.pick(event.position);
        if(picked instanceof C.Cesium3DTileFeature){
          const properties=Object.fromEntries(picked.getPropertyIds().map(name=>[name,picked.getProperty(name)]));
          selectCallback.current(selectedProperties(String(properties.source_record_id??''),properties,{source_id:'overture',version:String(properties.dataset_version??'미확인')}));
        }else if(typeof picked?.id==='string'&&liveBus.current?.picks.has(picked.id)){
          selectCallback.current(liveBus.current.picks.get(picked.id)!);
        }else if(typeof picked?.id==='string'&&pickMap.current.has(picked.id)){
          const feature=pickMap.current.get(picked.id)!;
          selectCallback.current(selectionFromProperties(feature.sourceId,feature.properties,feature.asset));
        }else if(picked?.id instanceof C.Entity){
          const entity=picked.id as C.Entity;
          const data=entity.properties?.getValue(v.clock.currentTime)??{};
          if(entity.id.startsWith('place:')){const p=PLACES.find(p=>`place:${p.id}`===entity.id);if(p)flyTo(p);return;}
          selectCallback.current({name:data.name||entity.name||'지도 객체',detail:data.description||'원천 자료에 포함된 공간 형상입니다.',height:typeof data.height==='number'?data.height:undefined,provenance:data.provenance??{source_id:data.source_id,dataset_version:data.dataset_version,evidence_type:data.evidence_type??'unverified'}});
        }
      },C.ScreenSpaceEventType.LEFT_CLICK);
      statusCallback.current('공개 공간 자료를 불러오는 중');
    }catch(error){admission.dispose();if(primitiveAdmission.current===admission)primitiveAdmission.current=null;if(updateGeometryVisibility)document.removeEventListener('visibilitychange',updateGeometryVisibility);geometryGate.dispose();sceneLabels?.destroy();labelScheduler.current=null;const failedViewer=viewer.current;viewer.current=null;if(failedViewer&&!failedViewer.isDestroyed())failedViewer.destroy();Object.assign(C.RequestScheduler,previousRequests);statusCallback.current(`3D 화면을 시작하지 못했습니다: ${error instanceof Error?error.message:'WebGL 확인이 필요합니다.'}`);return;}
    const releaseDormant=()=>primitiveCache.clear();
    document.addEventListener('visibilitychange',updateCacheLimits);window.addEventListener('memorypressure',releaseDormant);
    return()=>{admission.dispose();if(primitiveAdmission.current===admission)primitiveAdmission.current=null;cameraSettler?.dispose();if(updateGeometryVisibility)document.removeEventListener('visibilitychange',updateGeometryVisibility);document.removeEventListener('visibilitychange',updateCacheLimits);window.removeEventListener('memorypressure',releaseDormant);for(const job of activeJobs.values())job.controller.abort();geometryGate.dispose();activeJobs.clear();wanted.current.clear();geometryClient.destroy();loadQueue.cancelAll();commitFrames.cancel();for(const resource of resources.current)disposePrimitive(resource);primitiveCache.clear();sceneLabels?.destroy();if(labelScheduler.current===sceneLabels)labelScheduler.current=null;activePicks.clear();activeStagedRoots.clear();resources.current=[];tracks.current=[];frames.current=[];initialWeatherLayers.clear();viewer.current=null;movingRef.current=false;if(!v.isDestroyed())v.destroy();Object.assign(C.RequestScheduler,previousRequests);};
  },[loadQueue,commitFrames,geometryClient,geometryGate,applyVisualQuality,workBudget,disposePrimitive,primitiveCache,updateCacheLimits,invalidateGround,settleGround]);
  useEffect(()=>{
    const v=viewer.current;if(!v)return;
    const assets=selectionRef.current.assets,releaseId=props.catalog.release_id;
    const keyFor=(asset:Asset)=>primitiveCacheKey(releaseId,asset,distantOverview);
    const resourceKey=(resource:Resource)=>primitiveCacheKey(resource.releaseId,resource.asset,Boolean(resource.distantOverview));
    const desired=new Set(assets.map(keyFor));wanted.current=desired;
    const labelLimit=Math.max(2,Math.floor(QUALITY[qualityRef.current].labels/Math.max(1,assets.filter(a=>a.format==='geojson').length)));
    updateCacheLimits();
    primitiveCache.retain(resource=>resource.releaseId===releaseId&&props.layers[resource.asset.layer]);
    // Keep requests shared by old and new views. Abort only assets leaving the view.
    for(const [key,job] of jobs.current)if(!desired.has(key)){job.controller.abort();jobs.current.delete(key);}
    // Reserve returning objects before departing objects can evict them from the LRU.
    const activeKeys=new Set(resources.current.map(resourceKey)),returning:Resource[]=[];
    for(const asset of assets)if(isBatchedGeometry(asset)&&!activeKeys.has(keyFor(asset))&&!jobs.current.has(keyFor(asset))){
      const resource=primitiveCache.take(keyFor(asset));if(resource)returning.push(resource);
    }
    const retained:Resource[]=[];
    for(const resource of resources.current){
      const key=resourceKey(resource);
      if(desired.has(key)){retained.push(resource);continue;}
      if(resource.source)v.dataSources.remove(resource.source,true);
      if(resource.primitive){
        resource.primitive.setActive(false);
        // Hidden collections stop Cesium preparation. Never let a dormant cache
        // retain unfinished GPU work and occupy every scene admission slot.
        if(resource.primitive.preparationReady&&resource.releaseId===releaseId&&props.layers[resource.asset.layer])primitiveCache.put(key,resource,primitiveCacheCost(resource.asset,resource.primitive));
        else disposePrimitive(resource);
      }
      if(resource.tileset)v.scene.primitives.remove(resource.tileset);
      if(resource.imagery)v.imageryLayers.remove(resource.imagery,true);
    }
    resources.current=[...retained,...returning];
    for(const resource of resources.current){resource.primitive?.setGroundRefreshPaused(movingRef.current||terrainRevisions.current.pending);resource.primitive?.setActive(true,labelLimit);}
    for(const resource of returning)resource.primitive?.refreshGround(terrainRevisions.current.revision);
    const ids=new Set(resources.current.map(r=>r.asset.id));
    for(const item of tracks.current)if(!ids.has(item.assetId))v.entities.remove(item.entity);
    tracks.current=tracks.current.filter(item=>ids.has(item.assetId));frames.current=frames.current.filter(item=>ids.has(item.asset.id));
    tilesetCount.current=assets.filter(a=>a.format==='3d-tiles').length;rebalanceTilesets();commitFrames.notify();
    const failures:string[]=[];
    const report=()=>{if(v.isDestroyed())return;const detail=selectionRef.current.deferred;statusCallback.current(failures.length?`자료 오류 · ${failures[0]}`:`${resources.current.length}개 자료 연결${detail?` · ${detail}개 상세 자료는 확대해 확인`:''}`);};
    for(const asset of assets){
      const key=keyFor(asset);
      if(resources.current.some(resource=>resourceKey(resource)===key)||jobs.current.has(key))continue;
      const controller=new AbortController();
      const promise=loadQueue.enqueue(key,async signal=>{
        const stale=()=>signal.aborted||v.isDestroyed()||viewer.current!==v||!wanted.current.has(key);
        const commit=(resource:Omit<Resource,'releaseId'>)=>{resources.current.push({...resource,releaseId});commitFrames.notify();report();};
        try{
          if(stale())return;
          if(asset.format==='quantized-mesh'){
            const terrain=await C.CesiumTerrainProvider.fromUrl(asset.url.replace(/layer\.json$/,''),{requestVertexNormals:true});
            if(!stale()){v.terrainProvider=layersRef.current.terrain?terrain:new C.EllipsoidTerrainProvider();invalidateGround();commit({asset,terrain});}
          }else if(asset.format==='3d-tiles'){
            const budget=tilesetBudget(tilesetCount.current,qualityRef.current==='low');
            // Traditional REPLACE traversal downloads every sibling before refining,
            // including buildings outside the view. Skip traversal keeps the coarse
            // parent while only visible descendants stream within the memory budget.
            const tileset=await C.Cesium3DTileset.fromUrl(asset.url,{maximumScreenSpaceError:sceneQuality(qualityRef.current,movingRef.current,false).sse,...budget,
              skipLevelOfDetail:true,loadSiblings:false,preloadFlightDestinations:false});
            if(stale()){tileset.destroy();return;}
            tileset.show=layersRef.current[asset.layer];v.scene.primitives.add(tileset);commit({asset,tileset});
          }else if(isBatchedGeometry(asset)){
            await geometryGate.wait(signal);if(stale())return;
            const data=await geometryClient.load(asset.url,asset.layer==='infrastructure'&&distantOverview,signal);
            await geometryGate.wait(signal);
            if(stale())return;
            let stagedRoot:C.PrimitiveCollection|undefined;
            const detachStaged=()=>{
              const root=stagedRoot;if(!root)return;
              stagedRoot=undefined;stagedRoots.current.delete(root);
              if(!v.isDestroyed()){v.scene.primitives.remove(root);v.scene.requestRender();}
            };
            const primitive=await buildPrimitives(data,asset,v,{signal,budget:geometryBudget,labelLimit,pickMap:pickMap.current,labelScheduler:labelScheduler.current??undefined,admission:primitiveAdmission.current??undefined,
              groundRevision:terrainRevisions.current.revision,groundRefreshPaused:movingRef.current||terrainRevisions.current.pending,
              groundState:()=>({revision:terrainRevisions.current.revision,paused:movingRef.current||terrainRevisions.current.pending}),
              stageRoot:root=>{
                if(stale())throw new DOMException('Aborted','AbortError');
                v.scene.primitives.add(root);stagedRoot=root;stagedRoots.current.add(root);v.scene.requestRender();
                return detachStaged;
              }});
            if(stale()){detachStaged();primitive.destroy();return;}
            primitive.setGroundRefreshPaused(movingRef.current||terrainRevisions.current.pending);primitive.setActive(layersRef.current[asset.layer],labelLimit);
            if(stagedRoot!==primitive.root)v.scene.primitives.add(primitive.root);
            stagedRoots.current.delete(primitive.root);stagedRoot=undefined;
            commit({asset,primitive,distantOverview});primitive.refreshGround(terrainRevisions.current.revision);
          }else if(asset.format==='geojson'){
            const response=await fetch(asset.url,{signal});if(!response.ok)throw new Error(`HTTP ${response.status}`);
            const data=await response.json();if(stale())return;
            if(asset.source_id==='natural-earth'){
              const canvas=document.createElement('canvas');canvas.width=2048;canvas.height=1600;
              const ctx=canvas.getContext('2d')!;ctx.fillStyle='#dce4cd';const [west,south,east,north]=asset.bbox;
              const country=data as {features:{geometry:{type:string;coordinates:number[][][]|number[][][][]}}[]};
              for(const feature of country.features){const polygons=(feature.geometry.type==='MultiPolygon'?feature.geometry.coordinates:[feature.geometry.coordinates]) as number[][][][];
                for(const polygon of polygons){ctx.beginPath();for(const ring of polygon){ring.forEach(([lon,lat]:number[],i:number)=>{const x=(lon-west)/(east-west)*canvas.width,y=(north-lat)/(north-south)*canvas.height;if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.closePath();}ctx.fill('evenodd');}}
              const provider=await C.SingleTileImageryProvider.fromUrl(canvas.toDataURL('image/png'),{rectangle:C.Rectangle.fromDegrees(...asset.bbox)});
              if(stale())return;const imagery=v.imageryLayers.addImageryProvider(provider,0);imagery.show=layersRef.current.terrain;commit({asset,imagery});
            }else{
              // Depth is a small, special surface-to-underground representation.
              const source=await C.GeoJsonDataSource.load(data,{clampToGround:false});
              if(stale()){source.entities.removeAll();return;}
              for(const entity of source.entities.values){
                const properties=entity.properties?.getValue(v.clock.currentTime)??{};
                if(entity.polyline){entity.polyline.clampToGround=new C.ConstantProperty(false);entity.polyline.width=new C.ConstantProperty(5);entity.polyline.material=new C.ColorMaterialProperty(C.Color.fromCssColorString('#8969ae'));entity.polyline.arcType=new C.ConstantProperty(C.ArcType.NONE);}
                if(entity.position){
                  entity.billboard=undefined;const coordinates=C.Cartographic.fromCartesian(entity.position.getValue(v.clock.currentTime)!);
                  entity.position=new C.ConstantPositionProperty(C.Cartesian3.fromRadians(coordinates.longitude,coordinates.latitude,properties.display_height));
                  entity.point=new C.PointGraphics({pixelSize:9,color:C.Color.fromCssColorString('#956449'),outlineColor:C.Color.WHITE,outlineWidth:2,heightReference:C.HeightReference.NONE});
                  entity.label=new C.LabelGraphics({text:properties.name,font:'12px sans-serif',fillColor:C.Color.fromCssColorString('#59432e'),outlineColor:C.Color.WHITE,outlineWidth:3,style:C.LabelStyle.FILL_AND_OUTLINE,pixelOffset:new C.Cartesian2(0,-17),distanceDisplayCondition:new C.DistanceDisplayCondition(0,6000)});
                  entity.polyline=new C.PolylineGraphics({positions:[C.Cartesian3.fromRadians(coordinates.longitude,coordinates.latitude,properties.surface_height),C.Cartesian3.fromRadians(coordinates.longitude,coordinates.latitude,properties.display_height)],width:2,material:C.Color.fromCssColorString('#956449')});
                }
                await workBudget.checkpoint(signal);
              }
              source.show=layersRef.current[asset.layer];await v.dataSources.add(source);
              if(stale()){if(!v.isDestroyed())v.dataSources.remove(source,true);return;}commit({asset,source});
            }
          }else if(asset.format==='imagery'){
            const response=await fetch(asset.url,{signal});if(!response.ok)throw new Error(`HTTP ${response.status}`);
            const manifest=await response.json() as WeatherManifest;
            if(manifest.schema_version!==1||!Array.isArray(manifest.frames))throw new Error('기상 프레임 형식 오류');
            for(const frame of manifest.frames)if(!frame.url.startsWith('/data/')||frame.url.includes('..')||frame.url.includes('\\')||!Number.isFinite(Date.parse(frame.time))||!Number.isFinite(Date.parse(frame.valid_until))||Date.parse(frame.valid_until)<=Date.parse(frame.time))throw new Error('기상 프레임 시각 오류');
            if(!stale()){for(const frame of manifest.frames)frames.current.push({asset,frame,time:Date.parse(frame.time),until:Date.parse(frame.valid_until)});frames.current.sort((a,b)=>b.time-a.time);commit({asset});}
          }else if(asset.format==='replay'){
            const response=await fetch(asset.url,{signal});if(!response.ok)throw new Error(`HTTP ${response.status}`);
            const chunk=await response.json() as ReplayChunk;if(stale())return;
            if(chunk.schema_version!==1||!Array.isArray(chunk.tracks))throw new Error('관측 경로 형식 오류');
            const staged:typeof tracks.current=[],finish=workBudget.startTask();let committed=false;
            try{
              for(const track of chunk.tracks){
                await workBudget.checkpoint(signal);
                const prepared=prepareTrack(track),position=new C.ConstantPositionProperty(),cartesian=new C.Cartesian3();
                const entity=v.entities.add({id:`track:${asset.id}:${track.id}`,name:track.label,show:false,position,point:{pixelSize:10,color:track.layer==='rail'?C.Color.fromCssColorString('#b56045'):C.Color.fromCssColorString('#407f84'),outlineColor:C.Color.WHITE,outlineWidth:2,disableDepthTestDistance:10000,heightReference:track.layer==='bus'||track.points.every(p=>p.height===undefined)?C.HeightReference.CLAMP_TO_GROUND:C.HeightReference.NONE},properties:{name:track.label,description:track.description??'수집된 위치 기록 사이의 계산 위치입니다.',provenance:{...track.provenance,evidence_type:track.position_evidence}}});
                staged.push({assetId:asset.id,track,prepared,entity,position,cartesian});
              }
              if(stale())return;tracks.current.push(...staged);commit({asset});committed=true;
            }finally{finish();if(!committed&&!v.isDestroyed())for(const item of staged)v.entities.remove(item.entity);}
          }
          return true;
        }catch(error){if(stale())return;const message=(error instanceof Error?error.message:'불러오기 실패').replace(/([?&](?:key|token|authKey|access_token)=)[^&\s]*/gi,'$1[redacted]');failures.push(`${asset.id}: ${message}`);console.warn('[KOREA REPLAY] asset load failed',{id:asset.id,error:message});report();return false;}
      },controller.signal);
      const job={controller,promise};jobs.current.set(key,job);
      void promise.finally(()=>{if(jobs.current.get(key)===job)jobs.current.delete(key);});
    }
    report();
    // Opening can release promise continuations immediately. Wanted keys, stale
    // job cancellation and cache transfers must all be complete before this call.
    geometryGate.commitView(view.workRevision);
    // Individual controllers belong to asset keys, not this effect generation.
  },[visibleIds,distantOverview,view.workRevision,props.catalog.release_id,props.layers,quality,loadQueue,commitFrames,rebalanceTilesets,geometryClient,geometryGate,geometryBudget,workBudget,primitiveCache,disposePrimitive,updateCacheLimits,invalidateGround]);
  useEffect(()=>{
    const v=viewer.current;if(!v)return;
    rebalanceTilesets();
    v.scene.globe.enableLighting=props.layers.sun;
    v.scene.globe.translucency.enabled=props.layers.depth&&resources.current.some(resource=>resource.asset.layer==='depth'&&(resource.source||resource.primitive));
    v.scene.globe.translucency.frontFaceAlpha=.35;
    const terrain=resources.current.find(resource=>resource.terrain)?.terrain;
    if(props.layers.terrain&&terrain&&v.terrainProvider!==terrain){v.terrainProvider=terrain;invalidateGround();}
    else if(!props.layers.terrain&&!(v.terrainProvider instanceof C.EllipsoidTerrainProvider)){v.terrainProvider=new C.EllipsoidTerrainProvider();invalidateGround();}
    for(const resource of resources.current){const show=props.layers[resource.asset.layer];if(resource.source)resource.source.show=show;resource.primitive?.setActive(show);if(resource.imagery)resource.imagery.show=show;if(resource.tileset)resource.tileset.show=show;}
    applyVisualQuality();
  },[props.layers,quality,resourceRevision,rebalanceTilesets,applyVisualQuality,invalidateGround]);
  useEffect(()=>{
    const v=viewer.current;if(!v)return;
    v.clock.currentTime=C.JulianDate.fromDate(new Date(props.instant),v.clock.currentTime);
    for(const item of tracks.current){
      const p=props.mode==='replay'&&props.layers[item.track.layer]?preparedTrackPosition(item.prepared,props.instant):null;
      item.entity.show=Boolean(p);if(p){C.Cartesian3.fromDegrees(p.lon,p.lat,p.height+4,C.Ellipsoid.WGS84,item.cartesian);item.position.setValue(item.cartesian);}
    }
    for(const kind of ['satellite','radar'] as const){
      const match=props.mode==='replay'&&props.layers[kind]?frames.current.find(f=>f.asset.layer===kind&&f.time<=props.instant&&f.until>props.instant):undefined;
      const previous=weatherLayers.current.get(kind),key=match?.frame.url??'';
      if(previous?.key===key)continue;
      if(previous?.layer)v.imageryLayers.remove(previous.layer,true);
      const state:{key:string;layer?:C.ImageryLayer}={key};weatherLayers.current.set(kind,state);
      if(match)C.SingleTileImageryProvider.fromUrl(key,{rectangle:C.Rectangle.fromDegrees(...match.frame.bbox)}).then(provider=>{
        if(v.isDestroyed()||weatherLayers.current.get(kind)!==state)return;
        const layer=v.imageryLayers.addImageryProvider(provider);layer.alpha=.85;state.layer=layer;v.scene.requestRender();
      }).catch(()=>statusCallback.current('기상 영상 프레임을 불러오지 못했습니다.'));
    }
    v.scene.requestRender();
  },[props.instant,props.layers,props.mode,resourceRevision]);
  useEffect(()=>{
    const v=viewer.current;if(!v||v.isDestroyed())return;
    if(element.current)element.current.dataset.liveBusCount='0';
    const snapshot=props.liveTransit;if(!snapshot||snapshot.kind!=='bus')return;
    const layer=createLiveBusLayer(v,snapshot,{budget:workBudget,groundState:()=>({paused:document.hidden||movingRef.current||terrainRevisions.current.pending,revision:terrainRevisions.current.revision}),
      onCount:count=>{if(element.current)element.current.dataset.liveBusCount=String(count);}});
    liveBus.current=layer;v.scene.primitives.add(layer.root);
    void layer.ready.catch(()=>{if(!v.isDestroyed())statusCallback.current('현재 버스 위치를 표시하지 못했습니다.');});
    const visibility=()=>{if(!layer.root.isDestroyed())layer.root.show=!document.hidden;layer.setGroundRefreshPaused(document.hidden||movingRef.current||terrainRevisions.current.pending);if(!document.hidden&&!terrainRevisions.current.pending&&!movingRef.current)layer.refreshGround(terrainRevisions.current.revision);};
    document.addEventListener('visibilitychange',visibility);visibility();
    return()=>{document.removeEventListener('visibilitychange',visibility);if(liveBus.current===layer)liveBus.current=null;if(!v.isDestroyed())v.scene.primitives.remove(layer.root);layer.destroy();};
  },[props.liveTransit,workBudget]);
  return <div ref={element} className="map-scene" aria-label="대한민국 3차원 지도"/>;
});
export default MapScene;
