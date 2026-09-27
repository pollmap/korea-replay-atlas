import {useState} from 'react';
import type {PropertyComplex} from '../shared/property';
import {DEFAULT_SURROUNDINGS_FILTER,DEVELOPMENT_STAGES,SURROUNDINGS_CATEGORIES,SURROUNDINGS_RADII,SURROUNDINGS_TYPES,surroundingsFilterChange,surroundingsRadiusLabel,surroundingsSourceUrl,surroundingsView,type SurroundingsCategory,type SurroundingsFilterAction,type SurroundingsRadius,type SurroundingsSourceState,type SurroundingsType} from '../shared/property-surroundings';
import PropertyMapLinks from './PropertyMapLinks';

interface Props {complex:PropertyComplex;region:string;releaseId:string;sources?:Partial<Record<SurroundingsCategory,SurroundingsSourceState>>;onRetry?:(category:SurroundingsCategory)=>void;}
/** The keyed child resets local filters when either the complex or immutable release changes. */
export default function PropertySurroundings(props:Props){return <SurroundingsContent key={`${props.releaseId}:${props.complex.id}`} {...props}/>;}
function SurroundingsContent({complex,region,releaseId,sources,onRetry}:Props){
  const [filter,setFilter]=useState(DEFAULT_SURROUNDINGS_FILTER);
  const change=(action:SurroundingsFilterAction)=>setFilter(current=>surroundingsFilterChange(current,action));
  const view=surroundingsView(sources?.[filter.category],{complexId:complex.id,releaseId},filter);
  const category=SURROUNDINGS_CATEGORIES.find(([id])=>id===filter.category)![1],type=filter.type==='all'?'전체':SURROUNDINGS_TYPES[filter.category].find(([id])=>id===filter.type)![1];
  const stateLabel={unconnected:'자료 연결 전',loading:'자료 불러오는 중',error:'자료를 불러오지 못했습니다',empty:'조건에 맞는 자료가 없습니다',ready:'확인된 자료',partial:'일부 자료 확인'}[view.state];
  const sourceUrl=view.source?surroundingsSourceUrl(view.source.url):undefined;
  return <section className="property-surroundings" aria-label={`${complex.name} 주변 정보`}>
    <header><div><span className="surroundings-eyebrow">주변 둘러보기</span><h3>{complex.name}</h3></div><span className="surroundings-radius-badge">반경 {surroundingsRadiusLabel(filter.radius)}</span></header>
    <nav className="surroundings-categories" aria-label="주변 정보 종류">{SURROUNDINGS_CATEGORIES.map(([id,label])=><button key={id} aria-pressed={filter.category===id} onClick={()=>change({category:id})}>{label}</button>)}</nav>
    <div className="surroundings-distance" role="group" aria-label="주변 검색 반경">{SURROUNDINGS_RADII.map(radius=><button key={radius} aria-pressed={radius===filter.radius} onClick={()=>change({radius:radius as SurroundingsRadius})}>{surroundingsRadiusLabel(radius)}</button>)}</div>
    <div className="surroundings-filters"><label>유형<select value={filter.type} onChange={event=>change({type:event.target.value as SurroundingsType|'all'})}><option value="all">전체</option>{SURROUNDINGS_TYPES[filter.category].map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><label>정렬<select value={filter.sort} onChange={event=>change({sort:event.target.value as 'distance'|'name'})}><option value="distance">가까운순</option><option value="name">이름순</option></select></label></div>
    <div className="surroundings-list-heading"><h4>{category} 목록</h4><span>{view.count===null?(view.state==='partial'&&view.records.length>0?`확인 ${view.records.length}곳 · 전체 미확인`:'자료 확인 전'):`${view.count}곳`}</span></div>
    <p className="surroundings-conditions">반경 {surroundingsRadiusLabel(filter.radius)} · {type} · {filter.sort==='distance'?'가까운순':'이름순'}</p>
    {view.state!=='ready'&&<div className={`surroundings-state is-${view.state}`} role={view.state==='error'?'alert':'status'}><span className="surroundings-state-symbol" aria-hidden="true">{view.state==='error'?'!':view.state==='loading'?'…':'◎'}</span><strong>{stateLabel}</strong>{view.state==='partial'&&<small>선택 범위의 전체 시설 수는 확인되지 않았습니다.</small>}{view.state==='error'&&onRetry&&<button onClick={()=>onRetry(filter.category)}>다시 불러오기</button>}</div>}
    {view.records.length>0&&<ul className="surroundings-results">{view.records.map(row=><li key={row.id}><div><strong>{row.name}</strong><span>{SURROUNDINGS_TYPES[filter.category].find(([id])=>id===row.type)?.[1]}</span></div><p>{row.address??'주소 미제공'}</p><small>직선 {Math.round(row.distanceMeters!).toLocaleString()}m{row.development?` · ${DEVELOPMENT_STAGES[row.development.stage]} · ${row.development.effectiveDate}`:''}</small>{surroundingsSourceUrl(row.source.url)&&<a href={surroundingsSourceUrl(row.source.url)} target="_blank" rel="noopener noreferrer">{row.source.label} · {row.source.asOf} ↗</a>}{row.development&&surroundingsSourceUrl(row.development.documentUrl)&&<a href={surroundingsSourceUrl(row.development.documentUrl)} target="_blank" rel="noopener noreferrer">계획 원문 ↗</a>}</li>)}</ul>}
    {view.unknownDistances>0&&<p className="surroundings-source">거리 미확인 {view.unknownDistances}곳은 반경 목록에서 제외했습니다.</p>}
    {view.source&&<p className="surroundings-source">{sourceUrl?<a href={sourceUrl} target="_blank" rel="noopener noreferrer">{view.source.label} ↗</a>:view.source.label} · {view.source.asOf} 기준 · 직선거리</p>}
    <PropertyMapLinks complex={complex} region={region}/>
  </section>;
}
