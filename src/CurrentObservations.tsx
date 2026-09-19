import {useCallback,useDeferredValue,useEffect,useMemo,useRef,useState} from 'react';
import type {Place} from '../shared/contracts';
import {LIVE_TRANSIT_API_PREFIX,isLiveTransitFresh,liveTransitVehicles,type LiveTransitKind,type LiveTransitSnapshot} from '../shared/live-transit';
import {LIVE_WEATHER_API_PREFIX,type LiveWeatherKind,type PanelLiveFrame} from '../shared/live-weather';
import {currentPanelFrame,decodeLiveTargets,decodeLiveTransit,decodeLiveWeather,emptyLiveResource,LIVE_SHARE_NOTICE,publicSourceUrl,snapshotRefresh,startLivePoll,targetsRefresh,transitExpiry,weatherExpiry,type LiveResource} from './live-client';
import {createTransitCatalogReader,type TransitCatalogRef,type TransitRouteCatalog,type TransitRoute,type TransitCity} from '../shared/transit-catalog';

interface Props {onTransit:(value:LiveTransitSnapshot|null)=>void;onLocate:(place:Place)=>void;onClose:()=>void;}
const clock=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
function timeLabel(value:string|null|undefined){const date=Date.parse(value??'');return Number.isFinite(date)?clock.format(date)+' KST':'미제공';}
function ageLabel(value:string|null|undefined,now:number){const date=Date.parse(value??'');return Number.isFinite(date)?Math.max(0,Math.floor((now-date)/60000))+'분 전':'확인되지 않음';}

