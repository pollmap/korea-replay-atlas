/** Current source reports, independent of the immutable historical replay catalog. */
export const LIVE_TRANSIT_API_PREFIX = '/api/v1/live/transit';
export const LIVE_SUBWAY_MAX_AGE_SECONDS = 300;
export const LIVE_TRANSIT_FUTURE_TOLERANCE_SECONDS = 30;

export type LiveTransitKind = 'bus' | 'subway';
export type LiveTransitStatus = 'available' | 'partial' | 'empty' | 'stale' | 'unavailable';
export interface LiveBusRoute {id:string;city_code:string;route_id:string;label:string;}
export interface LiveSubwayLine {id:string;name:string;subway_id:string;}

interface LiveVehicleBase {
  id:string;
  label:string;
  /** No source in this adapter identifies the sensor measurement timestamp. */
  observed_at:null;
  retrieved_at:string;
}
export interface LiveBusVehicle extends LiveVehicleBase {
  kind:'bus';
  route_id:string;
  city_code:string;
  source_received_at:null;
  position:{lon:number;lat:number;crs:'EPSG:4326';method:'provider-map-matched'};
  station_id:string|null;
  station_name:string|null;
  station_order:number|null;
}
export interface LiveSubwayVehicle extends LiveVehicleBase {
  kind:'subway';
  line_id:string;
  train_no:string;
  direction:'up'|'down';
  /** Provider reception time; never presented as a GPS observation time. */
  source_received_at:string;
  station_id:string;
  station_name:string;
  terminal_name:string|null;
  reported_status:'approaching'|'arrived'|'departed'|'left-previous';
  position:null;
  station_mapping:'unresolved';
}
export type LiveTransitVehicle = LiveBusVehicle | LiveSubwayVehicle;
export type LiveTransitErrorCode = 'invalid_request'|'not_configured'|'invalid_configuration'|'upstream_auth'
  |'upstream_http'|'upstream_timeout'|'upstream_invalid'|'upstream_inconsistent'|'response_limit'
  |'quota_exceeded'|'busy'|'aborted'|'broker_unavailable';
export interface LiveTransitError {code:LiveTransitErrorCode;message:string;retryable:boolean;}
export type LiveTransitQuota={daily_limit:number;local_budget:number;guard:'isolate-and-regional-cache';global_enforced:false}
  |{daily_limit:number;local_budget:number;guard:'unavailable';global_enforced:false}
  |{daily_limit:number;local_budget:number;budget_limit:number;guard:'durable-object';global_enforced:true;scope:'broker-mediated-requests';window:'rolling-24h-conservative'};
export function validTransitQuota(value:unknown):value is LiveTransitQuota {
  if(!value||typeof value!=='object'||Array.isArray(value))return false;
  const q=value as Record<string,unknown>;
  if(!Number.isSafeInteger(q.daily_limit)||Number(q.daily_limit)<1||Number(q.daily_limit)>1_000_000
    ||!Number.isSafeInteger(q.local_budget)||Number(q.local_budget)<1||Number(q.local_budget)>Number(q.daily_limit))return false;
  return (q.guard==='isolate-and-regional-cache'||q.guard==='unavailable')&&q.global_enforced===false
    ||q.guard==='durable-object'&&q.global_enforced===true&&q.scope==='broker-mediated-requests'&&q.window==='rolling-24h-conservative'
      &&q.budget_limit===q.local_budget;
}
export interface LiveTransitSnapshot {
  schema_version:1;
  mode:'live';
  kind:LiveTransitKind;
  status:LiveTransitStatus;
  target:{id:string;label:string};
  /** Successful retrieval only. An error never advances this field. */
  retrieved_at:string|null;
  served_at:string;
  expires_at:string|null;
  refresh_after_seconds:number;
  max_source_age_seconds:number|null;
  source:{id:'tago'|'seoul-subway';page_url:string;license:string;access:'official-key'};
  coverage:{scope:'selected-route'|'selected-line';complete:boolean};
  counts:{upstream:number;accepted:number;invalid:number;stale:number;duplicate:number;ambiguous:number};
  vehicles:LiveTransitVehicle[];
  quota:LiveTransitQuota;
  error?:LiveTransitError;
}
export interface LiveTransitTargets {
  schema_version:1;
  mode:'live';
  bus_routes:(LiveBusRoute&{configured:boolean;refresh_after_seconds:number})[];
  subway_lines:(LiveSubwayLine&{configured:boolean;refresh_after_seconds:number})[];
  /** Queries fetch a snapshot; this adapter does not persist a continuous archive. */
  continuous_collection:false;
  global_quota_enforced:boolean;
  global_quota_scope?:'broker-mediated-requests';
  bus_catalog?:import('./transit-catalog').TransitCatalogRef&{configured:boolean};
}

export function liveTransitVehicles(snapshot:LiveTransitSnapshot,now=Date.now()):LiveTransitVehicle[] {
  const retrieved=Date.parse(snapshot.retrieved_at??''),expires=Date.parse(snapshot.expires_at??'');
  if(!['available','partial'].includes(snapshot.status)||!Number.isFinite(now)||!Number.isFinite(retrieved)
    ||!Number.isFinite(expires)||now>=expires||retrieved>now+LIVE_TRANSIT_FUTURE_TOLERANCE_SECONDS*1000)return [];
  return snapshot.vehicles.filter(vehicle=>{
    if(vehicle.kind==='bus')return true;
    const received=Date.parse(vehicle.source_received_at);
    return Number.isFinite(received)&&received<=now+LIVE_TRANSIT_FUTURE_TOLERANCE_SECONDS*1000
      &&now-received<=LIVE_SUBWAY_MAX_AGE_SECONDS*1000;
  });
}

export function isLiveTransitFresh(snapshot:LiveTransitSnapshot,now=Date.now()):boolean {
  if(snapshot.status==='empty'){
    const retrieved=Date.parse(snapshot.retrieved_at??''),expires=Date.parse(snapshot.expires_at??'');
    return Number.isFinite(now)&&Number.isFinite(retrieved)&&Number.isFinite(expires)&&now<expires
      &&retrieved<=now+LIVE_TRANSIT_FUTURE_TOLERANCE_SECONDS*1000;
  }
  return liveTransitVehicles(snapshot,now).length>0;
}
