import type {PropertyMapPoint} from '../shared/property-map-point';
import {lazy,Suspense,useCallback,useEffect,useRef,useState} from 'react';
import type {Catalog,LayerId,Place} from '../shared/contracts';
import {EMPTY_CATALOG,PLACES,SOURCES} from '../shared/sources';
import type {MapHandle} from './map-handle';
import MapErrorBoundary from './MapErrorBoundary';
import {fetchPublicCatalog} from './catalog';
import type {MapSelection} from '../shared/selection';
import {assertPinnedDeployment,validateRuntime,versionedShareUrl,type RuntimeVersion} from '../shared/share';
import MapInteractionTools from './MapInteractionTools';
import {EMPTY_MEASUREMENT,measureMap} from '../shared/map-tools';
import {readFlatCamera} from '../shared/map2d';
import {assertPinnedDeploymentV2,validateRuntimeV2,versionedShareUrlV2,type RuntimeV2} from '../shared/runtime-v2';
import {readApartmentEntry} from '../shared/apartment-entry';
import {useAtlas} from './useAtlas';
import {regionNavigationPlace} from './region-navigation';
import {PROPERTY_BRAND} from './property-brand';
import type {PropertyViewState} from './PropertyExplorer';
const Map2D=lazy(()=>import('./Map2D'));
const PropertyExplorer=lazy(()=>import('./PropertyExplorer'));
const layers:Record<LayerId,boolean>={terrain:true,buildings:true,infrastructure:true,rail:true,bus:false,depth:false,satellite:false,radar:false,sun:false};
export type Selection=MapSelection;
export default function App(){
  const [initial]=useState(()=>readApartmentEntry(location.href));
  const [catalog,setCatalog]=useState<Catalog>(EMPTY_CATALOG);
  const [catalogError,setCatalogError]=useState('');
  const [place,setPlace]=useState<Place>(initial.place);
  const [notice,setNotice]=useState(initial.retired?'3D·도시 재생 기능이 종료되었습니다. 2D 아파트 지도로 열었습니다.':'');
  const [mapStatus,setMapStatus]=useState('지도 불러오는 중');
  const [runtime,setRuntime]=useState<RuntimeVersion|RuntimeV2|null>(null);
  const [runtimeChecked,setRuntimeChecked]=useState(false);
  const [runtimeError,setRuntimeError]=useState('');
  const atlas=useAtlas(runtime&&'schema_version' in runtime?runtime:null,runtimeChecked,runtimeError);
  const [propertyOpen,setPropertyOpen]=useState(true);
  const [focusMode,setFocusMode]=useState(false);
  const [requestedMarker,setRequestedMarker]=useState<{value:NonNullable<PropertyViewState['markerDisplay']>;request:number}>();
  const requestMarker=useCallback((value:NonNullable<PropertyViewState['markerDisplay']>)=>setRequestedMarker(previous=>({value,request:(previous?.request??0)+1})),[]);
  const [propertyMapPoint,setPropertyMapPoint]=useState<PropertyMapPoint|null>(null);
  const [requestedRegion,setRequestedRegion]=useState<{code:string;request:number;complexId?:string;skipLocate?:boolean;province?:string}>();
  const selectPropertyRegion=useCallback((code:string)=>{setPropertyOpen(true);setFocusMode(false);setRequestedRegion(previous=>({code,request:(previous?.request??0)+1}));},[]);
  const selectPropertyComplex=useCallback((code:string,complexId:string)=>{
    if(!/^\d{5}$/.test(code)||!new RegExp(`^molit-apt:${code}:[A-Za-z0-9_-]{1,64}$`).test(complexId))return;
    setPropertyOpen(true);setFocusMode(false);setRequestedRegion(previous=>({code,complexId,skipLocate:true,request:(previous?.request??0)+1}));
  },[]);
  const [boundaries]=useState(()=>new URLSearchParams(location.hash.slice(1)).get('boundaries')!=='off');
  const propertyViewRef=useRef<PropertyViewState|null>(null);
  const [propertyMapView,setPropertyMapView]=useState<PropertyViewState|null>(null);
  const [propertyRegionName,setPropertyRegionName]=useState('');
  const selectPropertyProvince=useCallback((province:string)=>{setPropertyOpen(true);setFocusMode(false);setRequestedRegion(previous=>({code:'',province,request:(previous?.request??0)+1}));},[]);
  const onPropertyView=useCallback((value:PropertyViewState)=>{propertyViewRef.current=value;setPropertyMapView(previous=>JSON.stringify(previous)===JSON.stringify(value)?previous:value);},[]);
  const [measurement,setMeasurement]=useState(EMPTY_MEASUREMENT);
  const [flatCamera]=useState(()=>readFlatCamera(location.hash));
  const mapRef=useRef<MapHandle|null>(null);
  const propertyFrameDone=useRef(false);
  const [sourcesOpen,setSourcesOpen]=useState(false);
  const dialogRef=useRef<HTMLElement|null>(null);
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
  useEffect(()=>{
    const controller=new AbortController();
    void fetchPublicCatalog(initial.release,controller.signal).then(setCatalog).catch(error=>{if(!controller.signal.aborted)setCatalogError(error.message);});
    return()=>controller.abort();
  },[initial.release]);
  useEffect(()=>{
    document.title=`${PROPERTY_BRAND.name} · 아파트 실거래 지도`;
    const shortcut=(event:KeyboardEvent)=>{
      if(sourcesOpen||event.ctrlKey||event.metaKey||event.altKey)return;
      const editing=event.target instanceof HTMLInputElement||event.target instanceof HTMLTextAreaElement||event.target instanceof HTMLSelectElement||(event.target as HTMLElement)?.isContentEditable;
      if(event.key==='/'&&!editing){event.preventDefault();setFocusMode(false);setPropertyOpen(true);requestAnimationFrame(()=>document.querySelector<HTMLInputElement>('.property-search input')?.focus());}
      if(event.key.toLowerCase()==='f'&&!editing){event.preventDefault();setFocusMode(value=>!value);}
      if(event.key==='Escape')setFocusMode(false);
    };
    document.addEventListener('keydown',shortcut);return()=>document.removeEventListener('keydown',shortcut);
  },[sourcesOpen]);
  useEffect(()=>{
    if(!sourcesOpen)return;
    const before=document.activeElement as HTMLElement|null,dialog=dialogRef.current;
    dialog?.querySelector<HTMLButtonElement>('button')?.focus();
    const keydown=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){setSourcesOpen(false);return;}
      if(event.key!=='Tab'||!dialog)return;
      const nodes=dialog.querySelectorAll<HTMLElement>('button,a[href],input,select,[tabindex="0"]'),first=nodes[0],last=nodes[nodes.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
    };
    document.addEventListener('keydown',keydown);return()=>{document.removeEventListener('keydown',keydown);before?.focus();};
  },[sourcesOpen]);
  const propertyRequested=!focusMode&&propertyOpen;
  const propertyVisible=!!atlas.content&&propertyRequested;
  const goTo=useCallback((target:Place)=>{setPlace(target);mapRef.current?.flyTo(target,{overviewPanelVisible:!focusMode&&propertyOpen,focused:focusMode});},[focusMode,propertyOpen]);
  useEffect(()=>{
    if(propertyFrameDone.current||!atlas.content)return;propertyFrameDone.current=true;
    const params=new URLSearchParams(location.hash.slice(1));
    if(params.has('flatCamera')||params.has('position'))return;
    const region=atlas.content.regions.regions.find(row=>row.lawd_code===params.get('regionCode'));
    const target=regionNavigationPlace(region??{name:params.get('regionQuery')??''},atlas.content.map);if(target)goTo(target);
  },[atlas.content,goTo]);
  const inspect=useCallback(()=>{},[]);
  const instant=initial.time;
  const share=async()=>{
    if(catalogError){setNotice('공유할 지도 자료가 정상적으로 연결되지 않았습니다.');return;}
    if(!runtime){setNotice(runtimeError||'배포 버전을 확인하고 있습니다. 잠시 뒤 공유해 주세요.');return;}
    const params=new URLSearchParams({place:place.id,time:new Date(instant).toISOString(),mode:'map'});
    params.set('position',[place.lon,place.lat,place.range].join(','));params.set('name',place.name);params.set('region',place.region);
    params.set('layers',Object.entries(layers).filter(([,v])=>v).map(([k])=>k).join(','));
    params.set('release',catalog.release_id);
    params.set('view','2d');
    params.set('boundaries',boundaries?'on':'off');
    const flatCamera=mapRef.current?.flatCamera?.();if(flatCamera)params.set('flatCamera',flatCamera.join(','));
    const propertyView=propertyViewRef.current;
    if(propertyView&&atlas.content){params.set('propertyRelease',atlas.content.property.release_id);if(propertyView.propertyType==='officetel')params.set('propertyType','officetel');else params.delete('propertyType');params.set('regionCode',propertyView.region);if(propertyView.legalDong)params.set('legalDong',propertyView.legalDong);else params.delete('legalDong');if(propertyView.regionQuery)params.set('regionQuery',propertyView.regionQuery);else params.delete('regionQuery');params.set('trade',propertyView.trade);if(propertyView.trade==='rent'&&propertyView.rentKind&&propertyView.rentKind!=='all')params.set('rentKind',propertyView.rentKind);else params.delete('rentKind');params.set('month',propertyView.month);params.set('complex',propertyView.complex);params.set('area',propertyView.area);params.set('historyMonths',String(propertyView.historyMonths));params.set('compareRegions',propertyView.compare.join(','));params.set('compareComplexes',propertyView.compareComplexes.join(','));if(propertyView.includeReview)params.set('review','include');else params.delete('review');for(const key of ['markerDisplay','priceBasis','areaBasis','detailSection','latestPriceMinEok','latestPriceMaxEok'] as const){const value=propertyView[key];if(value)params.set(key,value);}}
    let url:string;try{url='schema_version' in runtime?versionedShareUrlV2(runtime,catalog.release_id,params):versionedShareUrl(runtime,catalog.release_id,params);}catch(error){setNotice(error instanceof Error?error.message:'공유 링크를 만들지 못했습니다.');return;}
    try{await navigator.clipboard.writeText(url);setNotice('현재 지도와 조건의 링크를 복사했습니다.');}
    catch{setNotice(`링크 복사가 허용되지 않았습니다. 공유 주소: ${url}`);}
  };
  const deploymentBlocked=initial.deployment!==null&&(!runtimeChecked||!!runtimeError);
  const showNational=()=>{selectPropertyRegion('');goTo(PLACES[0]);};
  return <div className={`app-shell map-first atlas-shell is-2d is-exploring apartment-only ${propertyVisible?'has-property':propertyRequested&&atlas.state==='loading'?'reserves-property':''} ${focusMode?'is-focused':''}`}>
    {deploymentBlocked?<div className="map-loading" role="alert">{runtimeError||'공유된 배포 버전 확인 중'}</div>:<MapErrorBoundary><Suspense fallback={<div className="map-loading">지도 불러오는 중</div>}>
      <Map2D ref={mapRef} catalog={catalog} layers={layers} boundaries={boundaries} overviewPanelVisible={propertyRequested} focused={focusMode} initialPlace={place} initialFlatCamera={flatCamera} measurement={measurement} onMeasurement={setMeasurement} vectorData={atlas.content} vectorPending={atlas.state==='loading'||atlas.state==='error'&&!!runtime&&'schema_version' in runtime} lightweight={false} onSelect={inspect} onStatus={setMapStatus} inspectFeatures={false} onPropertyRegion={selectPropertyRegion} onPropertyComplex={selectPropertyComplex} propertyView={propertyMapView} onMarkerDisplay={requestMarker} propertyTrade={propertyMapView?.trade??'sale'} propertyMapPoint={propertyMapPoint} propertyRegion={propertyMapView?.region??initial.region} propertyRegionName={propertyRegionName} onPropertyProvince={selectPropertyProvince}/>
    </Suspense></MapErrorBoundary>}
    <header className="topbar">
      <a className="brand" href="#" onClick={event=>{event.preventDefault();showNational();}} aria-label="아파트 지도 처음으로"><span className="brand-symbol" aria-hidden="true"><svg viewBox="0 0 40 40" focusable="false"><path d="M5 32V16l10-6v22M15 32V7l12 5v20M27 19l8-4v17M3 33h34M9 19v2m0 5v2m11-15v3m0 5v3m0 4v2m12-10v3m0 4v2" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round"/></svg></span><span className="brand-wordmark"><strong>{PROPERTY_BRAND.name}</strong></span></a>
      <div id="property-header-tools"/>
      <nav className="header-actions" aria-label="서비스 메뉴"><button onClick={()=>setSourcesOpen(true)}>출처</button><button className="share-button" onClick={share}>공유</button></nav>
    </header>
    {!propertyVisible&&!focusMode&&<button className="property-reopen" onClick={()=>setPropertyOpen(true)}>아파트 목록</button>}
    <button className="focus-toggle" aria-pressed={focusMode} aria-label={focusMode?'도구 표시':'지도만 보기'} title="F / Esc" onClick={()=>setFocusMode(value=>!value)}><span>{focusMode?'도구 표시':'지도만 보기'}</span></button>
    <MapInteractionTools map={mapRef} view="2d" place={place} hidden={focusMode} measurement={measurement} onMeasure={mode=>setMeasurement(measureMap(mode,[]))} onUndo={()=>setMeasurement(previous=>measureMap(previous.mode,previous.points.slice(0,-1)))} onNotice={setNotice} onLocate={goTo}/>
    {atlas.content&&<Suspense fallback={null}><PropertyExplorer mapLayout key={atlas.content.property.release_id} atlas={atlas.content} hidden={!propertyVisible} onOpen={()=>{setFocusMode(false);setPropertyOpen(true);}} onClose={()=>setPropertyOpen(false)} onLocate={goTo} onUseMapCenter={()=>{const point=mapRef.current?.viewport?.();return point?{longitude:point.lon,latitude:point.lat}:null;}} onViewState={onPropertyView} onMapRegionName={setPropertyRegionName} onMapPoint={setPropertyMapPoint} requestedRegion={requestedRegion} requestedMarker={requestedMarker}/></Suspense>}
    {atlas.state==='error'&&<div className="notice" role="alert"><span>{atlas.error}</span><button onClick={()=>location.reload()}>다시 시도</button></div>}
    {notice&&<div className="notice" role="status"><span>{notice}</span><button aria-label="안내 닫기" onClick={()=>setNotice('')}>×</button></div>}
    <footer className="attribution"><span>{mapStatus}</span><span>© OSM · Overture · Natural Earth · SGIS</span></footer>
    {sourcesOpen&&<div className="modal-backdrop" onClick={()=>setSourcesOpen(false)}><section ref={dialogRef} className="sources-modal" role="dialog" aria-modal="true" aria-labelledby="sources-title" onClick={event=>event.stopPropagation()}><button className="close" aria-label="출처 닫기" onClick={()=>setSourcesOpen(false)}>×</button><h2 id="sources-title">데이터 출처</h2><p>거래 기준일과 원문은 단지 상세에서 확인할 수 있습니다.</p>{SOURCES.filter(source=>['osm','overture','natural-earth','sgis','molit','k-apt'].includes(source.id)).map(source=><article key={source.id}><a href={source.url} target="_blank" rel="noreferrer">{source.title} ↗</a><p>{source.description}</p><small>{source.license}</small></article>)}</section></div>}
  </div>;
}