function useLiveResource<T>(url:string|null,decode:(value:unknown)=>T,refreshSeconds:(value:T)=>number,expiresAt?:(value:T)=>number|null){
  const [stored,setStored]=useState<{url:string|null;value:LiveResource<T>}>(()=>({url:null,value:emptyLiveResource<T>()}));
  const handle=useRef<ReturnType<typeof startLivePoll<T>>|null>(null);
  useEffect(()=>{
    if(!url)return;
    const client=startLivePoll({url,decode,refreshSeconds,expiresAt,onChange:value=>setStored({url,value})});handle.current=client;
    return()=>{client.stop();if(handle.current===client)handle.current=null;};
  },[url,decode,refreshSeconds,expiresAt]);
  const refresh=useCallback(()=>handle.current?.refresh(),[]);
  // Do not render the previous route/product for one commit after a tab changes.
  return {...(stored.url===url?stored.value:emptyLiveResource<T>()),refresh};
}
const NO_ROUTES:TransitRoute[]=[];
const NO_CITIES:TransitCity[]=[];
const CITY_OPTION_LIMIT=24;
export function busCityAvailability(city:TransitCity|undefined){
  if(!city)return 'unselected';
  if(!city.location_service_supported)return 'unsupported';
  if(city.status!=='complete')return 'failed';
  return city.route_count?'available':'empty';
}
export function filterBusCities(cities:readonly TransitCity[],query:string,selectedCode=''){
  const terms=query.normalize('NFKC').trim().toLocaleLowerCase('ko-KR').split(/\s+/).filter(Boolean);
  const matches=cities.filter(city=>terms.every(term=>city.city_name.normalize('NFKC').toLocaleLowerCase('ko-KR').includes(term)))
    .sort((a,b)=>a.city_name.localeCompare(b.city_name,'ko-KR'));
  const options=matches.slice(0,CITY_OPTION_LIMIT),selected=cities.find(city=>city.city_code===selectedCode);
  if(selected&&!options.some(city=>city.city_code===selected.city_code)){options.unshift(selected);options.splice(CITY_OPTION_LIMIT);}
  return {options,matchCount:matches.length};
}
export function nationalBusRouteOptions(city:TransitCity|undefined,routes:readonly TransitRoute[],configured:boolean){
  if(busCityAvailability(city)!=='available'||!city)return [];
  return routes.filter(route=>route.city_code===city.city_code&&route.id===`tago-${city.city_code}-${route.route_id.toLowerCase()}`)
    .map(route=>({id:route.id,label:route.label,configured,search:`${route.label} ${route.route_no} ${route.start_station_name??''} ${route.end_station_name??''}`}));
}
export async function readBusCatalogSelection(reader:ReturnType<typeof createTransitCatalogReader>,reference:TransitCatalogRef,cityCode:string){
  const catalog=await reader.manifest(reference),selected=catalog.cities.find(city=>city.city_code===cityCode);
  const city=busCityAvailability(selected)==='available'?await reader.city(reference,cityCode):null;
  return {catalog,routes:city?.routes??NO_ROUTES};
}
function useBusCatalog(reference:TransitCatalogRef|undefined,cityCode:string,active:boolean){
  const url=reference?.url,sha256=reference?.sha256,byteLength=reference?.byte_length;
  const key=reference?`${url}:${sha256}:${byteLength}`:'';
  const [state,setState]=useState<{key:string;city:string;catalog:TransitRouteCatalog|null;routes:TransitRoute[];loading:boolean;error:string}>({key:'',city:'',catalog:null,routes:NO_ROUTES,loading:false,error:''});
  useEffect(()=>{
    if(!url||!sha256||!byteLength||!active)return;
    const ref={url,sha256,byte_length:byteLength};let controller:AbortController|null=null,stopped=false;
    const load=async()=>{
      controller?.abort();if(stopped||document.hidden)return;
      const request=new AbortController();controller=request;
      const reader=createTransitCatalogReader((input,init)=>fetch(input,{...init,signal:init?.signal?AbortSignal.any([init.signal,request.signal]):request.signal}));
      setState(previous=>({key,city:cityCode,catalog:previous.key===key?previous.catalog:null,routes:NO_ROUTES,loading:true,error:''}));
      try{
        const selection=await readBusCatalogSelection(reader,ref,cityCode);if(stopped||request.signal.aborted)return;
        setState({key,city:cityCode,...selection,loading:false,error:''});
      }catch(error){if(!stopped&&!request.signal.aborted)setState(previous=>({...previous,key,city:cityCode,routes:NO_ROUTES,loading:false,error:error instanceof Error?error.message:'도시별 노선을 불러오지 못했습니다.'}));}
    };
    const visibility=()=>{if(document.hidden)controller?.abort();else void load();};
    document.addEventListener('visibilitychange',visibility);void load();
    return()=>{stopped=true;controller?.abort();document.removeEventListener('visibilitychange',visibility);};
  },[url,sha256,byteLength,cityCode,active,key]);
  return {catalog:state.key===key?state.catalog:null,routes:state.key===key&&state.city===cityCode?state.routes:NO_ROUTES,loading:state.key===key&&state.city===cityCode&&state.loading,error:state.key===key&&state.city===cityCode?state.error:''};
}
function ObservationImage({frame}:{frame:PanelLiveFrame}){
  const [failed,setFailed]=useState(false);
  return failed?<div className="live-empty" role="status">관측 영상 파일을 불러오지 못했습니다. <button onClick={()=>setFailed(false)}>다시 보기</button></div>:<figure className="live-radar-image">
    <img src={frame.url} width={frame.image_size?.[0]} height={frame.image_size?.[1]} alt={`기상청 ${frame.kind==='radar'?'전국 합성레이더':'천리안2A 적외영상'}, ${timeLabel(frame.time)}`} onError={()=>setFailed(true)}/>
    <figcaption>기상청 원본 영상 · 지도와 범례 포함</figcaption>
  </figure>;
}
export default function CurrentObservations({onTransit,onLocate,onClose}:Props){
  const [kind,setKind]=useState<LiveTransitKind>('bus');
  const [targetId,setTargetId]=useState('');
  const [showBus,setShowBus]=useState(true);
  const [cityCode,setCityCode]=useState('');
  const [cityQuery,setCityQuery]=useState('');
  const cityFilter=useDeferredValue(cityQuery);
  const [routeQuery,setRouteQuery]=useState('');
  const routeFilter=useDeferredValue(routeQuery).trim().toLocaleLowerCase('ko-KR');
  const [weatherKind,setWeatherKind]=useState<LiveWeatherKind>('radar');
  const [listLimit,setListLimit]=useState(40);
  const [now,setNow]=useState(Date.now);
  const targets=useLiveResource(`${LIVE_TRANSIT_API_PREFIX}/targets`,decodeLiveTargets,targetsRefresh);
  const busCatalog=useBusCatalog(targets.data?.bus_catalog,cityCode,kind==='bus');
  const cityChoices=useMemo(()=>filterBusCities(busCatalog.catalog?.cities??NO_CITIES,cityFilter,cityCode),[busCatalog.catalog,cityFilter,cityCode]);
  const selectedCity=busCatalog.catalog?.cities.find(city=>city.city_code===cityCode);
  const cityAvailability=busCityAvailability(selectedCity);
  const catalogRouteCount=useMemo(()=>busCatalog.catalog?.cities.reduce((sum,city)=>sum+city.route_count,0)??0,[busCatalog.catalog]);
  const allOptions=useMemo(()=>kind==='bus'?nationalBusRouteOptions(selectedCity,busCatalog.routes,Boolean(targets.data?.bus_catalog?.configured)):targets.data?.subway_lines.map(value=>({id:value.id,label:value.name,configured:value.configured,search:value.name})),[kind,selectedCity,busCatalog.routes,targets.data]);
  const options=useMemo(()=>{
    const matches=allOptions?.filter(value=>!routeFilter||kind!=='bus'||value.search.toLocaleLowerCase('ko-KR').includes(routeFilter)).slice(0,120)??[];
    const current=allOptions?.find(value=>value.id===targetId);if(current&&!matches.some(value=>value.id===current.id)){matches.unshift(current);matches.splice(120);}
    return matches;
  },[allOptions,routeFilter,kind,targetId]);
  const selectedTarget=allOptions?.find(value=>value.id===targetId&&value.configured);
  const transit=useLiveResource(selectedTarget?`${LIVE_TRANSIT_API_PREFIX}/${kind}?${kind==='bus'?'route':'line'}=${encodeURIComponent(selectedTarget.id)}`:null,decodeLiveTransit,snapshotRefresh,transitExpiry);
  const weather=useLiveResource(`${LIVE_WEATHER_API_PREFIX}/${weatherKind}`,decodeLiveWeather,snapshotRefresh,weatherExpiry);
  const snapshot=transit.data?.kind===kind&&transit.data.target.id===selectedTarget?.id?transit.data:null;
  const weatherData=weather.data?.kind===weatherKind?weather.data:null;
  const vehicles=snapshot&&!transit.expired&&!transit.paused?liveTransitVehicles(snapshot,now):[];
  const transitFresh=Boolean(snapshot&&!transit.expired&&!transit.paused&&isLiveTransitFresh(snapshot,now));
  const panelFrame=weatherData&&!weather.expired&&!weather.paused?currentPanelFrame(weatherData,now):null;
  useEffect(()=>{
    let timer:ReturnType<typeof setInterval>|undefined;
    const change=()=>{clearInterval(timer);if(!document.hidden){setNow(Date.now());timer=setInterval(()=>setNow(Date.now()),1000);}};
    document.addEventListener('visibilitychange',change);change();
    return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',change);};
  },[]);
  useEffect(()=>{
    onTransit(kind==='bus'&&showBus&&!transit.expired&&!transit.paused?snapshot:null);
  },[kind,showBus,snapshot,transit.expired,transit.paused,onTransit]);
  useEffect(()=>()=>onTransit(null),[onTransit]);
  const selectKind=(value:LiveTransitKind)=>{setKind(value);setTargetId('');setListLimit(40);};
  const selectCity=(value:string)=>{setCityCode(value);setTargetId('');setRouteQuery('');setListLimit(40);};
  const cityMessage=selectedCity?(cityAvailability==='unsupported'?`${selectedCity.city_name}는 현재 연결한 TAGO 버스 위치 조회를 지원하지 않습니다.`:
    cityAvailability==='failed'?`${selectedCity.city_name}의 공식 노선 목록을 확인하지 못해 조회할 수 없습니다.`:
    cityAvailability==='empty'?`${selectedCity.city_name}의 공식 목록에 등록된 노선이 없습니다.`:''):'';
  const transitMessage=transit.paused?'화면이 보이지 않아 조회를 잠시 멈췄습니다.':transit.error||snapshot?.error?.message||
    (transit.expired?'유효 시간이 지나 위치 표시를 지웠습니다. 다음 조회를 기다립니다.':snapshot?.status==='empty'?'이 노선에서 현재 제공된 운행체가 없습니다.':snapshot?.status==='partial'?'제공된 유효 자료만 표시합니다. 일부 응답은 제외되었습니다.':'');
  const weatherMessage=weather.paused?'화면이 보이지 않아 조회를 잠시 멈췄습니다.':weather.error||weatherData?.error?.message||
    (weather.expired?'관측 유효 시간이 지나 영상 표시를 지웠습니다.':weatherData&&!panelFrame?'현재 표시할 수 있는 최신 영상이 없습니다.':'');
  const transitSource=snapshot?publicSourceUrl(snapshot.source.page_url):undefined;
  const weatherSource=weatherData?publicSourceUrl(weatherData.source.page_url):undefined;
  return <section className="current-observations panel" aria-label="현재 관측" data-live-transit-state={transit.expired?'expired':snapshot?.status??'idle'} data-live-weather-state={weather.expired?'expired':weatherData?.status??'loading'}>
    <button className="observations-close" aria-label="관측 패널 닫기" onClick={onClose}>×</button><header className="current-observations-header"><span className="eyebrow">CURRENT OBSERVATIONS</span><h2>현재 자료 조회</h2><p>선택한 원천의 최신 응답을 확인합니다.</p></header>
    <section className="live-section" aria-labelledby="live-transit-heading">
      <div className="live-section-heading"><h3 id="live-transit-heading">교통</h3><button className="live-refresh" disabled={!selectedTarget||transit.loading||transit.paused} onClick={transit.refresh}>{transit.loading?'조회 중…':'새로 확인'}</button></div>
      <div className="live-kind-switch" aria-label="교통 종류"><button aria-pressed={kind==='bus'} onClick={()=>selectKind('bus')}>버스 위치</button><button aria-pressed={kind==='subway'} onClick={()=>selectKind('subway')}>지하철 역 상태</button></div>
      {kind==='bus'&&<>
        <label className="live-target-label" htmlFor="live-bus-city-search">도시 이름으로 찾기</label>
        <input className="live-route-search" id="live-bus-city-search" type="search" value={cityQuery} maxLength={60} disabled={!busCatalog.catalog} onChange={event=>{setCityQuery(event.target.value);selectCity('');}} placeholder="예: 서울, 부산, 인천, 제주" aria-describedby="live-bus-city-help"/>
        <label className="live-target-label" htmlFor="live-bus-city">조회할 도시</label>
        <select id="live-bus-city" value={cityCode} onChange={event=>selectCity(event.target.value)} disabled={!busCatalog.catalog||!cityChoices.options.length} aria-describedby="live-bus-city-help">
          <option value="">{targets.loading||busCatalog.loading&&!cityCode?'도시 목록을 확인하는 중…':'도시를 선택하세요'}</option>
          {cityChoices.options.map(city=><option key={city.city_code} value={city.city_code}>{city.city_name}{!city.location_service_supported?' · 위치 조회 미지원':city.status!=='complete'?' · 목록 확인 실패':!city.route_count?' · 노선 없음':` · ${city.route_count}노선`}</option>)}
        </select>
        <p id="live-bus-city-help" className="live-method">{busCatalog.catalog?`공식 목록 ${busCatalog.catalog.cities.length.toLocaleString()}개 도시 · ${catalogRouteCount.toLocaleString()}노선. 이름으로 찾은 도시를 선택하세요.`:'공식 도시 목록을 확인한 뒤 도시와 노선을 직접 선택합니다.'}</p>
        {busCatalog.catalog&&cityChoices.matchCount>CITY_OPTION_LIMIT&&<p className="live-method">{cityChoices.matchCount}개 중 최대 {CITY_OPTION_LIMIT}개 표시 · 도시 이름으로 범위를 좁혀 주세요.</p>}
        {busCatalog.catalog&&!cityChoices.matchCount&&cityFilter.trim()&&<p className="live-empty" role="status">현재 TAGO 공식 도시 목록에 “{cityFilter.trim()}”와 일치하는 도시가 없어 이 화면에서 버스 위치를 조회할 수 없습니다.</p>}
        {targets.data&&!targets.data.bus_catalog&&<p className="live-empty" role="status">전국 버스 도시 목록이 아직 연결되지 않았습니다.</p>}
        {busCatalog.loading&&<p className="live-method" role="status">{cityCode?'선택한 도시의 노선 목록을 읽고 있습니다.':'전국 도시 목록을 읽고 있습니다.'}</p>}
        {busCatalog.error&&<p className="live-error" role="status">{busCatalog.error}</p>}
        {cityMessage&&<p className="live-empty" role="status">{cityMessage}</p>}
        {selectedCity?.retrieved_at&&<p className="live-method">{selectedCity.city_name} 노선 목록 확인: {timeLabel(selectedCity.retrieved_at)}</p>}
        {cityCode&&!!busCatalog.routes.length&&<><label className="live-target-label" htmlFor="live-bus-route-search">노선 번호·정류장으로 찾기</label><input className="live-route-search" id="live-bus-route-search" value={routeQuery} maxLength={80} onChange={event=>setRouteQuery(event.target.value)} placeholder="예: 102, 시청, 터미널"/><p className="live-method">{busCatalog.routes.length.toLocaleString()}노선 중 {options.length}개 표시 · 범위를 좁혀 선택하세요.</p></>}
      </>}
      <label className="live-target-label" htmlFor="live-transit-target">{kind==='bus'?'조회할 노선':'조회할 지하철 노선'}</label>
      <select id="live-transit-target" value={selectedTarget?.id??''} onChange={event=>{setTargetId(event.target.value);setListLimit(40);}} disabled={!options?.some(value=>value.configured)}>
        <option value="">{kind==='bus'&&!cityCode?'도시를 먼저 선택하세요':targets.loading||busCatalog.loading?'대상 목록을 확인하는 중…':kind==='bus'&&cityAvailability==='unsupported'?'이 도시의 버스 위치는 조회할 수 없습니다':options?.length?'노선을 선택하세요':routeFilter&&allOptions?.length?'검색과 일치하는 노선이 없습니다':'등록된 조회 대상이 없습니다'}</option>
        {options?.map(value=><option key={value.id} value={value.id} disabled={!value.configured}>{value.label}{value.configured?'':' · 연결 대기'}</option>)}
      </select>
      {targets.error&&<p className="live-error" role="status">{targets.error} <button onClick={targets.refresh}>목록 다시 확인</button></p>}
      {options?.length&&!options.some(value=>value.configured)?<p className="live-empty">등록된 노선의 원천 자료 연결을 기다리고 있습니다.</p>:null}
      {kind==='bus'?<label className="live-map-toggle"><input type="checkbox" checked={showBus} onChange={event=>setShowBus(event.target.checked)}/>버스 위치를 지도에 표시</label>:<p className="live-method">역 이름과 원천 상태를 목록으로 표시합니다. 지도 좌표는 연결되지 않았습니다.</p>}
      {snapshot&&<dl className="live-timestamps"><dt>원천 측정 시각</dt><dd>미제공{kind==='subway'?' · 수신 시각은 각 행에 표시':''}</dd><dt>서버 원천 조회</dt><dd>{timeLabel(snapshot.retrieved_at)}<small>{ageLabel(snapshot.retrieved_at,now)}</small></dd><dt>응답 시각</dt><dd>{timeLabel(snapshot.served_at)}</dd></dl>}
      {transitMessage&&<p className="live-error" role="status">{transitMessage}</p>}
      {transitFresh&&<p className="live-count">{vehicles.length.toLocaleString()}개 운행체 · {snapshot?.status==='partial'?'일부 유효 자료':'선택 노선의 응답'}</p>}
      {!!vehicles.length&&<ul className="live-vehicles">{vehicles.slice(0,listLimit).map(vehicle=><li key={vehicle.id}>
        {vehicle.kind==='bus'?<button onClick={()=>onLocate({id:`live-bus:${vehicle.id}`,name:vehicle.label,region:'제공기관의 현재 버스 위치',lon:vehicle.position.lon,lat:vehicle.position.lat,range:1600})}><strong>{vehicle.label}</strong><span>{vehicle.station_name??'정류장 이름 미제공'} <b aria-hidden="true">↗</b></span></button>:<div><strong>{vehicle.station_name} · {vehicle.direction==='up'?'상행':'하행'}</strong><span>{({'approaching':'진입','arrived':'도착','departed':'출발','left-previous':'전역 출발'} as const)[vehicle.reported_status]} · 열차 {vehicle.train_no}</span><small>원천 수신 {timeLabel(vehicle.source_received_at)} · {ageLabel(vehicle.source_received_at,now)}</small></div>}
      </li>)}</ul>}
      {vehicles.length>listLimit&&<button className="live-more" onClick={()=>setListLimit(value=>value+40)}>나머지 {vehicles.length-listLimit}개 더 보기</button>}
      {kind==='bus'&&!!vehicles.length&&<p className="live-method">제공기관의 도로 보정 위치입니다. 조회 사이의 이동 경로와 높이는 관측값으로 만들지 않습니다.</p>}
      {transitSource&&<a className="live-source" href={transitSource} target="_blank" rel="noreferrer">{kind==='bus'?'국토교통부 TAGO':'서울특별시'} 원천 ↗ <small>{snapshot?.source.license}</small></a>}
    </section>
    <section className="live-section" aria-labelledby="live-weather-heading">
      <div className="live-section-heading"><h3 id="live-weather-heading">기상</h3><button className="live-refresh" disabled={weather.loading||weather.paused} onClick={weather.refresh}>{weather.loading?'조회 중…':'새로 확인'}</button></div>
      <div className="live-kind-switch" aria-label="기상 종류"><button aria-pressed={weatherKind==='radar'} onClick={()=>setWeatherKind('radar')}>레이더</button><button aria-pressed={weatherKind==='satellite'} onClick={()=>setWeatherKind('satellite')}>위성</button></div>
      {weatherData&&<dl className="live-timestamps"><dt>원천 관측</dt><dd>{timeLabel(weatherData.latest_observed_at)}<small>{ageLabel(weatherData.latest_observed_at,now)}</small></dd><dt>서버 원천 확인</dt><dd>{timeLabel(weatherData.checked_at)}</dd><dt>응답 시각</dt><dd>{timeLabel(weatherData.served_at)}</dd></dl>}
      {weatherMessage&&<p className="live-error" role="status">{weatherMessage}</p>}
      {panelFrame&&<ObservationImage key={panelFrame.url} frame={panelFrame}/>}
      {panelFrame&&<p className="live-method">원본 영상 안의 지도·범례를 함께 확인하세요. 배경색만으로 비가 없는 지역이라고 판정하지 않습니다.</p>}
      {weatherSource&&<a className="live-source" href={weatherSource} target="_blank" rel="noreferrer">{weatherData?.source.name} ↗</a>}
    </section>
    <p className="live-share-note">{LIVE_SHARE_NOTICE}</p>
  </section>;
}
