import {forwardRef,useEffect,useImperativeHandle,useMemo,useRef} from 'react';
import {Map as LibreMap,NavigationControl,ScaleControl,AttributionControl,setWorkerUrl,addProtocol,removeProtocol,type MapMouseEvent,type LayerSpecification,type GeoJSONSource} from 'maplibre-gl';
import libreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import type {Asset,BBox,Catalog,LayerId,Place} from '../shared/contracts';
import type {LiveTransitSnapshot} from '../shared/live-transit';
import {QUALITY,type PerformanceSnapshot} from '../shared/map-performance';
import {createMap2DFetcher,createMap2DPixelRatioController,MAP2D_ATTRIBUTION,map2DHeight,map2DKey,map2DLayers,map2DZoom,map2DOverviewPadding,map2DPixelRatio,readFlatCamera,selectMap2DAssets,type FlatCamera} from '../shared/map2d';
import type {Selection} from './App';
import type {MapHandle} from './MapScene';
import type {Map2DWorkerRequest,Map2DWorkerResponse} from './map2d-data.worker';
import {resolveSceneAssets} from './catalog';
import {EMPTY_MEASUREMENT,measureMap,measurementGeoJSON,type Measurement} from '../shared/map-tools';
import type {MapCatalog2D} from '../shared/map-tiles';
import {createMapTilesProtocol} from './map-tiles-protocol';
import {atlasFetch} from './atlas-client';
import {vectorLayers} from './vector-style';
import {map2DLiveBuses,map2DLiveBusSelection} from './map2d-live';
import type {PropertyRegions,PropertyRelease} from '../shared/property';
import {pickedPropertyProvince,pickedPropertyRegion,provinceMapLayer,regionMapBubbleImage,regionMapData,regionMapLayer,PROVINCE_MAP_LAYER,REGION_MAP_IMAGE,REGION_MAP_LAYER,REGION_MAP_SOURCE} from './region-map-layer';

// Bundle the v6 worker and its shared ESM dependency for both dev and production.
setWorkerUrl(libreWorkerUrl);

