import * as C from 'cesium';
import {liveTransitVehicles,type LiveTransitSnapshot} from '../shared/live-transit';
import type {MapSelection} from '../shared/selection';
import {FrameWorkBudget} from '../shared/map-performance';

interface Options {
  budget:FrameWorkBudget;groundState:()=>{paused:boolean;revision:number};
  now?:()=>number;onCount?:(count:number)=>void;
}
interface GroundPoint {point:C.PointPrimitive;position:C.Cartographic;revision?:number;}
/** Transient bus reports. No Entity, position interpolation, or source-height mutation. */
export function createLiveBusLayer(viewer:C.Viewer,snapshot:LiveTransitSnapshot,options:Options){
  const root=new C.PointPrimitiveCollection(),picks=new Map<string,MapSelection>(),points:GroundPoint[]=[];
  const now=options.now??Date.now,controller=new AbortController();
  let destroyed=false,paused=options.groundState().paused,refreshController:AbortController|null=null,requestedRevision=options.groundState().revision;
  let expiry:ReturnType<typeof setTimeout>|undefined;
  const expired=()=>now()>=Date.parse(snapshot.expires_at??'')||!Number.isFinite(Date.parse(snapshot.expires_at??''));
  const clear=()=>{controller.abort();refreshController?.abort();picks.clear();points.length=0;if(!root.isDestroyed())root.removeAll();options.onCount?.(0);if(!viewer.isDestroyed())viewer.scene.requestRender();};
  const refreshGround=(revision:number)=>{
    requestedRevision=revision;
    if(destroyed||paused||options.groundState().paused||refreshController||expired())return;
    const current=new AbortController();refreshController=current;
    void(async()=>{
      await options.budget.defer(current.signal);const finish=options.budget.startTask();
      try{for(const item of points){
        if(current.signal.aborted||destroyed||expired()||paused||options.groundState().paused||viewer.isDestroyed())return;
        if(item.revision===revision)continue;
        await options.budget.run('live-terrain-height',()=>{
          if(destroyed||expired()||paused||options.groundState().paused||viewer.isDestroyed())return;
          const height=viewer.scene.globe.getHeight(item.position);
          if(typeof height==='number'&&Number.isFinite(height)){
            item.revision=revision;item.position.height=height+2;
            item.point.position=C.Cartographic.toCartesian(item.position);
          }
        },current.signal);
      }if(!viewer.isDestroyed())viewer.scene.requestRender();}finally{finish();}
    })().catch(()=>{}).finally(()=>{
      if(refreshController===current)refreshController=null;
      if(!destroyed&&!paused&&(current.signal.aborted||requestedRevision!==revision))refreshGround(requestedRevision);
    });
  };
  const ready=(async()=>{
    await options.budget.defer(controller.signal);const finish=options.budget.startTask();
    try{
      for(const vehicle of liveTransitVehicles(snapshot,now())){
        if(vehicle.kind!=='bus'||destroyed||expired())continue;
        await options.budget.run('live-point-instance',()=>{
          if(destroyed||expired())return;
          const id=`live-bus:${vehicle.id}`,position=C.Cartographic.fromDegrees(vehicle.position.lon,vehicle.position.lat,0);
          const point=root.add({id,position:C.Cartographic.toCartesian(position),pixelSize:9,color:C.Color.fromCssColorString('#156c76'),outlineColor:C.Color.WHITE,outlineWidth:2,disableDepthTestDistance:Number.POSITIVE_INFINITY});
          points.push({point,position});
          picks.set(id,{name:vehicle.label,detail:`${vehicle.station_name??'정류장 이름 미제공'} · 제공기관의 도로 보정 위치입니다. 원천 측정 시각과 고도는 제공되지 않습니다.`,rawHeight:null,properties:{live:true,expires_at:snapshot.expires_at},
            provenance:{source_id:'tago',source_record_id:vehicle.id,dataset_version:`현재 조회 ${snapshot.retrieved_at}`,observed_at:null,retrieved_at:vehicle.retrieved_at,evidence_type:'official_record'}});
        },controller.signal);
      }
      options.onCount?.(root.length);if(!viewer.isDestroyed())viewer.scene.requestRender();
    }finally{finish();}
    if(!expired())refreshGround(options.groundState().revision);else clear();
  })().catch(error=>{if(!controller.signal.aborted)clear();if(error instanceof Error&&error.name!=='AbortError')throw error;});
  const ttl=Date.parse(snapshot.expires_at??'')-now();
  if(Number.isFinite(ttl)&&ttl>0)expiry=setTimeout(clear,Math.min(ttl,2_147_483_647));else clear();
  return {root,picks,ready,refreshGround,setGroundRefreshPaused(value:boolean){paused=value;if(value)refreshController?.abort();},
    destroy(){if(destroyed)return;destroyed=true;clearTimeout(expiry);clear();if(!root.isDestroyed())root.destroy();}};
}
export type LiveBusLayer=ReturnType<typeof createLiveBusLayer>;
