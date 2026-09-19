import {isLiveWeatherFresh,type LiveWeatherManifest,type PanelLiveFrame} from '../shared/live-weather';
import {validTransitQuota,type LiveTransitSnapshot,type LiveTransitTargets} from '../shared/live-transit';
import {validTransitRef} from '../shared/transit-catalog';

export const LIVE_SHARE_NOTICE='현재 관측은 보관되지 않아 동일한 시점의 화면을 공유할 수 없습니다. 기록 재생이나 햇빛 실험에서 공유해 주세요.';
export interface LiveResource<T> {data:T|null;loading:boolean;error:string;paused:boolean;expired:boolean;}
export interface VisibilitySource {readonly hidden:boolean;addEventListener(type:'visibilitychange',listener:()=>void):void;removeEventListener(type:'visibilitychange',listener:()=>void):void;}
interface PollOptions<T> {
  url:string;decode:(value:unknown)=>T;refreshSeconds:(value:T)=>number;expiresAt?:(value:T)=>number|null;
  onChange:(state:LiveResource<T>)=>void;fetcher?:typeof fetch;visibility?:VisibilitySource;now?:()=>number;timeoutMs?:number;
}
export const emptyLiveResource=<T,>():LiveResource<T>=>({data:null,loading:false,error:'',paused:false,expired:false});