interface Props {catalog:Catalog;layers:Record<LayerId,boolean>;boundaries?:boolean;overviewPanelVisible?:boolean;focused?:boolean;instant:number;mode:'replay'|'sun';liveTransit?:LiveTransitSnapshot|null;initialPlace:Place;initialFlatCamera?:FlatCamera|null;measurement?:Measurement;onMeasurement?:(value:Measurement)=>void;vectorData?:{map:MapCatalog2D;origin:string;property?:PropertyRelease;regions?:PropertyRegions}|null;vectorPending?:boolean;lightweight:boolean;propertyTrade?:'sale'|'rent';onSelect:(value:Selection)=>void;onPropertyRegion?:(code:string)=>void;onPropertyComplex?:(regionCode:string,complexId:string)=>void;onStatus:(value:string)=>void;onPerformance?:(value:PerformanceSnapshot)=>void;}
interface Resource {asset:Asset;source:string;layerIds:string[];url:string;record:number;features:number;vertices:number;}
type Loaded=Extract<Map2DWorkerResponse,{type:'loaded'}>;
const abortError=()=>new DOMException('Aborted','AbortError');
const anchors=['land','water','area','road','point'] as const;
const SEOUL_KAPT_SOURCE='seoul-kapt-provider-points';
const SEOUL_KAPT_SELECTED='seoul-kapt-selected-point';
const SEOUL_KAPT_URL=new URL('./data/seoul-kapt-points-33058dae0a1d86c3.geojson',import.meta.url).href;
// Fit the mainland and Jeju for legible national discovery. Offshore islands
// remain in the same world and can be reached through search or panning.
const NATIONAL_OVERVIEW:[[number,number],[number,number]]=[[125,33],[130.2,38.7]];
export const map2DSourceLayers=(source:string,asset:Asset):{layer:LayerSpecification;before:string}[]=>{
  const fillAnchor=asset.source_id==='natural-earth'?'land':asset.layer==='terrain'?'water':'area';
  return [
    {before:`map2d-anchor-${fillAnchor}`,layer:{id:`${source}-fill`,type:'fill',source,filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':['match',['get','map2d_category'],'land','#f8fafc','water','#c9e3f3','building','#dce3ec','#e5f0e8'],'fill-opacity':.94}}},
    {before:'map2d-anchor-road',layer:{id:`${source}-line`,type:'line',source,filter:['==',['geometry-type'],'LineString'],layout:{'line-cap':'round','line-join':'round'},paint:{'line-color':['match',['get','map2d_category'],'rail','#87645e','water','#b3d4eb',['match',['get','map2d_road'],'motorway','#db8773','trunk','#e2ab72','primary','#e6c47b','secondary','#e8d59e','#fffdf6']],'line-width':['interpolate',['linear'],['zoom'],3,.45,9,['match',['get','map2d_road'],'motorway',2,'trunk',1.8,'primary',1.5,1],15,['match',['get','map2d_road'],'motorway',6,'trunk',5,'primary',4,'secondary',3,2],19,8],'line-opacity':.96}}},
    {before:'map2d-anchor-point',layer:{id:`${source}-point`,type:'circle',source,filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':['interpolate',['linear'],['zoom'],3,1.5,12,3,17,5],'circle-color':['match',['get','map2d_category'],'rail','#835e58','building','#a2b1c1','#91a7b6'],'circle-stroke-width':1,'circle-stroke-color':'#ffffff'}}},
  ];
};

const Map2D=forwardRef<MapHandle,Props>(function Map2D(props,ref){
  const element=useRef<HTMLDivElement>(null),mapRef=useRef<LibreMap|null>(null),refreshRef=useRef<(()=>void)|null>(null);
  const latest=useRef(props);latest.current=props;
  const regions=useMemo(()=>{const input=props.vectorData;return regionMapData(input?.property&&input.regions?{map:input.map,property:input.property,regions:input.regions,trade:props.propertyTrade}:null);},[props.vectorData,props.propertyTrade]);
  const regionDataRef=useRef(regions),refreshRegionsRef=useRef<(()=>void)|null>(null);regionDataRef.current=regions;
  useImperativeHandle(ref,()=>({
    flyTo:(place:Place,options)=>{
      const map=mapRef.current;if(!map)return;
      const node=map.getContainer(),padding=map2DOverviewPadding(node.clientWidth,node.clientHeight,options?.overviewPanelVisible??!!latest.current.overviewPanelVisible,options?.focused??latest.current.focused);
      if(place.id==='korea')map.fitBounds(NATIONAL_OVERVIEW,{padding,duration:700});
      // Offset the target into the unobscured viewport without persisting camera
      // padding, which would otherwise shift later national fits/shared cameras.
      else map.flyTo({center:[place.lon,place.lat],offset:[(padding.left-padding.right)/2,(padding.top-padding.bottom)/2],zoom:map2DZoom(place.lat,place.range,Math.max(64,Math.min(node.clientHeight-padding.top-padding.bottom,node.clientWidth-padding.left-padding.right))),bearing:0,pitch:0,duration:700});
    },
    north:()=>{mapRef.current?.easeTo({bearing:0,duration:450});},
    overhead:()=>{mapRef.current?.easeTo({bearing:0,pitch:0,duration:450});},
    camera:()=>null,
    flatCamera:()=>{const map=mapRef.current;if(!map)return null;const center=map.getCenter();return [center.lng,center.lat,map.getZoom(),map.getBearing()] as FlatCamera;},
    viewport:()=>{const map=mapRef.current;if(!map)return null;const center=map.getCenter();return {...latest.current.initialPlace,lon:center.lng,lat:center.lat,range:Math.max(100,Math.min(2500000,map2DHeight(center.lat,map.getZoom(),map.getContainer().clientHeight)))};},
  }),[]);
  useEffect(()=>{
    const node=element.current;if(!node)return;
    // Create all lifetime resources inside the effect so development StrictMode can replay it.
    const initial=latest.current.initialPlace,shared=latest.current.initialFlatCamera===undefined?readFlatCamera(location.hash):latest.current.initialFlatCamera,downloads=createMap2DFetcher(atlasFetch);
    let map:LibreMap;
    try{map=new LibreMap({container:node,center:shared?[shared[0],shared[1]]:[initial.lon,initial.lat],zoom:shared?.[2]??map2DZoom(initial.lat,initial.range,node.clientHeight),bearing:shared?.[3]??0,pitch:0,minZoom:3,maxZoom:19,maxPitch:0,pixelRatio:map2DPixelRatio(devicePixelRatio),renderWorldCopies:false,attributionControl:false,
      locale:{'NavigationControl.ZoomIn':'확대','NavigationControl.ZoomOut':'축소','NavigationControl.ResetBearing':'북쪽으로','AttributionControl.ToggleAttribution':'지도 출처'},
      style:{version:8,sources:{},layers:[{id:'map2d-ocean',type:'background',paint:{'background-color':'#e7edf5'}},...anchors.map(name=>({id:`map2d-anchor-${name}`,type:'background' as const,paint:{'background-opacity':0}}))]}});}
    catch{downloads.dispose();latest.current.onStatus('2D 지도를 시작하지 못했습니다. 브라우저의 그래픽 가속 상태를 확인해 주세요.');return;}
    mapRef.current=map;if(initial.id==='korea'&&!shared)map.fitBounds(NATIONAL_OVERVIEW,{padding:map2DOverviewPadding(node.clientWidth,node.clientHeight,!!latest.current.overviewPanelVisible,latest.current.focused),duration:0});map.addControl(new NavigationControl({visualizePitch:false}),'bottom-right');map.addControl(new ScaleControl({unit:'metric',maxWidth:100}),'bottom-left');map.addControl(new AttributionControl({compact:true}),'bottom-right');
    let worker:Worker;
    try{worker=new Worker(new URL('./map2d-data.worker.ts',import.meta.url),{type:'module'});}catch{map.remove();mapRef.current=null;downloads.dispose();latest.current.onStatus('2D 공간 처리기를 시작하지 못했습니다.');return;}
    const resolution=createMap2DPixelRatioController(map,()=>devicePixelRatio);
    let disposed=false,ready=false,moving=false,indexLoading=false,workerFailed=false,release='',sequence=0,revision=0,deferred=0,errors=0,frames=0,lastMovingFrame:number|null=null;
    let settleTimer:ReturnType<typeof setTimeout>|undefined,indexController:AbortController|undefined,commitFrame:number|undefined,pickController:AbortController|undefined,liveTimer:ReturnType<typeof setInterval>|undefined;
    let resolved:Asset[]=[],wanted=new Set<string>();
    let vectorProtocol:ReturnType<typeof createMapTilesProtocol>|undefined,vectorRelease='',firstReadyRelease='';
    let installedRegions:typeof regions|undefined;
    const vectorSourceIds:string[]=[],vectorLayerIds:string[]=[];
    const resources=new Map<string,Resource>(),jobs=new Map<string,{controller:AbortController}>(),frameSamples:number[]=[];
    const pending=new Map<number,{resolve:(value:Map2DWorkerResponse)=>void;reject:(error:unknown)=>void;detach:()=>void}>();
    const waiters=new Set<{resume:()=>void;reject:()=>void}>();
    const budget=()=>QUALITY[latest.current.lightweight?'low':'balanced'];
    const mark=()=>{
      if(disposed)return;
      const rows=[...resources.values()],network=downloads.stats(),sourcesLoading=rows.filter(row=>!map.isSourceLoaded(row.source)).length;
      if(vectorProtocol){const stat=vectorProtocol.snapshot();Object.assign(node.dataset,{vectorRelease,vectorCachedBytes:String(stat.cachedBytes),vectorActive:String(stat.active),vectorPeak:String(stat.peakActive),vectorArchives:String(stat.cachedArchives)});}
      Object.assign(node.dataset,{mapEngine:'maplibre',mapDimension:'2d',map2dPixelRatio:String(map.getPixelRatio()),map2dAssets:String(rows.length),map2dFeatures:String(rows.reduce((sum,row)=>sum+row.features,0)),map2dVertices:String(rows.reduce((sum,row)=>sum+row.vertices,0)),map2dJobs:String(jobs.size),map2dSourcesLoading:String(sourcesLoading),map2dIndexLoading:String(indexLoading),map2dMoving:String(moving),map2dErrors:String(errors),map2dFetchActive:String(network.active),map2dFetchPeak:String(network.peak),map2dFrames:String(frames),map2dMovingSamples:String(frameSamples.length),map2dMovingP95Ms:frameSamples.length?[...frameSamples].sort((a,b)=>a-b)[Math.floor((frameSamples.length-1)*.95)].toFixed(2):'0',map2dRelease:release});
      latest.current.onStatus(vectorProtocol?`전국 벡터 지도 · ${vectorSourceIds.length}개 주제${vectorProtocol.snapshot().active?' · 화면 자료 불러오는 중':''}${errors?` · ${errors}개 자료 오류`:''}`:`${rows.length}개 2D 자료 연결${indexLoading||jobs.size||sourcesLoading?' · 지역 자료 불러오는 중':''}${deferred?` · ${deferred}개 상세 자료 표시 대기`:''}${errors?` · ${errors}개 자료 오류`:''}`);
    };
    const report=()=>{if(commitFrame===undefined&&!disposed)commitFrame=requestAnimationFrame(()=>{commitFrame=undefined;mark();});};
    const waitForView=(signal:AbortSignal):Promise<void>=>{
      if(disposed||signal.aborted)return Promise.reject(abortError());
      if(!moving&&!document.hidden)return Promise.resolve();
      return new Promise((resolve,reject)=>{
        const finish=(cancelled=false)=>{waiters.delete(waiter);signal.removeEventListener('abort',abort);if(cancelled)reject(abortError());else resolve();};
        const waiter={resume:()=>finish(),reject:()=>finish(true)},abort=()=>waiter.reject();
        waiters.add(waiter);signal.addEventListener('abort',abort,{once:true});
      });
    };
    const post=(request:Map2DWorkerRequest,signal:AbortSignal,transfer:Transferable[]=[]):Promise<Map2DWorkerResponse>=>{
      if(signal.aborted||disposed)return Promise.reject(abortError());
      if(workerFailed)return Promise.reject(new Error('2D 공간 처리기를 사용할 수 없습니다.'));
      return new Promise((resolve,reject)=>{
        const abort=()=>{pending.delete(request.id);signal.removeEventListener('abort',abort);worker.postMessage({id:request.id,type:'cancel'} satisfies Map2DWorkerRequest);reject(abortError());};
        pending.set(request.id,{resolve,reject,detach:()=>signal.removeEventListener('abort',abort)});signal.addEventListener('abort',abort,{once:true});worker.postMessage(request,transfer);
      });
    };
    worker.onmessage=(event:MessageEvent<Map2DWorkerResponse>)=>{const result=event.data,task=pending.get(result.id);if(!task){if(result.type==='loaded')worker.postMessage({type:'release',id:result.id} satisfies Map2DWorkerRequest);return;}pending.delete(result.id);task.detach();if(result.type==='error')task.reject(new Error(result.error));else task.resolve(result);};
    worker.onerror=()=>{workerFailed=true;for(const task of pending.values()){task.detach();task.reject(new Error('2D 공간 처리기를 사용할 수 없습니다.'));}pending.clear();errors++;report();};
    const remove=(key:string)=>{
      const resource=resources.get(key);if(!resource)return;
      resources.delete(key);for(const id of resource.layerIds)if(map.getLayer(id))map.removeLayer(id);
      if(map.getSource(resource.source))map.removeSource(resource.source);
      URL.revokeObjectURL(resource.url);worker.postMessage({type:'release',id:resource.record} satisfies Map2DWorkerRequest);
    };
    const prune=()=>{if(indexLoading||jobs.size)return;for(const key of resources.keys())if(!wanted.has(key))remove(key);report();};
    const makeRoom=(asset:Asset,vertices:number)=>{
      const limits=budget(),cost=(row:Resource)=>row.asset.source_id==='natural-earth'?0:row.asset.byte_length??0;
      const fits=()=>{const rows=[...resources.values()].filter(row=>row.asset.source_id!=='natural-earth');return rows.reduce((n,r)=>n+cost(r),0)+(asset.byte_length??0)<=limits.bytes&&rows.reduce((n,r)=>n+r.vertices,0)+vertices<=limits.vertices&&rows.length+1<=limits.files;};
      if(asset.source_id==='natural-earth')return;
      for(const key of resources.keys()){if(fits())break;if(!wanted.has(key))remove(key);}
      if(!fits())throw new Error('2D 표시 예산을 초과했습니다.');
    };
    const load=(asset:Asset)=>{
      const key=map2DKey(asset);if(resources.has(key)||jobs.has(key)&&!jobs.get(key)!.controller.signal.aborted)return;
      const controller=new AbortController(),job={controller},record=++sequence,loadRelease=release;
      jobs.set(key,job);
      void(async()=>{
        let loaded:Loaded|undefined,url:string|undefined,source:string|undefined,installed=false;
        try{
          await waitForView(controller.signal);
          const response=await downloads.fetcher(asset.url,{signal:controller.signal});if(!response.ok)throw new Error(`공간 자료 HTTP ${response.status}`);
          const bytes=await response.arrayBuffer();await waitForView(controller.signal);
          const result=await post({type:'load',id:record,asset,bytes},controller.signal,[bytes]);
          if(result.type!=='loaded')throw new Error('2D 공간 처리 응답 오류');loaded=result;
          await waitForView(controller.signal);
          if(disposed||controller.signal.aborted||release!==loadRelease||!wanted.has(key))throw abortError();
          makeRoom(asset,loaded.vertexCount);
          url=URL.createObjectURL(new Blob([loaded.bytes],{type:'application/geo+json'}));source=`map2d-${record}`;
          // Passing a URL lets MapLibre parse/tile in its worker, avoiding main-thread JSON.parse/stringify.
          map.addSource(source,{type:'geojson',data:url,tolerance:0,buffer:64,maxzoom:16,attribution:MAP2D_ATTRIBUTION});
          const layers=map2DSourceLayers(source,asset);for(const {layer,before} of layers)map.addLayer(layer,before);
          resources.set(key,{asset,source,layerIds:layers.map(row=>row.layer.id),url,record,features:loaded.featureCount,vertices:loaded.vertexCount});installed=true;
        }catch(error){if(!disposed&&!controller.signal.aborted&&!(error instanceof DOMException&&error.name==='AbortError')){errors++;report();}}
        finally{
          if(!installed){if(source&&!disposed){for(const {layer} of map2DSourceLayers(source,asset))if(map.getLayer(layer.id))map.removeLayer(layer.id);if(map.getSource(source))map.removeSource(source);}if(url)URL.revokeObjectURL(url);if(loaded&&!disposed)worker.postMessage({type:'release',id:record} satisfies Map2DWorkerRequest);}
          if(jobs.get(key)===job)jobs.delete(key);if(!disposed){prune();report();}
        }
      })();
    };
    const apply=(assets:Asset[])=>{
      if(disposed||!ready)return;
      const bounds=map.getBounds(),center=map.getCenter(),height=map2DHeight(center.lat,map.getZoom(),node.clientHeight);
      const bbox:BBox=[bounds.getWest(),bounds.getSouth(),bounds.getEast(),bounds.getNorth()];
      const selected=selectMap2DAssets(latest.current.vectorData||latest.current.vectorPending?assets.filter(a=>a.source_id==='natural-earth'):assets,{bbox,height,layers:latest.current.layers,quality:latest.current.lightweight?'low':'balanced'});
      wanted=new Set(selected.assets.map(map2DKey));deferred=selected.deferred;
      for(const [key,job] of jobs)if(!wanted.has(key))job.controller.abort();
      for(const [key,resource] of resources)if(resource.asset.layer!=='terrain'&&!latest.current.layers[resource.asset.layer])remove(key);
      for(const asset of selected.assets)load(asset);prune();report();
    };
    const loadSeoulKaptPoints=()=>{
      if(disposed||!ready)return;
      const bounds=map.getBounds();
      const visible=map.getZoom()>=10&&bounds.getEast()>=126.6&&bounds.getWest()<=127.4&&bounds.getNorth()>=37.3&&bounds.getSouth()<=37.8;
      node.dataset.seoulKaptVisible=String(visible);
      if(!visible||map.getSource(SEOUL_KAPT_SOURCE))return;
      map.addSource(SEOUL_KAPT_SOURCE,{type:'geojson',data:SEOUL_KAPT_URL,cluster:true,clusterRadius:42,clusterMaxZoom:13,attribution:'서울특별시 열린데이터광장 · 공공누리 제1유형'});
      map.addLayer({id:'seoul-kapt-clusters',type:'circle',source:SEOUL_KAPT_SOURCE,minzoom:10,filter:['has','point_count'],paint:{'circle-color':'#244fc0','circle-opacity':.88,'circle-radius':['step',['get','point_count'],15,20,19,100,24],'circle-stroke-color':'#fff','circle-stroke-width':2}});
      map.addLayer({id:'seoul-kapt-cluster-count',type:'symbol',source:SEOUL_KAPT_SOURCE,minzoom:10,filter:['has','point_count'],layout:{'text-field':['concat',['get','point_count_abbreviated'],'개'],'text-size':10,'text-font':['Malgun Gothic','sans-serif']},paint:{'text-color':'#fff'}});
      map.addLayer({id:'seoul-kapt-points',type:'circle',source:SEOUL_KAPT_SOURCE,minzoom:10,filter:['!',['has','point_count']],paint:{'circle-color':['case',['has','property_complex_id'],'#16805f','#2859c4'],'circle-radius':['interpolate',['linear'],['zoom'],10,4,15,6],'circle-stroke-color':'#fff','circle-stroke-width':2,'circle-opacity':.93}});
      map.addLayer({id:'seoul-kapt-linked-names',type:'symbol',source:SEOUL_KAPT_SOURCE,minzoom:13,filter:['all',['!',['has','point_count']],['has','property_complex_id']],layout:{'text-field':['get','name'],'text-font':['Malgun Gothic','sans-serif'],'text-size':11,'text-offset':[0,1.35],'text-anchor':'top','text-max-width':12,'text-optional':true},paint:{'text-color':'#174c38','text-halo-color':'#fff','text-halo-width':1.5}});
      map.addSource(SEOUL_KAPT_SELECTED,{type:'geojson',data:{type:'FeatureCollection',features:[]}});
      map.addLayer({id:'seoul-kapt-selected-halo',type:'circle',source:SEOUL_KAPT_SELECTED,minzoom:10,paint:{'circle-color':'#ffb12b','circle-radius':15,'circle-opacity':.28,'circle-stroke-color':'#fff','circle-stroke-width':2}});
      map.addLayer({id:'seoul-kapt-selected-core',type:'circle',source:SEOUL_KAPT_SELECTED,minzoom:10,paint:{'circle-color':'#f28d20','circle-radius':6,'circle-stroke-color':'#fff','circle-stroke-width':2}});
      node.dataset.seoulKaptLayer='provider-points-crs-unconfirmed';
    };
    const installVectors=()=>{
      const input=latest.current.vectorData;if(!ready||!input||vectorRelease===input.map.release_id)return;
      for(const id of vectorLayerIds)if(map.getLayer(id))map.removeLayer(id);for(const id of vectorSourceIds)if(map.getSource(id))map.removeSource(id);vectorLayerIds.length=0;vectorSourceIds.length=0;
      vectorProtocol?.dispose();vectorProtocol=createMapTilesProtocol({catalog:input.map,origin:input.origin,allowedOrigins:[input.origin],mobile:matchMedia('(max-width:780px)').matches,fetcher:atlasFetch});vectorRelease=input.map.release_id;addProtocol('krtile',vectorProtocol.protocol);
      const order=['land','water','buildings','facilities','roads','rail','admin-dong','admin-sigungu','admin-sido'];
      const labels:LayerSpecification[]=[];
      for(const topic of [...input.map.topics].sort((a,b)=>order.indexOf(a.id)-order.indexOf(b.id))){
        const source=`vector-${topic.id}`;map.addSource(source,{type:'vector',tiles:[vectorProtocol.tileUrl(topic.id)],bounds:topic.bounds,minzoom:topic.minzoom,maxzoom:topic.maxzoom,promoteId:'stable_id',attribution:input.map.attribution});vectorSourceIds.push(source);
        for(const layer of vectorLayers(topic,source)){if(layer.type==='symbol')labels.push(layer);else{map.addLayer(layer,'measure-fill');vectorLayerIds.push(layer.id);}}
      }
      for(const layer of labels){map.addLayer(layer,'measure-fill');vectorLayerIds.push(layer.id);}
      if(map.getLayer('live-bus-points'))map.moveLayer('live-bus-points');
    };
    const refresh=()=>{
      if(disposed||!ready||document.hidden)return;
      installVectors();
      for(const id of vectorLayerIds){const layer=id.includes('buildings')?'buildings':id.includes('rail')?'rail':id.includes('roads')||id.includes('facilities')?'infrastructure':null;map.setLayoutProperty(id,'visibility',(layer&&!latest.current.layers[layer])||(id.startsWith('vector-admin-')&&latest.current.boundaries===false)?'none':'visible');}
      if(latest.current.vectorData||latest.current.vectorPending){
        release=latest.current.catalog.release_id;indexController?.abort();indexLoading=false;
        if(latest.current.vectorData?.map.topics.some(topic=>topic.id==='land')){apply([]);return;}
        const controller=new AbortController();indexController=controller;indexLoading=true;
        const core={...latest.current.catalog,assets:latest.current.catalog.assets.filter(a=>a.source_id==='natural-earth')};
        void resolveSceneAssets(core,[124.5,33,132.5,38.7],500000,controller.signal,downloads.fetcher).then(assets=>{if(disposed||controller.signal.aborted)return;indexLoading=false;apply(assets);}).catch(()=>{if(!disposed&&!controller.signal.aborted){indexLoading=false;errors++;report();}});return;
      }
      const catalog=latest.current.catalog,nextRelease=catalog.release_id;indexController?.abort();const currentRevision=++revision;
      if(release!==nextRelease){release=nextRelease;resolved=[];wanted.clear();errors=0;pickController?.abort();for(const job of jobs.values())job.controller.abort();for(const key of resources.keys())remove(key);}
      const controller=new AbortController();indexController=controller;indexLoading=true;
      const bounds=map.getBounds(),center=map.getCenter(),bbox:BBox=[bounds.getWest(),bounds.getSouth(),bounds.getEast(),bounds.getNorth()],height=map2DHeight(center.lat,map.getZoom(),node.clientHeight);
      const active=()=>!disposed&&!controller.signal.aborted&&revision===currentRevision&&release===nextRelease;
      const publish=(assets:Asset[],complete=false)=>{if(!active())return;resolved=complete?assets:[...new Map([...resolved,...assets].map(asset=>[asset.id,asset])).values()];apply(resolved);};
      // Reconcile current jobs against the latest viewport before waking paused decoders.
      apply(resolved);
      void resolveSceneAssets(catalog,bbox,height,controller.signal,downloads.fetcher,assets=>publish(assets),map2DLayers(latest.current.layers)).then(assets=>{if(!active())return;indexLoading=false;publish(assets,true);prune();}).catch(()=>{if(!active())return;indexLoading=false;controller.abort();errors++;prune();report();});
      report();
    };
    const settle=()=>{if(disposed||resolution.applying)return;clearTimeout(settleTimer);settleTimer=setTimeout(()=>{settleTimer=undefined;if(disposed||document.hidden||map.isMoving()||resolution.inputHeld)return;resolution.restore();moving=false;refresh();loadSeoulKaptPoints();for(const waiter of [...waiters])waiter.resume();lastMovingFrame=null;report();},250);};
    const start=(event:{originalEvent?:unknown})=>{if(disposed||resolution.applying)return;moving=true;clearTimeout(settleTimer);resolution.moveStart(event.originalEvent);indexController?.abort();indexLoading=false;lastMovingFrame=null;report();};
    const visibility=()=>{if(document.hidden){resolution.releasePointers();clearTimeout(settleTimer);indexController?.abort();indexLoading=false;lastMovingFrame=null;}else settle();report();};
    // Capture before MapLibre's mouse/touch/wheel handlers. Resizing an active
    // drag can stop it; pointer holds also postpone the high-resolution restore.
    const pointerDown=(event:PointerEvent)=>{resolution.pointerDown(event.pointerId);clearTimeout(settleTimer);report();};
    const pointerUp=(event:PointerEvent)=>{if(resolution.pointerUp(event.pointerId))settle();};
    const wheel=()=>{resolution.beforeWheel();settle();};
    const blur=()=>{resolution.releasePointers();settle();};
    // The delayed fetch-settle flag remains true after camera motion ends. Do not
    // count idle tile-arrival gaps as animation frames in the moving-frame metric.
    const onRender=()=>{frames++;node.dataset.map2dFrames=String(frames);if(map.isMoving()&&!document.hidden){const now=performance.now();if(lastMovingFrame!==null){frameSamples.push(now-lastMovingFrame);if(frameSamples.length>600)frameSamples.shift();}lastMovingFrame=now;}else lastMovingFrame=null;};
    const onIdle=()=>{
      if(disposed||!ready||document.hidden||latest.current.vectorPending&&!vectorProtocol||!map.areTilesLoaded())return;
      const loaded=vectorProtocol?vectorProtocol.snapshot().cachedArchives>0:resources.size>0&&!indexLoading&&!jobs.size;
      if(!loaded)return;
      const current=vectorRelease||release,now=performance.now().toFixed(2);node.dataset.map2dLastIdleMs=now;
      if(firstReadyRelease!==current){firstReadyRelease=current;node.dataset.map2dFirstReadyMs=now;}
    };
    const click=(event:MapMouseEvent)=>{
      const measurement=latest.current.measurement??EMPTY_MEASUREMENT;
      if(measurement.mode!=='none'){if(measurement.points.length<512)latest.current.onMeasurement?.(measureMap(measurement.mode,[...measurement.points,[event.lngLat.lng,event.lngLat.lat]]));return;}
      const hits=map.queryRenderedFeatures(event.point);
      const liveHit=hits.find(feature=>typeof feature.properties?.live_bus_id==='string');
      if(liveHit){const selection=map2DLiveBusSelection(latest.current.liveTransit,liveHit.properties.live_bus_id);if(selection)latest.current.onSelect(selection);return;}
      const kaptHit=hits.find(feature=>feature.source===SEOUL_KAPT_SOURCE);
      if(kaptHit){
        if(kaptHit.properties?.cluster){map.easeTo({center:event.lngLat,zoom:Math.min(15,map.getZoom()+2),duration:350});return;}
        const code=kaptHit.properties?.kapt_code,name=kaptHit.properties?.name;
        if(typeof code==='string'&&typeof name==='string'){
          const coordinates=kaptHit.geometry.type==='Point'?kaptHit.geometry.coordinates:[event.lngLat.lng,event.lngLat.lat];
          (map.getSource(SEOUL_KAPT_SELECTED) as GeoJSONSource|undefined)?.setData({type:'FeatureCollection',features:[{type:'Feature',geometry:{type:'Point',coordinates},properties:{kapt_code:code}}]});
          const identity=kaptHit.properties?.property_complex_id,match=/^molit-apt:(11\d{3}):[A-Za-z0-9_-]{1,64}$/.exec(typeof identity==='string'?identity:'');
          if(match&&kaptHit.properties?.property_release_id===latest.current.vectorData?.property?.release_id&&latest.current.onPropertyComplex){
            latest.current.onPropertyComplex(match[1],identity);
            if(matchMedia('(max-width:780px)').matches){
              // Keep the selected point above the mobile bottom sheet.
              map.easeTo({center:[coordinates[0],coordinates[1]],offset:[0,-Math.min(150,Math.round(node.clientHeight*.2))],duration:300});
            }
            return;
          }
          latest.current.onSelect({name,sourceId:code,
          detail:'서울시 공동주택 아파트 정보의 K-apt ID와 제공 좌표입니다. 좌표계·점의 의미는 원천 명세에서 확인되지 않았습니다. 이 점은 현재 자료 버전에서 국토부 실거래 단지와 안전하게 연결되지 않았습니다.',
          provenance:{source_id:'seoul-openaptinfo',source_record_id:code,dataset_version:'2026-09-23',retrieved_at:'2026-09-23T04:36:36Z',evidence_type:'official_record'}});
        }
        return;
      }
      const regionCode=pickedPropertyRegion(hits,regionDataRef.current);
      if(regionCode&&latest.current.onPropertyRegion){pickController?.abort();latest.current.onPropertyRegion(regionCode);return;}
      const provinceCenter=pickedPropertyProvince(hits,regionDataRef.current);
      if(provinceCenter){map.easeTo({center:provinceCenter,zoom:7,duration:450});return;}
      const vectorHit=vectorProtocol&&hits.find(feature=>typeof feature.properties?.stable_id==='string'&&feature.source.startsWith('vector-'));
      if(vectorHit&&vectorProtocol){pickController?.abort();const controller=new AbortController();pickController=controller;void vectorProtocol.pick(vectorHit.source.slice(7),vectorHit.properties.stable_id,controller.signal).then(value=>{if(value&&!disposed&&!controller.signal.aborted)latest.current.onSelect(value);}).catch(()=>undefined);return;}
      const hit=hits.find(feature=>Number.isSafeInteger(feature.properties?.map2d_index));if(!hit)return;
      const resource=[...resources.values()].find(row=>row.source===hit.source);if(!resource)return;
      pickController?.abort();const controller=new AbortController();pickController=controller;
      void post({type:'pick',id:++sequence,record:resource.record,index:Number(hit.properties.map2d_index)},controller.signal).then(result=>{if(result.type==='picked'&&result.selection&&!disposed&&!controller.signal.aborted&&resources.get(map2DKey(resource.asset))===resource)latest.current.onSelect(result.selection);}).catch(()=>undefined);
    };
    const failure=()=>{errors++;report();};
    const refreshRegions=()=>{
      if(disposed||!ready)return;
      const current=regionDataRef.current,source=map.getSource(REGION_MAP_SOURCE) as GeoJSONSource|undefined;
      if(source&&installedRegions!==current){source.setData({type:'FeatureCollection',features:[...current.data.features,...current.provinces.features]});installedRegions=current;}
      node.dataset.propertyRegionCount=String(current.data.features.length);node.dataset.propertyRegionExcluded=String(current.excluded);node.dataset.propertyRegionMonth=current.month;
      if(map.getLayer(REGION_MAP_LAYER))map.setLayoutProperty(REGION_MAP_LAYER,'visibility',(latest.current.measurement?.mode??'none')==='none'?'visible':'none');
      if(map.getLayer(PROVINCE_MAP_LAYER))map.setLayoutProperty(PROVINCE_MAP_LAYER,'visibility',(latest.current.measurement?.mode??'none')==='none'?'visible':'none');
    };
    refreshRegionsRef.current=refreshRegions;
    const refreshLive=()=>{if(disposed||!ready||document.hidden)return;const data=map2DLiveBuses(latest.current.liveTransit);if(node.dataset.liveBusCount===String(data.features.length))return;(map.getSource('live-buses') as GeoJSONSource|undefined)?.setData(data);node.dataset.liveBusCount=String(data.features.length);};
    map.on('load',()=>{
      const data=map2DLiveBuses(latest.current.liveTransit);node.dataset.liveBusCount=String(data.features.length);
      map.addSource('live-buses',{type:'geojson',data});
      map.addLayer({id:'live-bus-points',type:'circle',source:'live-buses',paint:{'circle-color':'#12846e','circle-radius':6,'circle-stroke-width':2,'circle-stroke-color':'#ffffff'}});
      liveTimer=setInterval(refreshLive,5000);
    });
    map.on('load',()=>{ready=true;map.addSource('measure',{type:'geojson',data:measurementGeoJSON(latest.current.measurement??EMPTY_MEASUREMENT)});map.addLayer({id:'measure-fill',type:'fill',source:'measure',filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':'#317a63','fill-opacity':.15}});map.addLayer({id:'measure-line',type:'line',source:'measure',filter:['==',['geometry-type'],'LineString'],paint:{'line-color':'#246b55','line-width':3}});map.addLayer({id:'measure-point',type:'circle',source:'measure',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':5,'circle-color':'#246b55','circle-stroke-color':'#ffffff','circle-stroke-width':2}});refresh();loadSeoulKaptPoints();});map.on('movestart',start);map.on('moveend',settle);map.on('resize',settle);map.on('render',onRender);map.on('click',click);map.on('error',failure);map.on('sourcedata',report);
    map.on('load',()=>{
      map.addImage(REGION_MAP_IMAGE,regionMapBubbleImage(),{pixelRatio:2,stretchX:[[12,68]],stretchY:[[12,52]],content:[12,10,68,54]});
      installedRegions=regionDataRef.current;map.addSource(REGION_MAP_SOURCE,{type:'geojson',data:{type:'FeatureCollection',features:[...installedRegions.data.features,...installedRegions.provinces.features]}});map.addLayer(provinceMapLayer());map.addLayer(regionMapLayer());refreshRegions();
      if(map.getLayer('live-bus-points'))map.moveLayer('live-bus-points');
    });
    map.on('mouseenter',REGION_MAP_LAYER,()=>{if((latest.current.measurement?.mode??'none')==='none')map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',REGION_MAP_LAYER,()=>{map.getCanvas().style.cursor=(latest.current.measurement?.mode??'none')==='none'?'':'crosshair';});
    map.on('mouseenter',PROVINCE_MAP_LAYER,()=>{if((latest.current.measurement?.mode??'none')==='none')map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',PROVINCE_MAP_LAYER,()=>{map.getCanvas().style.cursor=(latest.current.measurement?.mode??'none')==='none'?'':'crosshair';});
    const canvas=map.getCanvas();canvas.addEventListener('pointerdown',pointerDown,{capture:true,passive:true});canvas.addEventListener('wheel',wheel,{capture:true,passive:true});
    window.addEventListener('pointerup',pointerUp,true);window.addEventListener('pointercancel',pointerUp,true);window.addEventListener('blur',blur);
    map.on('idle',onIdle);document.addEventListener('visibilitychange',visibility);refreshRef.current=()=>{if(!moving)refresh();};report();
    return()=>{
      disposed=true;refreshRef.current=null;refreshRegionsRef.current=null;mapRef.current=null;clearTimeout(settleTimer);clearInterval(liveTimer);if(commitFrame!==undefined)cancelAnimationFrame(commitFrame);
      canvas.removeEventListener('pointerdown',pointerDown,true);canvas.removeEventListener('wheel',wheel,true);window.removeEventListener('pointerup',pointerUp,true);window.removeEventListener('pointercancel',pointerUp,true);window.removeEventListener('blur',blur);resolution.dispose();
      indexController?.abort();pickController?.abort();for(const job of jobs.values())job.controller.abort();for(const waiter of [...waiters])waiter.reject();downloads.dispose();
      for(const task of pending.values()){task.detach();task.reject(abortError());}pending.clear();worker.terminate();document.removeEventListener('visibilitychange',visibility);
      map.remove();vectorProtocol?.dispose();if(vectorProtocol)removeProtocol('krtile');for(const row of resources.values())URL.revokeObjectURL(row.url);resources.clear();
    };
  },[]);
  useEffect(()=>{refreshRef.current?.();},[props.catalog,props.layers,props.boundaries,props.lightweight,props.vectorData,props.vectorPending]);
  useEffect(()=>{refreshRegionsRef.current?.();},[regions,props.measurement]);
  useEffect(()=>{const source=mapRef.current?.getSource('live-buses') as GeoJSONSource|undefined;const data=map2DLiveBuses(props.liveTransit);source?.setData(data);if(element.current)element.current.dataset.liveBusCount=String(data.features.length);},[props.liveTransit]);
  useEffect(()=>{const map=mapRef.current;if(!map)return;const source=map.getSource('measure') as GeoJSONSource|undefined;source?.setData(measurementGeoJSON(props.measurement??EMPTY_MEASUREMENT));map.getCanvas().style.cursor=props.measurement&&props.measurement.mode!=='none'?'crosshair':'';},[props.measurement]);
  return <><div ref={element} className="map-scene map-scene-2d" role="region" aria-label="대한민국 2D 지도"/><div className="seoul-kapt-note" role="note">서울시 K-apt 제공 점 · 초록색은 실거래 ID 연결 · 좌표계 검토 중</div>{regions.data.features.length>0&&<div className="region-map-caption" role="note" tabIndex={0} title={regions.notice} aria-label={`${regions.caption}. ${regions.notice}`}><strong>{regions.caption}</strong><span>{regions.data.features.length}개 지역 표시 · 위치·자료 미연결 {regions.excluded}개 제외</span></div>}</>;
});
export default Map2D;
