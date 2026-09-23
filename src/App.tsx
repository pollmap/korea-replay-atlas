import {lazy,Suspense,useCallback,useEffect,useMemo,useRef,useState} from 'react';
import type {Catalog,LayerId,Place} from '../shared/contracts';
import {EVIDENCE_LABEL} from '../shared/contracts';
import {EMPTY_CATALOG,PLACES,SOURCES} from '../shared/sources';
import {kstDate,kstInstant,kstSeconds,recordingDays,solarPosition} from '../shared/time';
import type {MapHandle} from './MapScene';
import MapErrorBoundary from './MapErrorBoundary';
import {fetchPublicCatalog} from './catalog';
import type {SearchRequest,SearchResponse} from './search.worker';
import type {PerformanceSnapshot} from '../shared/map-performance';
import {QUALITY_NOTICES,type MapSelection} from '../shared/selection';
import {assertPinnedDeployment,validateRuntime,versionedShareUrl,type RuntimeVersion} from '../shared/share';
import type {LiveTransitSnapshot} from '../shared/live-transit';
import {LIVE_SHARE_NOTICE} from './live-client';
import CurrentObservations from './CurrentObservations';
import MapInteractionTools from './MapInteractionTools';
import {EMPTY_MEASUREMENT,measureMap} from '../shared/map-tools';
import {readFlatCamera,type FlatCamera} from '../shared/map2d';
import {assertPinnedDeploymentV2,validateRuntimeV2,versionedShareUrlV2,type RuntimeV2} from '../shared/runtime-v2';
import {useAtlas} from './useAtlas';
import {regionNavigationPlace} from './region-navigation';
import type {PropertyViewState} from './PropertyExplorer';
const MapScene=lazy(()=>import('./MapScene'));
const Map2D=lazy(()=>import('./Map2D'));
const PropertyExplorer=lazy(()=>import('./PropertyExplorer'));
type ExploreMode='map'|'replay'|'sun'|'live';
const timeFormatter=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
const initialLayers:Record<LayerId,boolean>={terrain:true,buildings:true,infrastructure:true,rail:true,bus:false,depth:false,satellite:false,radar:false,sun:false};
export type Selection=MapSelection;
function readInitial(){
  const p=new URLSearchParams(location.hash.slice(1));
  let place=PLACES.find(v=>v.id===p.get('place'))??PLACES[0];
  const coordinates=p.get('position')?.split(',').map(Number);
  if(coordinates?.length===3&&coordinates.every(Number.isFinite)&&coordinates[0]>=124&&coordinates[0]<=132.5&&coordinates[1]>=32&&coordinates[1]<=39.5&&coordinates[2]>=500&&coordinates[2]<=2500000){place={id:p.get('place')?.slice(0,180)||'shared',name:p.get('name')?.slice(0,100)||'공유한 장소',region:p.get('region')?.slice(0,100)||'공개 지도 시설',lon:coordinates[0],lat:coordinates[1],range:coordinates[2]};}
  const parsed=Date.parse(p.get('time')??'');
  const sharedLayers=p.has('layers')?Object.fromEntries(Object.keys(initialLayers).map(key=>[key,p.get('layers')!.split(',').includes(key)])) as Record<LayerId,boolean>:{...initialLayers,sun:p.get('mode')==='sun'};
  const versions=new URLSearchParams(location.search).getAll('deployment');
  const mode:ExploreMode=p.get('mode')==='replay'?'replay':p.get('mode')==='sun'?'sun':'map';
  const view:'2d'|'3d'=p.get('view')==='2d'?'2d':p.get('view')==='3d'||mode==='replay'||mode==='sun'?'3d':'2d';
  return {place,view,layers:sharedLayers,release:p.get('release'),deployment:versions.length>1?'invalid':versions[0]??null,time:Number.isFinite(parsed)?parsed:kstInstant(kstDate(Date.now()),12*3600),mode,unavailableLiveShare:p.get('mode')==='live'};
}
export default function App(){
  const [initial]=useState(readInitial);
  const [catalog,setCatalog]=useState<Catalog>(EMPTY_CATALOG);
  const [catalogError,setCatalogError]=useState('');
  const [place,setPlace]=useState<Place>(initial.place);
  const [instant,setInstant]=useState(initial.time);
  const [mode,setMode]=useState<ExploreMode>(initial.mode);
  const [mapView,setMapView]=useState<'2d'|'3d'>(initial.view);
  const [liveInstant,setLiveInstant]=useState(Date.now);
  const [liveTransit,setLiveTransit]=useState<LiveTransitSnapshot|null>(null);
  const [layers,setLayers]=useState(initial.layers);
  const [playing,setPlaying]=useState(false);
  const [speed,setSpeed]=useState(300);
  const [query,setQuery]=useState('');
  const [searchResults,setSearchResults]=useState<Place[]>([]);
  const [searchStatus,setSearchStatus]=useState('');
  const [selection,setSelection]=useState<Selection|null>(null);
  const [sourcesOpen,setSourcesOpen]=useState(false);
  const [notice,setNotice]=useState(initial.unavailableLiveShare?LIVE_SHARE_NOTICE:'');
  const [mapStatus,setMapStatus]=useState('지도를 준비하고 있습니다');
  const [lightweight,setLightweight]=useState(false);
  const [runtime,setRuntime]=useState<RuntimeVersion|RuntimeV2|null>(null);
  const [runtimeChecked,setRuntimeChecked]=useState(false);
  const [runtimeError,setRuntimeError]=useState('');
  const atlas=useAtlas(runtime&&'schema_version' in runtime?runtime:null,runtimeChecked,runtimeError);
  const [propertyOpen,setPropertyOpen]=useState(true);
  const [requestedRegion,setRequestedRegion]=useState<{code:string;request:number}>();
  const selectPropertyRegion=useCallback((code:string)=>{setPropertyOpen(true);setSelection(null);setMenuOpen(false);setCityToolsOpen(false);setFocusMode(false);setMode('map');setPlaying(false);setTimeOpen(false);setSunOpen(false);setLayers(previous=>({...previous,sun:false}));setRequestedRegion(previous=>({code,request:(previous?.request??0)+1}));},[]);
  const [boundaries,setBoundaries]=useState(()=>new URLSearchParams(location.hash.slice(1)).get('boundaries')!=='off');
  const propertyViewRef=useRef<PropertyViewState|null>(null);
  const onPropertyView=useCallback((value:PropertyViewState)=>{propertyViewRef.current=value;},[]);
  const [menuOpen,setMenuOpen]=useState(false);
  const [cityToolsOpen,setCityToolsOpen]=useState(false);
  const [focusMode,setFocusMode]=useState(false);
  const [timeOpen,setTimeOpen]=useState(initial.mode==='replay'||initial.mode==='sun');
  const [timeExpanded,setTimeExpanded]=useState(false);
  const [sunOpen,setSunOpen]=useState(false);
  const [observationsOpen,setObservationsOpen]=useState(true);
  const [performanceInfo,setPerformanceInfo]=useState<PerformanceSnapshot|null>(null);
  const [measurement,setMeasurement]=useState(EMPTY_MEASUREMENT);
  const [searchActive,setSearchActive]=useState(-1);
  const [flatCamera,setFlatCamera]=useState<FlatCamera|null>(()=>readFlatCamera(location.hash));
  const [spatialCamera,setSpatialCamera]=useState<number[]|null|undefined>(undefined);
  const mapRef=useRef<MapHandle|null>(null);
  const propertyFrameDone=useRef(false);
  const dialogRef=useRef<HTMLElement|null>(null);
  const searchRef=useRef<HTMLInputElement|null>(null);
  const cityToolsRef=useRef<HTMLDivElement|null>(null);
  const searchWorkerRef=useRef<Worker|null>(null);
  const searchRequestRef=useRef(0);
  useEffect(()=>{
    if(mode!=='live')return;
    const timer=window.setInterval(()=>{if(!document.hidden)setLiveInstant(Date.now());},15000);
    const visible=()=>{if(!document.hidden)setLiveInstant(Date.now());};
    document.addEventListener('visibilitychange',visible);
    return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',visible);};
  },[mode]);
  useEffect(()=>{
    const controller=new AbortController();
    void fetch('/api/v2/runtime',{signal:controller.signal,cache:'no-store'}).then(async first=>{
      const response=first.status===404?await fetch('/api/v1/runtime',{signal:controller.signal,cache:'no-store'}):first;
      if(!response.ok)throw new Error('배포 버전을 확인하지 못했습니다.');
      const payload=await response.json();
      const value=payload&&typeof payload==='object'&&'schema_version' in payload&&payload.schema_version===2?validateRuntimeV2(payload):validateRuntime(payload);
      if('schema_version' in value)assertPinnedDeploymentV2(initial.deployment,value,location.origin);else assertPinnedDeployment(initial.deployment,value);
      if(!controller.signal.aborted){setRuntime(value);setRuntimeChecked(true);}
    }).catch(error=>{if(!controller.signal.aborted){setRuntimeError(error instanceof Error?error.message:'배포 버전을 확인하지 못했습니다.');setRuntimeChecked(true);}});
    return()=>controller.abort();
  },[initial.deployment]);
  useEffect(()=>()=>{searchWorkerRef.current?.terminate();searchWorkerRef.current=null;},[]);
  useEffect(()=>{
    const shortcut=(event:KeyboardEvent)=>{
      if(sourcesOpen||event.ctrlKey||event.metaKey||event.altKey)return;
      const editing=event.target instanceof HTMLInputElement||event.target instanceof HTMLTextAreaElement||event.target instanceof HTMLSelectElement||(event.target as HTMLElement)?.isContentEditable;
      if(event.key==='/'&&!editing){event.preventDefault();setFocusMode(false);requestAnimationFrame(()=>searchRef.current?.focus());}
      if(event.key.toLowerCase()==='f'&&!editing&&!event.ctrlKey&&!event.metaKey&&!event.altKey){event.preventDefault();setFocusMode(value=>!value);setQuery('');setCityToolsOpen(false);}
      if(event.key==='Escape'){setQuery('');setMenuOpen(false);if(cityToolsRef.current?.contains(document.activeElement))cityToolsRef.current.querySelector<HTMLButtonElement>('.city-tools-toggle')?.focus();setCityToolsOpen(false);setFocusMode(false);}
    };
    document.addEventListener('keydown',shortcut);return()=>document.removeEventListener('keydown',shortcut);
  },[sourcesOpen]);
  useEffect(()=>{
    if(!cityToolsOpen)return;
    const outside=(event:PointerEvent)=>{if(event.target instanceof Node&&!cityToolsRef.current?.contains(event.target))setCityToolsOpen(false);};
    document.addEventListener('pointerdown',outside);return()=>document.removeEventListener('pointerdown',outside);
  },[cityToolsOpen]);
  useEffect(()=>{
    const id=++searchRequestRef.current;
    if(!query.trim())return;
    let active=true;
    const timer=window.setTimeout(()=>{
      setSearchStatus('검색 중…');
      try{
        const worker=searchWorkerRef.current??new Worker(new URL('./search.worker.ts',import.meta.url),{type:'module'});
        searchWorkerRef.current=worker;
        worker.onmessage=(event:MessageEvent<SearchResponse>)=>{
          if(!active||event.data.id!==searchRequestRef.current)return;
          const data=event.data;setSearchResults(data.places);setSearchStatus(data.error??(data.places.length?'':'공개 검색 색인에서 일치하는 장소를 찾지 못했습니다.'));
        };
        worker.onerror=()=>{if(!active)return;setSearchResults([]);setSearchStatus('검색 처리를 시작하지 못했습니다. 다시 검색해 주세요.');worker.terminate();if(searchWorkerRef.current===worker)searchWorkerRef.current=null;};
        const index=catalog.assets.find(asset=>asset.format==='search-index');
        worker.postMessage({id,query,index:index?{url:index.url,sha256:index.sha256}:undefined} satisfies SearchRequest);
      }catch{if(active){setSearchResults([]);setSearchStatus('이 브라우저에서 검색 처리를 시작하지 못했습니다.');}}
    },180);
    return()=>{active=false;clearTimeout(timer);};
  },[query,catalog.assets]);
  useEffect(()=>{
    if(!sourcesOpen)return;
    const before=document.activeElement as HTMLElement|null;
    const dialog=dialogRef.current;
    dialog?.querySelector<HTMLButtonElement>('button')?.focus();
    const keydown=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){setSourcesOpen(false);return;}
      if(event.key!=='Tab'||!dialog)return;
      const nodes=dialog.querySelectorAll<HTMLElement>('button,a[href],input,select,[tabindex="0"]');
      const first=nodes[0],last=nodes[nodes.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
    };
    document.addEventListener('keydown',keydown);
    return()=>{document.removeEventListener('keydown',keydown);before?.focus();};
  },[sourcesOpen]);
  useEffect(()=>{
    const controller=new AbortController();
    const refresh=()=>fetchPublicCatalog(initial.release,controller.signal).then(next=>{setCatalogError('');setCatalog(previous=>previous.release_id===next.release_id?previous:next);}).catch(e=>{if(e.name!=='AbortError')setCatalogError(e.message);});
    void refresh();const timer=initial.release?null:window.setInterval(refresh,60000);
    return()=>{controller.abort();if(timer!==null)clearInterval(timer);};
  },[initial.release]);
  const days=useMemo(()=>recordingDays(catalog.assets),[catalog.assets]);
  const recordedDay=days.find(d=>d.date===kstDate(instant));
  const replayTo=recordedDay?.to??null;
  useEffect(()=>{
    if(!playing)return;
    let previous=performance.now();
    const timer=window.setInterval(()=>{
      const now=performance.now(),delta=(now-previous)*speed;previous=now;
      setInstant(t=>{
        const next=t+delta;
        if(mode==='replay'&&replayTo!==null)return Math.min(next,replayTo);
        const end=kstInstant(kstDate(t),86399);return next>end?kstInstant(kstDate(t),0):next;
      });
    },100);
    return()=>clearInterval(timer);
  },[playing,speed,mode,replayTo]);
  useEffect(()=>{if(mode==='replay'&&replayTo!==null&&instant>=replayTo)setPlaying(false);},[instant,mode,replayTo]);
  const goTo=useCallback((p:Place)=>{setPlace(p);setQuery('');setSelection(null);mapRef.current?.flyTo(p,{overviewPanelVisible:!focusMode&&mode==='map'&&!menuOpen&&propertyOpen,focused:focusMode});},[focusMode,mode,menuOpen,propertyOpen]);
  useEffect(()=>{
    if(propertyFrameDone.current||!atlas.content)return;propertyFrameDone.current=true;
    const params=new URLSearchParams(location.hash.slice(1));
    if(params.has('flatCamera')||params.has('camera')||params.has('position'))return;
    const region=atlas.content.regions.regions.find(row=>row.lawd_code===params.get('regionCode'));
    const target=region?regionNavigationPlace(region,atlas.content.map):null;if(target)goTo(target);
  },[atlas.content,goTo]);
  const inspect=useCallback((value:Selection|null)=>{setSelection(value);if(value){setMenuOpen(false);setSunOpen(false);setObservationsOpen(false);}},[]);
  const acceptLiveTransit=useCallback((value:LiveTransitSnapshot|null)=>{setLiveTransit(value);setSelection(previous=>previous?.properties?.live?null:previous);},[]);
  const viewInstant=mode==='live'?liveInstant:instant;
  const solar=useMemo(()=>solarPosition(viewInstant,place.lat,place.lon),[viewInstant,place.lat,place.lon]);
  const localLayers=useMemo(()=>new Set(catalog.layers.filter(layer=>layer.id==='sun'||place.id==='korea'||catalog.assets.some(a=>a.layer===layer.id&&a.bbox[0]<=place.lon&&a.bbox[2]>=place.lon&&a.bbox[1]<=place.lat&&a.bbox[3]>=place.lat)).map(layer=>layer.id)),[catalog.layers,catalog.assets,place]);
  const seconds=kstSeconds(instant);
  const date=kstDate(instant);
  const timeLabel=timeFormatter.format(new Date(instant));
  const visiblePlaces=query?searchResults:[];
  const switchMode=(next:ExploreMode)=>{
    setCityToolsOpen(false);
    if(next==='replay'&&!days.length){setNotice('재생 가능한 관측 기록이 아직 적재되지 않았습니다. 햇빛 실험은 바로 이용할 수 있습니다.');return;}
    setMode(next);setPlaying(false);setNotice('');
    setTimeOpen(next==='sun'||next==='replay');setSunOpen(next==='sun');
    if((next==='map'&&mapView!=='2d')||((next==='sun'||next==='replay')&&mapView!=='3d')){const position=mapRef.current?.viewport?.();if(position)setPlace(position);setFlatCamera(null);setSpatialCamera(null);setMeasurement(EMPTY_MEASUREMENT);setPerformanceInfo(null);}
    if(next==='map')setMapView('2d');
    if(next==='sun'||next==='replay')setMapView('3d');
    setLayers(previous=>({...previous,sun:next==='sun'}));
    if(next==='live'){setLiveInstant(Date.now());setObservationsOpen(true);}
    if(next==='replay')selectRecordedDay(recordedDay?.date??days.at(-1)!.date);
  };
  const selectRecordedDay=(date:string)=>{
    const day=days.find(d=>d.date===date);if(!day)return;
    setInstant(day.from);setPlaying(false);
    setLayers(prev=>({...prev,...Object.fromEntries(day.layers.map(id=>[id,true]))}));
  };
  const minSeconds=mode==='replay'&&recordedDay?kstSeconds(recordedDay.from):0;
  const maxSeconds=mode==='replay'&&recordedDay?kstSeconds(recordedDay.to):86399;
  const timeTicks=mode==='replay'&&recordedDay?Array.from({length:7},(_,i)=>{const value=Math.floor((minSeconds+(maxSeconds-minSeconds)*i/6)/60);return `${String(Math.floor(value/60)).padStart(2,'0')}:${String(value%60).padStart(2,'0')}`;}):['00:00','04:00','08:00','12:00','16:00','20:00','24:00'];
  const share=async()=>{
    if(mode==='live'){setNotice(LIVE_SHARE_NOTICE);return;}
    if(catalogError){setNotice('공유할 지도 자료가 정상적으로 연결되지 않았습니다.');return;}
    if(!runtime){setNotice(runtimeError||'배포 버전을 확인하고 있습니다. 잠시 뒤 공유해 주세요.');return;}
    const params=new URLSearchParams({place:place.id,time:new Date(instant).toISOString(),mode});
    params.set('position',[place.lon,place.lat,place.range].join(','));params.set('name',place.name);params.set('region',place.region);
    params.set('layers',Object.entries(layers).filter(([,v])=>v).map(([k])=>k).join(','));
    params.set('release',catalog.release_id);
    params.set('view',mapView);
    params.set('boundaries',boundaries?'on':'off');
    const flatCamera=mapRef.current?.flatCamera?.();if(flatCamera)params.set('flatCamera',flatCamera.join(','));
    const camera=mapRef.current?.camera();if(camera)params.set('camera',camera.join(','));
    const propertyView=propertyViewRef.current;
    if(propertyView&&atlas.content){params.set('propertyRelease',atlas.content.property.release_id);params.set('regionCode',propertyView.region);params.set('trade',propertyView.trade);params.set('month',propertyView.month);params.set('complex',propertyView.complex);params.set('area',propertyView.area);params.set('historyMonths',String(propertyView.historyMonths));params.set('compareRegions',propertyView.compare.join(','));params.set('compareComplexes',propertyView.compareComplexes.join(','));if(propertyView.includeReview)params.set('review','include');else params.delete('review');}
    let url:string;try{url='schema_version' in runtime?versionedShareUrlV2(runtime,catalog.release_id,params):versionedShareUrl(runtime,catalog.release_id,params);}catch(error){setNotice(error instanceof Error?error.message:'공유 링크를 만들지 못했습니다.');return;}
    try{await navigator.clipboard.writeText(url);setNotice('현재 장소·시각·자료 버전의 링크를 복사했습니다.');}
    catch{setNotice(`링크 복사가 허용되지 않았습니다. 공유 주소: ${url}`);}
  };
  const deploymentBlocked=initial.deployment!==null&&(!runtimeChecked||!!runtimeError);
  const ActiveMap=mapView==='2d'?Map2D:MapScene;
  const changeView=(view:'2d'|'3d')=>{if(view===mapView)return;const position=mapRef.current?.viewport?.();if(position)setPlace(position);setFlatCamera(null);setSpatialCamera(null);setPerformanceInfo(null);setMapView(view);setMeasurement(EMPTY_MEASUREMENT);if(view==='2d'&&(mode==='sun'||mode==='replay'))switchMode('map');};
  const propertyRequested=!focusMode&&mode==='map'&&!menuOpen&&!selection&&propertyOpen;
  const propertyVisible=!!atlas.content&&propertyRequested;
  const showNational=()=>{selectPropertyRegion('');setPlace(PLACES[0]);setQuery('');mapRef.current?.flyTo(PLACES[0],{overviewPanelVisible:true,focused:false});};
  return <div className={`app-shell map-first atlas-shell is-${mapView} ${propertyVisible?'has-property':propertyRequested&&atlas.state==='loading'?'reserves-property':''} ${mode==='map'?'is-exploring':''} ${mode==='live'?'is-live':''} ${focusMode?'is-focused':''} ${menuOpen?'has-layers':''} ${sunOpen?'has-sun':''} ${selection?'has-selection':''} ${mode==='live'&&observationsOpen?'has-observations':''}`}>
    {deploymentBlocked?<div className="map-loading" role="alert">{runtimeError||'공유된 배포 버전을 확인하는 중…'}</div>:<MapErrorBoundary key={mapView}><Suspense fallback={<div className="map-loading">대한민국의 지도를 펼치는 중…</div>}>
      <ActiveMap ref={mapRef} catalog={catalog} layers={layers} boundaries={boundaries} overviewPanelVisible={propertyRequested} focused={focusMode} instant={viewInstant} mode={mode==='replay'?'replay':'sun'} liveTransit={mode==='live'?liveTransit:null} initialPlace={place} initialCamera={spatialCamera} initialFlatCamera={flatCamera} measurement={measurement} onMeasurement={setMeasurement} vectorData={atlas.content} vectorPending={atlas.state==='loading'||atlas.state==='error'&&!!runtime&&'schema_version' in runtime} lightweight={lightweight} onSelect={inspect} onStatus={setMapStatus} onPerformance={setPerformanceInfo} {...(mapView==='2d'?{onPropertyRegion:selectPropertyRegion}:{})}/>
    </Suspense></MapErrorBoundary>}
    <header className="topbar">
      <a className="brand" href="#" onClick={e=>{e.preventDefault();showNational();}} aria-label="대한민국 전체 보기"><span className="brand-symbol" aria-hidden="true">K</span><span className="brand-wordmark"><strong>KOREA REPLAY</strong><small>전국 지도 · 아파트 실거래</small></span></a>
      <div className="search-wrap"><svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 4.5 4.5"/></svg><input ref={searchRef} aria-label="지역 또는 역 검색" role="combobox" aria-autocomplete="list" aria-controls="place-search-results" aria-expanded={!!query} aria-activedescendant={searchActive>=0&&visiblePlaces[searchActive]?`place-result-${searchActive}`:undefined} placeholder="지역·역·장소를 찾아보세요" maxLength={80} value={query} onChange={e=>{setQuery(e.target.value);setSearchActive(-1);setSearchResults([]);setSearchStatus('검색 중…');}} onKeyDown={event=>{if(event.key==='ArrowDown'){event.preventDefault();setSearchActive(index=>Math.min(visiblePlaces.length-1,index+1));}else if(event.key==='ArrowUp'){event.preventDefault();setSearchActive(index=>Math.max(0,index-1));}else if(event.key==='Enter'&&visiblePlaces[searchActive]){event.preventDefault();goTo(visiblePlaces[searchActive]);setSearchActive(-1);}else if(event.key==='Escape'){event.preventDefault();setQuery('');setSearchActive(-1);}}}/><kbd>/</kbd>
      {query&&<div id="place-search-results" className="search-results" role="listbox" aria-label="장소 검색 결과">{visiblePlaces.length?visiblePlaces.map((p,index)=><button id={`place-result-${index}`} key={p.id} role="option" aria-selected={index===searchActive} tabIndex={-1} onClick={()=>goTo(p)}><b>{p.name}</b><span>{p.region}</span></button>):<p role="status">{searchStatus}</p>}</div>}</div>
      <nav className="header-actions" aria-label="서비스 메뉴"><button className="explore-button" aria-pressed={mode==='map'} onClick={()=>{switchMode('map');setPropertyOpen(true);setMenuOpen(false);setSelection(null);}}>지도 탐색</button>
      <div className="city-tools" ref={cityToolsRef}><button className="city-tools-toggle" aria-expanded={cityToolsOpen} aria-controls="city-tools-menu" onClick={()=>setCityToolsOpen(value=>!value)}><span className="city-tools-label">도시 도구</span><svg aria-hidden="true" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="m4 6 4 4 4-4"/></svg></button>
      {cityToolsOpen&&<div className="city-tools-menu" id="city-tools-menu" role="group" aria-label="도시 탐색 도구"><p>도시를 더 자세히 탐색하기</p><button aria-pressed={mode==='live'} onClick={()=>switchMode('live')}><strong>현재 관측</strong><small>연결된 교통·기상 자료</small></button><button aria-pressed={mode==='replay'} onClick={()=>switchMode('replay')}><strong>기록</strong><small>보유한 관측 기록 재생</small></button><button aria-pressed={mode==='sun'} onClick={()=>switchMode('sun')}><strong>햇빛</strong><small>시각에 따른 태양·그림자</small></button>{mode!=='map'&&<div className="city-tools-context">{mode==='live'?<button onClick={()=>{setObservationsOpen(value=>!value);setCityToolsOpen(false);}} aria-expanded={observationsOpen}>관측 패널</button>:<><button onClick={()=>{setTimeOpen(value=>!value);setCityToolsOpen(false);}} aria-expanded={timeOpen}>시간 패널</button><button onClick={()=>{setSunOpen(value=>!value);setCityToolsOpen(false);}} aria-expanded={sunOpen}>태양 정보</button></>}</div>}<div className="city-tools-footer"><button onClick={()=>{setSourcesOpen(true);setCityToolsOpen(false);}}>데이터 출처</button><button aria-disabled={mode==='live'} onClick={()=>{void share();setCityToolsOpen(false);}}>공유</button></div></div>}</div>
      <button className="share-button" aria-disabled={mode==='live'} title={mode==='live'?LIVE_SHARE_NOTICE:undefined} onClick={share}><svg aria-hidden="true" viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.6"><path d="M10 13V2m0 0L6 6m4-4 4 4M4 10v7h12v-7"/></svg>공유</button></nav>
    </header>
    <nav className="map-dock" aria-label="지도 패널" hidden={focusMode}>
      <div className="view-switch" role="group" aria-label="지도 표현"><button aria-pressed={mapView==='2d'} onClick={()=>changeView('2d')} title="빠른 평면 지도">2D</button><button aria-pressed={mapView==='3d'} onClick={()=>changeView('3d')} title="지형과 건물을 입체로 보기">3D</button></div>
      <button className="dock-national" onClick={showNational} title="대한민국 전체로 이동">전국</button>
      {atlas.content&&<button onClick={()=>{if(mode!=='map')switchMode('map');setPropertyOpen(mode==='map'?!propertyVisible:true);setMenuOpen(false);setSelection(null);}} aria-expanded={propertyVisible}>지역 분석</button>}
      {atlas.content&&mapView==='2d'&&<button className="dock-boundaries" onClick={()=>setBoundaries(v=>!v)} aria-pressed={boundaries} title="행정경계 · 2025년 6월 기준">경계</button>}
      <button onClick={()=>setMenuOpen(v=>!v)} aria-expanded={menuOpen} aria-controls="layers-panel">레이어</button>
      {mode==='live'?<button className="dock-context" onClick={()=>{setObservationsOpen(v=>!v);if(!observationsOpen)setSelection(null);}} aria-expanded={observationsOpen} aria-controls="observations-host">관측</button>:mode!=='map'&&<>
        <button className="dock-context" onClick={()=>setTimeOpen(v=>!v)} aria-expanded={timeOpen} aria-controls="time-panel">시간</button>
        <button className="dock-context" onClick={()=>setSunOpen(v=>!v)} aria-expanded={sunOpen} aria-controls="sun-panel">태양</button>
      </>}
    </nav>
    <button className="focus-toggle" aria-pressed={focusMode} aria-label={focusMode?'도구 표시':'지도만 보기'} title="지도 집중 모드 · F / Esc" onClick={()=>{setFocusMode(v=>!v);setQuery('');setCityToolsOpen(false);}}><svg aria-hidden="true" viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6"><path d={focusMode?'M3 7h4V3m6 0v4h4M3 13h4v4m6 0v-4h4':'M7 3H3v4m10-4h4v4M3 13v4h4m6 0h4v-4'}/></svg><span>{focusMode?'도구 표시':'지도만 보기'}</span><kbd>{focusMode?'Esc':'F'}</kbd></button>
    <MapInteractionTools map={mapRef} view={mapView} place={place} hidden={focusMode} measurement={measurement} onMeasure={mode=>setMeasurement(measureMap(mode,[]))} onUndo={()=>setMeasurement(previous=>measureMap(previous.mode,previous.points.slice(0,-1)))} onNotice={setNotice} onLocate={goTo}/>
    {atlas.content&&<Suspense fallback={null}><PropertyExplorer key={atlas.content.property.release_id} atlas={atlas.content} hidden={!propertyVisible} onClose={()=>setPropertyOpen(false)} onLocate={goTo} onViewState={onPropertyView} requestedRegion={requestedRegion}/></Suspense>}
    <aside id="layers-panel" className="layers-panel panel" aria-label="지도 레이어" hidden={focusMode||!menuOpen}>
      <button className="close" aria-label="레이어 패널 닫기" onClick={()=>setMenuOpen(false)}>×</button>
      <div className="eyebrow">전국 지도 설정</div>
      <h2>레이어</h2><p className="place-subtitle">필요한 정보만 지도에 겹쳐 보세요.</p>
      <div className="section-label">지도에 겹쳐 보기</div>
      {atlas.content&&mapView==='2d'&&<label className="layer-row"><span className="layer-mark" aria-hidden="true"/><span className="layer-label">행정경계<small>2025년 6월 기준 · 통계 행정구역</small></span><input type="checkbox" aria-label="행정경계" checked={boundaries} onChange={event=>setBoundaries(event.target.checked)}/><span className="switch" aria-hidden="true"/></label>}
      <div className="layer-list">{catalog.layers.map(layer=>{
        const temporal=['bus','satellite','radar'].includes(layer.id);
        const local=localLayers.has(layer.id);
        const threeDimensional=['buildings','depth','sun'].includes(layer.id);
        const enabled=layer.state!=='unavailable'&&(!temporal||mode==='replay')&&!(mapView==='2d'&&threeDimensional);
        const hint=mapView==='2d'&&threeDimensional?'3D 보기에서 사용':mode==='live'&&temporal?'현재 관측 패널에서 조회':layer.state==='unavailable'?'자료 미연결':!local?'선택 지역 밖에 자료 있음':temporal&&mode!=='replay'?'기록 재생에서 사용':layer.id==='buildings'?'일부 지역 · 추정 높이 포함':layer.id==='depth'?'서울 일부 · 심도 기반 개략 위치':layer.state==='partial'?'일부 지역':layer.id==='sun'?'천문 계산':'자료 연결됨';
        return <label key={layer.id} className={`layer-row ${enabled?'':'unavailable'}`} title={mode==='live'&&temporal?hint:layer.reason??hint}>
          <span className={`layer-mark ${layer.id}`} aria-hidden="true"/><span className="layer-label">{layer.label}<small>{hint}</small></span>
          <input type="checkbox" aria-label={layer.label} checked={enabled&&layers[layer.id]} disabled={!enabled} onChange={e=>setLayers(prev=>({...prev,[layer.id]:e.target.checked}))}/><span className="switch" aria-hidden="true"/>
        </label>;
      })}</div>
      <div className="quality-row" title={mapView==='2d'?'화면 주변의 도로와 시설만 단계적으로 표시합니다.':performanceInfo?`렌더 CPU P95 ${performanceInfo.renderP95Ms.toFixed(1)}ms · 이동 프레임 P95 ${performanceInfo.frameP95Ms.toFixed(1)}ms`:"기기의 렌더링 부하에 맞춰 화질을 조절합니다."}><span>{lightweight?'경량 모드':mapView==='2d'?'2D · 화면 주변 자료만 표시':`자동 화질 · ${{high:'정밀',balanced:'균형',low:'경량'}[performanceInfo?.quality??'balanced']}`}</span><input aria-label="경량 모드" type="checkbox" checked={lightweight} onChange={e=>setLightweight(e.target.checked)}/></div>
      <button className="source-button" onClick={()=>setSourcesOpen(true)}>ⓘ 데이터와 출처 <span>↗</span></button>
      <div className="mobile-actions"><button aria-disabled={mode==='live'} onClick={share}>현재 화면 공유 ↗</button></div>
    </aside>
    <div className="scene-heading" hidden={focusMode||menuOpen||!!atlas.content&&propertyOpen&&mode==='map'}><span className="live-dot"/><h1>{place.name}</h1><span className="coordinate">{place.lat.toFixed(3)}° N · {place.lon.toFixed(3)}° E</span></div>
    {mode!=='live'&&<aside id="sun-panel" className="sun-panel panel" aria-label="태양 정보" hidden={focusMode||!sunOpen}><button className="close" aria-label="태양 패널 닫기" onClick={()=>setSunOpen(false)}>×</button><span className="sun-icon" aria-hidden="true">☀</span><div><span className="eyebrow">선택 지역의 태양</span><strong>{solar.altitude>=0?'낮':'밤'} · {timeLabel}</strong></div><div className="sun-measures"><span>고도<b>{solar.altitude.toFixed(1)}°</b></span><span>방위각<b>{solar.azimuth.toFixed(1)}°</b></span></div></aside>}
    {selection&&<aside className="selection-panel panel" aria-label="선택 정보" hidden={focusMode}>
      <button className="close" aria-label="선택 정보 닫기" onClick={()=>setSelection(null)}>×</button><span className="eyebrow">선택한 장소</span>
      <h2>{selection.name}</h2><p>{selection.detail}</p>
      {selection.height!==undefined&&<dl><dt>표현 높이</dt><dd>{selection.height.toFixed(selection.height<1?2:1)} m</dd></dl>}
      {selection.qualityState&&selection.height===undefined&&<p>높이 검토가 필요해 외곽선으로 표시합니다.</p>}
      {selection.rawHeight!==undefined&&<dl><dt>원천 높이</dt><dd>{selection.rawHeight===null?'개별 높이 미제공':`${selection.rawHeight.toFixed(selection.rawHeight<1?2:1)} m`}</dd></dl>}
      {selection.qualityFlags?.slice(0,2).map(flag=><p key={flag}>{QUALITY_NOTICES[flag]??'원천값의 추가 검토가 필요합니다.'}</p>)}
      {selection.provenance&&<><span className="evidence-badge">{EVIDENCE_LABEL[selection.provenance.evidence_type??'unverified']}</span><dl>
        <dt>자료 버전</dt><dd>{selection.provenance.dataset_version??'미확인'}</dd>
        <dt>출처</dt><dd>{SOURCES.find(s=>s.id===selection.provenance?.source_id)?.title??selection.provenance.source_id}</dd>
        {(selection.provenance.source_record_id||selection.sourceId)&&<><dt>원본 ID</dt><dd>{selection.provenance.source_record_id||selection.sourceId}</dd></>}
      </dl></>}
    </aside>}
    <div className="map-tools" hidden={focusMode||mapView==='2d'}><button aria-label="북쪽으로 정렬" onClick={()=>mapRef.current?.north()}>N ↑</button><button aria-label="위에서 보기" onClick={()=>mapRef.current?.overhead()}>⌑</button><button aria-label="선택 지역으로 돌아가기" onClick={()=>mapRef.current?.flyTo(place)}>⌖</button></div>
    {mapView==='2d'&&<details className="map-legend" hidden={focusMode}><summary>도로 구분</summary><span><i className="road-motorway"/>고속도로 등급</span><span><i className="road-trunk"/>주요 간선</span><span><i className="road-primary"/>주요 도로</span><span><i className="road-local"/>일반 도로</span><small>OSM 도로 등급 기준 · 법정 국도 구분은 미연결</small></details>}
    {(notice||catalogError||atlas.error||runtimeError)&&<div className="notice" role="status"><span>{runtimeError||catalogError||atlas.error||notice}</span>{(runtimeError||atlas.error)?<button onClick={()=>location.reload()}>다시 연결</button>:<button aria-label="알림 닫기" onClick={()=>{setNotice('');setCatalogError('');}}>×</button>}</div>}
    {mode==='live'?<div id="observations-host" hidden={focusMode||!observationsOpen}><CurrentObservations onTransit={acceptLiveTransit} onLocate={goTo} onClose={()=>setObservationsOpen(false)}/></div>:<section id="time-panel" className={`timeline panel ${timeExpanded?'is-expanded':''}`} aria-label="시간 탐색" hidden={focusMode||!timeOpen}>
      <div className="timeline-summary">
        <button className="play" disabled={mode==='replay'&&!recordedDay} aria-label={playing?'일시정지':'재생'} onClick={()=>{if(!playing&&replayTo!==null&&instant>=replayTo&&recordedDay)setInstant(recordedDay.from);setPlaying(v=>!v);}}>{playing?'Ⅱ':'▶'}</button>
        <button className="timeline-clock" aria-label="날짜·시간 설정" aria-expanded={timeExpanded} aria-controls="time-details" onClick={()=>setTimeExpanded(v=>!v)}><span>{timeLabel}</span><small>{date} · KST</small></button>
        <div className="time-track"><input aria-label="하루 중 시각" type="range" min={minSeconds} max={maxSeconds} step={1} value={seconds} disabled={mode==='replay'&&!recordedDay} onChange={e=>{setInstant(kstInstant(date,Math.min(maxSeconds,Math.max(minSeconds,Number(e.target.value)))));setPlaying(false);}}/></div>
        <button className="timeline-expand" aria-label={timeExpanded?'시간 설정 접기':'시간 설정 펼치기'} aria-expanded={timeExpanded} aria-controls="time-details" onClick={()=>setTimeExpanded(v=>!v)}>설정 {timeExpanded?'▴':'▾'}</button>
        <button className="timeline-close" aria-label="시간 패널 닫기" onClick={()=>setTimeOpen(false)}>×</button>
      </div>
      <div id="time-details" className="timeline-details" hidden={!timeExpanded}>
        <div className="date-control"><span className="eyebrow">{mode==='sun'?'햇빛 실험 날짜':'보유한 관측 기록'}</span>{mode==='sun'?<input aria-label="날짜" type="date" value={date} min="1900-01-01" max="2100-12-31" onChange={e=>{try{setInstant(kstInstant(e.target.value,seconds));setPlaying(false);}catch{/* Partial native date input. */}}}/>:<select aria-label="관측 기록 날짜" value={recordedDay?.date??''} onChange={e=>selectRecordedDay(e.target.value)}>{!recordedDay&&<option value="">이 날짜의 기록 없음</option>}{days.map(d=><option key={d.date} value={d.date}>{d.date} · {d.layers.map(id=>catalog.layers.find(l=>l.id===id)?.label).join(' / ')}</option>)}</select>}</div>
        <label className="speed-control">재생 배속<select aria-label="재생 배속" value={speed} onChange={e=>setSpeed(Number(e.target.value))}>{[1,10,60,300,900].map(v=><option key={v} value={v}>{v}×</option>)}</select></label>
        <div className="hour-labels">{timeTicks.map((t,i)=><span key={i}>{t}</span>)}</div>
        <div className="timeline-footer"><span>{mode==='sun'?'좌표·시각에 따른 태양 계산':recordedDay?.labels.join(' · ')||'보유한 기록만 재생'}</span><span>건물 기준 {catalog.base_date??'적재 대기'}</span></div>
      </div>
    </section>}
    <footer className="attribution"><span>{mapStatus}</span><span>© OSM · Overture · Mapzen · Natural Earth · EC/JRC · 서울교통공사 · 기상청</span></footer>
    {sourcesOpen&&<div className="modal-backdrop" onClick={()=>setSourcesOpen(false)}><section ref={dialogRef} className="sources-modal" role="dialog" aria-modal="true" aria-labelledby="sources-title" onClick={e=>e.stopPropagation()}><button className="close" aria-label="출처 닫기" onClick={()=>setSourcesOpen(false)}>×</button><span className="eyebrow">SOURCES & COVERAGE</span><h2 id="sources-title">자료가 말해 주는 범위</h2><p>관측·공식 기록과 계산한 표현을 구분합니다. 연결되지 않은 자료는 움직이는 모형으로 대신하지 않습니다.</p><div className="coverage-summary">공개 버전 <code>{catalog.release_id}</code><br/>연결된 파일 {catalog.assets.length.toLocaleString()}개 · {catalog.generated_at?new Date(catalog.generated_at).toLocaleString('ko-KR'):'최초 적재 전'}</div>{SOURCES.map(source=><article key={source.id}><a href={source.url} target="_blank" rel="noreferrer">{source.title} ↗</a><p>{source.description}</p><small>{source.license}</small></article>)}</section></div>}
  </div>;
}
