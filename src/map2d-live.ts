import type {FeatureCollection,Point} from 'geojson';
import {liveTransitVehicles,type LiveBusVehicle,type LiveTransitSnapshot} from '../shared/live-transit';
import type {MapSelection} from '../shared/selection';

export interface Map2DLiveBusProperties {live_bus_id:string;label:string;}

function freshBuses(snapshot:LiveTransitSnapshot|null|undefined,now:number):LiveBusVehicle[]{
  if(!snapshot||snapshot.kind!=='bus')return [];
  return liveTransitVehicles(snapshot,now).filter((vehicle):vehicle is LiveBusVehicle=>vehicle.kind==='bus');
}

/** Transient source coordinates only: no height, interpolation or stored history. */
export function map2DLiveBuses(snapshot:LiveTransitSnapshot|null|undefined,now=Date.now()):FeatureCollection<Point,Map2DLiveBusProperties>{
  return {type:'FeatureCollection',features:freshBuses(snapshot,now).map(vehicle=>{
    const id=`live-bus:${vehicle.id}`;
    return {type:'Feature',id,properties:{live_bus_id:id,label:vehicle.label},
      geometry:{type:'Point',coordinates:[vehicle.position.lon,vehicle.position.lat]}};
  })};
}

/** Recheck freshness at click time; an expired rendered point cannot be selected. */
export function map2DLiveBusSelection(snapshot:LiveTransitSnapshot|null|undefined,id:string,now=Date.now()):MapSelection|null{
  const vehicle=freshBuses(snapshot,now).find(item=>`live-bus:${item.id}`===id);
  if(!vehicle||!snapshot)return null;
  // Match the 3D current-bus selection contract without importing the Cesium layer.
  return {name:vehicle.label,detail:`${vehicle.station_name??'정류장 이름 미제공'} · 제공기관의 도로 보정 위치입니다. 원천 측정 시각과 고도는 제공되지 않습니다.`,rawHeight:null,
    properties:{live:true,expires_at:snapshot.expires_at},
    provenance:{source_id:'tago',source_record_id:vehicle.id,dataset_version:`현재 조회 ${snapshot.retrieved_at}`,
      observed_at:null,retrieved_at:vehicle.retrieved_at,evidence_type:'official_record'}};
}
