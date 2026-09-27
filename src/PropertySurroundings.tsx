import {useEffect,useRef,useState} from 'react';
import type {PropertyComplex} from '../shared/property';
import {DEFAULT_SURROUNDINGS_FILTER,DEVELOPMENT_STAGES,SURROUNDINGS_CATEGORIES,SURROUNDINGS_RADII,SURROUNDINGS_TYPES,surroundingsFilterChange,surroundingsRadiusLabel,surroundingsSourceUrl,surroundingsView,type SurroundingsCategory,type SurroundingsFilterAction,type SurroundingsRadius,type SurroundingsSourceState,type SurroundingsType} from '../shared/property-surroundings';
import PropertyMapLinks from './PropertyMapLinks';
import {loadPropertyPoi} from './property-poi-loader';
import {validPoiCenter,type PoiCenter} from '../shared/property-poi';
import type {Place} from '../shared/contracts';

interface Props {complex:PropertyComplex;region:string;releaseId:string;active?:boolean;sources?:Partial<Record<SurroundingsCategory,SurroundingsSourceState>>;onRetry?:(category:SurroundingsCategory)=>void;onUseMapCenter?:()=>PoiCenter|null;onLocate?:(place:Place)=>void;}
/** The keyed child resets local filters when either the complex or immutable release changes. */
export default function PropertySurroundings(props:Props){return <SurroundingsContent key={`${props.releaseId}:${props.complex.id}`} {...props}/>;}
function SurroundingsContent({complex,region,releaseId,active=false,sources,onRetry,onUseMapCenter,onLocate}:Props){
  const [filter,setFilter]=useState(DEFAULT_SURROUNDINGS_FILTER);
  const [manualCenter,setManualCenter]=useState<PoiCenter|null>(null),[centerError,setCenterError]=useState(''),[attempt,setAttempt]=useState(0),[limit,setLimit]=useState(20);
  const listHeading=useRef<HTMLHeadingElement>(null);
  const center=manualCenter??complex.position;
  const queryKey=center?`${center.longitude}:${center.latitude}:${attempt}`:'';
  const [loaded,setLoaded]=useState<{key:string;sources?:Props['sources'];error?:string}|null>(null);
  useEffect(()=>{
    if(!active||!center||sources||loaded?.key===queryKey)return;
    const controller=new AbortController();
    void loadPropertyPoi(center,{complexId:complex.id,releaseId},controller.signal).then(result=>{if(!controller.signal.aborted)setLoaded({key:queryKey,sources:result});},error=>{if(!controller.signal.aborted)setLoaded({key:queryKey,error:error instanceof Error?error.message:'시설 자료를 불러오지 못했습니다.'});});
    return()=>controller.abort();
  },[active,center,complex.id,releaseId,queryKey,sources,loaded]);
  const change=(action:SurroundingsFilterAction)=>{setFilter(current=>surroundingsFilterChange(current,action));setLimit(20);};
  const generated=filter.category==='development'||!center?undefined:loaded?.key===queryKey?(loaded.error?{status:'error' as const,scope:{complexId:complex.id,releaseId}}:loaded.sources?.[filter.category]):{status:'loading' as const,scope:{complexId:complex.id,releaseId}};
  const view=surroundingsView(sources?.[filter.category]??generated,{complexId:complex.id,releaseId},filter);
  const pageStart=Math.min(Math.max(0,limit-20),Math.max(0,Math.ceil(view.records.length/20)-1)*20),pageEnd=pageStart+20;
  const turnPage=(offset:number)=>{setLimit(Math.max(20,pageEnd+offset*20));listHeading.current?.scrollIntoView({block:'start'});listHeading.current?.focus({preventScroll:true});};
  const category=SURROUNDINGS_CATEGORIES.find(([id])=>id===filter.category)![1],type=filter.type==='all'?'전체':SURROUNDINGS_TYPES[filter.category].find(([id])=>id===filter.type)![1];
  const locateReference=(point:PoiCenter)=>onLocate?.({id:'poi:search-reference',name:'주변 탐색 기준점',region,lon:point.longitude,lat:point.latitude,range:filter.radius*3});
  const stateLabel={unconnected:'자료 연결 전',loading:'자료 불러오는 중',error:'자료를 불러오지 못했습니다',empty:'조건에 맞는 자료가 없습니다',ready:'확인된 자료',partial:'일부 자료 확인'}[view.state];
  const sourceUrl=view.source?surroundingsSourceUrl(view.source.url):undefined;
  return <section className="property-surroundings" aria-label={`${complex.name} 주변 정보`}>
    <header><div><span className="surroundings-eyebrow">주변 둘러보기</span><h3>{complex.name}</h3></div><span className="surroundings-radius-badge">반경 {surroundingsRadiusLabel(filter.radius)}</span></header>
    {onUseMapCenter&&<div className="surroundings-reference"><p>{manualCenter?'선택한 지도 기준점 · 단지 위치와 다를 수 있습니다':complex.position?'검증된 단지 위치 기준':'단지 좌표 확인 전 · 지도에서 기준 위치를 정해 주세요'}</p><button onClick={()=>{const point=onUseMapCenter();if(point&&validPoiCenter(point)){setManualCenter({longitude:point.longitude,latitude:point.latitude});setCenterError('');setLimit(20);}else setCenterError('지도가 준비되면 다시 선택해 주세요.');}}>현재 지도 중심으로 보기</button>{manualCenter&&onLocate&&<button onClick={()=>locateReference(manualCenter)}>기준점 보기</button>}{manualCenter&&complex.position&&<button onClick={()=>{setManualCenter(null);setLimit(20);}}>단지 위치로 복원</button>}{centerError&&<span role="alert">{centerError}</span>}</div>}
    <nav className="surroundings-categories" aria-label="주변 정보 종류">{SURROUNDINGS_CATEGORIES.map(([id,label])=><button key={id} aria-pressed={filter.category===id} onClick={()=>change({category:id})}>{label}</button>)}</nav>
    <div className="surroundings-distance" role="group" aria-label="주변 검색 반경">{SURROUNDINGS_RADII.map(radius=><button key={radius} aria-pressed={radius===filter.radius} onClick={()=>change({radius:radius as SurroundingsRadius})}>{surroundingsRadiusLabel(radius)}</button>)}</div>
    <div className="surroundings-filters"><label>유형<select value={filter.type} onChange={event=>change({type:event.target.value as SurroundingsType|'all'})}><option value="all">전체</option>{SURROUNDINGS_TYPES[filter.category].filter(([id])=>id!=='road').map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><label>정렬<select value={filter.sort} onChange={event=>change({sort:event.target.value as 'distance'|'name'})}><option value="distance">가까운순</option><option value="name">이름순</option></select></label></div>
    <div className="surroundings-list-heading"><h4 ref={listHeading} tabIndex={-1}>{category} 목록</h4><span>{view.count===null?(view.state==='partial'&&view.records.length>0?`지도기록 ${view.records.length}개`:'자료 확인 전'):`${view.count}곳`}</span></div>
    <p className="surroundings-conditions">반경 {surroundingsRadiusLabel(filter.radius)} · {type} · {filter.sort==='distance'?'가까운순':'이름순'}</p>
    {view.state!=='ready'&&<div className={`surroundings-state is-${view.state}`} role={view.state==='error'?'alert':'status'}><span className="surroundings-state-symbol" aria-hidden="true">{view.state==='error'?'!':view.state==='loading'?'…':'◎'}</span><strong>{stateLabel}</strong>{view.state==='partial'&&<small>{view.records.length?'공개지도 수록 시설 · 누락될 수 있습니다.':'이 범위의 공개지도 수록 시설이 없습니다. 실제 시설이 없다는 뜻은 아닙니다.'}</small>}{view.state==='error'&&<><small>{loaded?.key===queryKey?loaded.error:undefined}</small><button onClick={()=>onRetry?onRetry(filter.category):setAttempt(value=>value+1)}>다시 불러오기</button></>}</div>}
    {view.records.length>0&&<ul className="surroundings-results">{view.records.slice(pageStart,pageEnd).map(row=><li key={row.id}><div><strong>{row.name}</strong><span>{SURROUNDINGS_TYPES[filter.category].find(([id])=>id===row.type)?.[1]}</span></div>{row.address&&<p>{row.address}</p>}<small>직선 {Math.round(row.distanceMeters!).toLocaleString()}m{row.position?.method==='area_representative_point'?' · 시설 영역 대표점 기준':row.position?.method==='line_midpoint'?' · 시설 선형 대표점 기준':''}{row.development?` · ${DEVELOPMENT_STAGES[row.development.stage]} · ${row.development.effectiveDate}`:''}</small><div className="surroundings-row-actions">{row.position&&onLocate&&<button onClick={()=>onLocate({id:`poi:${row.id}`,name:row.name,region,lon:row.position!.longitude,lat:row.position!.latitude,range:800})}>지도에서 보기</button>}{surroundingsSourceUrl(row.source.url)&&<a href={surroundingsSourceUrl(row.source.url)} target="_blank" rel="noopener noreferrer">원본 정보 ↗</a>}{row.development&&surroundingsSourceUrl(row.development.documentUrl)&&<a href={surroundingsSourceUrl(row.development.documentUrl)} target="_blank" rel="noopener noreferrer">계획 원문 ↗</a>}</div></li>)}</ul>}
    {view.records.length>20&&<nav className="surroundings-pages" aria-label="시설 목록 페이지"><button disabled={pageStart===0} onClick={()=>turnPage(-1)}>이전 시설</button><span aria-live="polite">{pageStart+1}–{Math.min(pageEnd,view.records.length)} / {view.records.length}</span><button disabled={pageEnd>=view.records.length} onClick={()=>turnPage(1)}>다음 시설</button></nav>}
    {view.unknownDistances>0&&<p className="surroundings-source">거리 미확인 {view.unknownDistances}곳은 반경 목록에서 제외했습니다.</p>}
    {view.source&&<p className="surroundings-source">{sourceUrl?<a href={sourceUrl} target="_blank" rel="noopener noreferrer">{view.source.label} ↗</a>:view.source.label} · {view.source.asOf.slice(0,10)} 기준 · 직선거리{view.source.id.startsWith('osm')?' · ODbL · 보행 경로·통학구역과 다릅니다.':''}</p>}
    <PropertyMapLinks complex={complex} region={region}/>
  </section>;
}