/** One active request; a stale response cannot repopulate a replaced/hidden view. */
export function startLivePoll<T>(options:PollOptions<T>){
  if(!/^\/api\/v1\/live\/(?:weather|transit)\//.test(options.url))throw new Error('현재 자료의 조회 경로를 확인할 수 없습니다.');
  const fetcher=options.fetcher??fetch,visibility=options.visibility??document,now=options.now??Date.now;
  let state=emptyLiveResource<T>(),stopped=false,active:AbortController|null=null;
  let refreshTimer:ReturnType<typeof setTimeout>|undefined,expiryTimer:ReturnType<typeof setTimeout>|undefined;
  const emit=()=>{if(!stopped)options.onChange({...state});};
  const expire=()=>{
    clearTimeout(expiryTimer);
    const deadline=state.data?options.expiresAt?.(state.data):null;
    state.expired=deadline!==null&&deadline!==undefined&&(!Number.isFinite(deadline)||now()>=deadline);
    if(deadline!==null&&deadline!==undefined&&!state.expired){
      expiryTimer=setTimeout(()=>{expire();emit();},Math.min(2_147_483_647,Math.max(1,deadline-now())));
    }
  };
  const schedule=(seconds:number)=>{
    clearTimeout(refreshTimer);
    if(!stopped&&!visibility.hidden)refreshTimer=setTimeout(()=>void refresh(),Math.max(15,Math.min(3600,Number.isFinite(seconds)?seconds:60))*1000);
  };
  const refresh=async()=>{
    if(stopped||visibility.hidden||active)return;
    clearTimeout(refreshTimer);
    const controller=new AbortController();active=controller;let timedOut=false;
    const timeout=setTimeout(()=>{timedOut=true;controller.abort();},options.timeoutMs??15000);
    state={...state,loading:true,error:'',paused:false};expire();emit();
    let next=60;
    try{
      const response=await fetcher(options.url,{signal:controller.signal,cache:'no-store',redirect:'error',credentials:'same-origin'});
      const data=options.decode(await response.json());
      if(stopped||controller.signal.aborted||active!==controller)return;
      state={data,loading:false,error:'',paused:false,expired:false};expire();emit();
      next=options.refreshSeconds(data);
    }catch{
      if(stopped||active!==controller||controller.signal.aborted&&!timedOut)return;
      state={data:null,loading:false,error:timedOut?'현재 자료의 응답이 늦어 조회를 멈췄습니다.':'현재 자료를 불러오지 못했습니다. 다음 조회에서 다시 확인합니다.',paused:false,expired:false};
      clearTimeout(expiryTimer);emit();
    }finally{
      clearTimeout(timeout);
      if(active===controller){active=null;schedule(next);}
    }
  };
  const onVisibility=()=>{
    if(visibility.hidden){clearTimeout(refreshTimer);const previous=active;active=null;previous?.abort();state={...state,loading:false,paused:true};expire();emit();}
    else{state={...state,paused:false};expire();emit();void refresh();}
  };
  visibility.addEventListener('visibilitychange',onVisibility);
  if(visibility.hidden)onVisibility();else void refresh();
  return {refresh:()=>void refresh(),stop:()=>{stopped=true;clearTimeout(refreshTimer);clearTimeout(expiryTimer);active?.abort();active=null;visibility.removeEventListener('visibilitychange',onVisibility);}};
}

type JsonObject=Record<string,unknown>;
function object(value:unknown):value is JsonObject{return value!==null&&typeof value==='object'&&!Array.isArray(value);}
function text(value:unknown):value is string{return typeof value==='string'&&value.length<=500;}
function nullableTime(value:unknown):boolean{return value===null||typeof value==='string'&&Number.isFinite(Date.parse(value));}
function finitePositive(value:unknown):value is number{return typeof value==='number'&&Number.isFinite(value)&&value>0;}
function invalid():never{throw new Error('현재 자료 응답 형식이 올바르지 않습니다.');}
function base(value:unknown):value is JsonObject{return object(value)&&value.schema_version===1&&value.mode==='live';}
function failure(value:JsonObject){if(value.error!==undefined&&(!object(value.error)||!text(value.error.message)))invalid();}
export function decodeLiveTargets(value:unknown):LiveTransitTargets{
  if(!base(value)||!Array.isArray(value.bus_routes)||!Array.isArray(value.subway_lines)||value.bus_routes.length>100||value.subway_lines.length>100||value.continuous_collection!==false)invalid();
  if(typeof value.global_quota_enforced!=='boolean'||value.global_quota_enforced&&value.global_quota_scope!=='broker-mediated-requests'
    ||!value.global_quota_enforced&&value.global_quota_scope!==undefined)invalid();
  for(const route of value.bus_routes)if(!object(route)||!text(route.id)||!text(route.label)||!text(route.city_code)||!text(route.route_id)||typeof route.configured!=='boolean'||!finitePositive(route.refresh_after_seconds))invalid();
  for(const line of value.subway_lines)if(!object(line)||!text(line.id)||!text(line.name)||!text(line.subway_id)||typeof line.configured!=='boolean'||!finitePositive(line.refresh_after_seconds))invalid();
  if(value.bus_catalog!==undefined&&(!object(value.bus_catalog)||typeof value.bus_catalog.configured!=='boolean'||!validTransitRef(value.bus_catalog)))invalid();
  return value as unknown as LiveTransitTargets;
}
export function decodeLiveTransit(value:unknown):LiveTransitSnapshot{
  if(!base(value)||!['bus','subway'].includes(String(value.kind))||!['available','partial','empty','stale','unavailable'].includes(String(value.status))||!object(value.target)||!text(value.target.id)||!text(value.target.label)
    ||!nullableTime(value.retrieved_at)||!nullableTime(value.expires_at)||!nullableTime(value.served_at)||!finitePositive(value.refresh_after_seconds)
    ||!object(value.source)||!text(value.source.page_url)||!text(value.source.license)||!Array.isArray(value.vehicles)||value.vehicles.length>1000)invalid();
  failure(value);
  if(!validTransitQuota(value.quota))invalid();
  for(const vehicle of value.vehicles){
    if(!object(vehicle)||vehicle.kind!==value.kind||!text(vehicle.id)||!text(vehicle.label)||!nullableTime(vehicle.retrieved_at)||vehicle.observed_at!==null)invalid();
    if(vehicle.kind==='bus'){
      const p=vehicle.position;
      if(!object(p)||typeof p.lon!=='number'||typeof p.lat!=='number'||!Number.isFinite(p.lon)||!Number.isFinite(p.lat)||p.lon<124||p.lon>132.5||p.lat<32||p.lat>39.5||p.crs!=='EPSG:4326'||p.method!=='provider-map-matched')invalid();
      if(vehicle.station_name!==null&&!text(vehicle.station_name))invalid();
    }else if(vehicle.position!==null||vehicle.station_mapping!=='unresolved'||!text(vehicle.station_name)||!text(vehicle.train_no)||!nullableTime(vehicle.source_received_at)
      ||!['up','down'].includes(String(vehicle.direction))||!['approaching','arrived','departed','left-previous'].includes(String(vehicle.reported_status)))invalid();
  }
  return value as unknown as LiveTransitSnapshot;
}
export function decodeLiveWeather(value:unknown):LiveWeatherManifest{
  if(!base(value)||!['radar','satellite'].includes(String(value.kind))||!['available','stale','unavailable'].includes(String(value.status))
    ||!nullableTime(value.checked_at)||!nullableTime(value.served_at)||!nullableTime(value.latest_observed_at)||!finitePositive(value.max_age_seconds)||!finitePositive(value.refresh_after_seconds)
    ||!Array.isArray(value.frames)||value.frames.length>30||!object(value.source)||!text(value.source.name)||!text(value.source.page_url))invalid();
  failure(value);
  if(value.panel_frames!==undefined){
    if(!Array.isArray(value.panel_frames)||value.panel_frames.length>30)invalid();
    for(const frame of value.panel_frames){
      if(!object(frame)||frame.kind!==value.kind||frame.representation!=='panel-image'||frame.map_overlay!==false||frame.projection!==null
        ||typeof frame.time!=='string'||!Number.isFinite(Date.parse(frame.time))||typeof frame.url!=='string'||typeof frame.source_time_kst!=='string'||!/^\d{12}$/.test(frame.source_time_kst))invalid();
      if(frame.kind==='radar'){
        if(frame.product!=='CMP_WRC'||!/^\/api\/v1\/live\/weather\/radar\/cmp-wrc\/frames\/\d{12}\.png$/.test(frame.url)
          ||!Array.isArray(frame.image_size)||frame.image_size[0]!==635||frame.image_size[1]!==620||frame.image_size.length!==2)invalid();
      }else if(frame.kind==='satellite'){
        if(frame.product!=='GK2A_IR105_KO'||frame.image_size!==null||typeof frame.source_time_utc!=='string'||!/^\d{12}$/.test(frame.source_time_utc)
          ||frame.url!==`/api/v1/live/weather/satellite/gk2a-ir105-ko/frames/${frame.source_time_utc}.png`)invalid();
      }else invalid();
    }
  }
  return value as unknown as LiveWeatherManifest;
}
export const transitExpiry=(value:LiveTransitSnapshot)=>value.expires_at?Date.parse(value.expires_at):null;
export const weatherExpiry=(value:LiveWeatherManifest)=>value.latest_observed_at?Date.parse(value.latest_observed_at)+value.max_age_seconds*1000:null;
export const snapshotRefresh=(value:LiveTransitSnapshot|LiveWeatherManifest)=>value.refresh_after_seconds;
export const targetsRefresh=()=>300;
export function currentPanelFrame(value:LiveWeatherManifest,now=Date.now()):PanelLiveFrame|null{
  if(!isLiveWeatherFresh(value,now)||value.presentation!=='panel-image'||now>=weatherExpiry(value)!)return null;
  return value.panel_frames?.find(frame=>frame.time===value.latest_observed_at)??null;
}
export function publicSourceUrl(value:string):string|undefined{
  try{const url=new URL(value);if(url.protocol==='https:'&&!url.username&&!url.password&&!url.search&&(url.hostname.endsWith('.go.kr')||url.hostname==='go.kr'))return url.href;}catch{/* Do not navigate to malformed source metadata. */}
  return undefined;
}
