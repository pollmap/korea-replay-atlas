import {useId,useMemo,useRef,useState} from 'react';
import type {PropertyComplex,PropertyTransaction} from '../shared/property';
import {discoverPropertyComplexes,EMPTY_PROPERTY_DISCOVERY_FILTERS,propertyDiscoveryDongs,type PropertyDiscoveryFilters,type PropertyDiscoverySort} from '../shared/property-discovery';
import {moneyLabel} from '../shared/property-view';

export interface PropertyComplexListProps {
  complexes:PropertyComplex[];rows:PropertyTransaction[];dataReady:boolean;trade:'sale'|'rent';
  onSelect:(id:string)=>void;selectedId?:string;watchedIds?:ReadonlySet<string>;onWatch?:(item:PropertyComplex)=>void;
}
const PAGE_SIZE=40;

export default function PropertyComplexList({complexes,rows,dataReady,trade,onSelect,selectedId,watchedIds,onWatch}:PropertyComplexListProps){
  const [filters,setFilters]=useState<PropertyDiscoveryFilters>(EMPTY_PROPERTY_DISCOVERY_FILTERS),[page,setPage]=useState(0);
  const captionId=useId(),errorId=useId();
  const [filterPanel,setFilterPanel]=useState<'price'|'area'|'year'|null>(null);
  const filterButtons=useRef<HTMLDivElement>(null);
  const panelId=useId();
  const closeFilter=()=>{filterButtons.current?.querySelector<HTMLButtonElement>(`[data-filter="${filterPanel}"]`)?.focus();setFilterPanel(null);};
  const result=useMemo(()=>discoverPropertyComplexes(complexes,rows,dataReady,trade,filters),[complexes,rows,dataReady,trade,filters]);
  const dongs=useMemo(()=>propertyDiscoveryDongs(complexes),[complexes]);
  const lastPage=Math.max(0,Math.ceil(result.items.length/PAGE_SIZE)-1),currentPage=Math.min(page,lastPage),start=currentPage*PAGE_SIZE;
  const visible=result.items.slice(start,start+PAGE_SIZE);
  const change=<K extends keyof PropertyDiscoveryFilters>(key:K,value:PropertyDiscoveryFilters[K])=>{setFilters(current=>({...current,[key]:value}));setPage(0);};
  const active=Object.entries(filters).some(([key,value])=>key!=='sort'&&value!=='');
  return <section className="property-discovery" aria-label="아파트 단지 찾기">
    <label className="discovery-search">단지 찾기<input type="search" value={filters.query} placeholder="아파트 이름 또는 법정동" onChange={event=>change('query',event.target.value)} autoComplete="off"/></label>
    <div className="discovery-filter-chips" ref={filterButtons} role="group" aria-label="단지 조건 빠른 선택">{([['price',trade==='sale'?'매매가':'보증금',filters.priceMinEok||filters.priceMaxEok],['area','전용면적',filters.areaMinM2||filters.areaMaxM2],['year','건축연도',filters.buildYearMin||filters.buildYearMax]] as const).map(([id,label,enabled])=><button key={id} aria-expanded={filterPanel===id} aria-controls={panelId} data-filter={id} data-active={!!enabled} onClick={()=>setFilterPanel(current=>current===id?null:id)}>{label}{enabled?<span className="filter-active-dot" aria-label="적용 중"/>:<span aria-hidden="true">⌄</span>}</button>)}</div>
    <div className="discovery-selects">
      <label>법정동<select value={filters.dong} onChange={event=>change('dong',event.target.value)}><option value="">모든 법정동</option>{dongs.map(dong=><option key={dong} value={dong}>{dong}</option>)}</select></label>
      <label>정렬<select value={filters.sort} onChange={event=>change('sort',event.target.value as PropertyDiscoverySort)}><option value="recent">최근 계약순</option><option value="count">신고 거래 많은순</option><option value="price-low">최근 거래금액 낮은순</option><option value="price-high">최근 거래금액 높은순</option><option value="name">단지 이름순</option></select></label>
    </div>
    {filterPanel&&<div id={panelId} className="discovery-filters quick-filter-panel" onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();closeFilter();}}}>
      <div className="quick-filter-heading"><strong>{filterPanel==='price'?'거래금액 조건':filterPanel==='area'?'전용면적 조건':'건축연도 조건'}</strong><button aria-label="조건 선택 닫기" onClick={closeFilter}>닫기</button></div>
      {filterPanel==='price'&&<><div className="filter-presets">{[['3억 이하','','3'],['3–5억','3','5'],['5–8억','5','8'],['8–12억','8','12'],['12억 이상','12','']].map(([label,min,max])=><button key={label} aria-pressed={filters.priceMinEok===min&&filters.priceMaxEok===max} onClick={()=>{setFilters(current=>({...current,priceMinEok:min,priceMaxEok:max}));setPage(0);}}>{label}</button>)}</div>
      <fieldset aria-describedby={`${captionId}${result.errors.length?` ${errorId}`:''}`}>
        <legend>{trade==='sale'?'매매가':'보증금'} · 억원</legend>
        <label>최소<input inputMode="decimal" value={filters.priceMinEok} onChange={event=>change('priceMinEok',event.target.value)} placeholder="제한 없음" aria-label={`최소 ${trade==='sale'?'매매가':'보증금'} (억원)`}/></label>
        <span aria-hidden="true">–</span>
        <label>최대<input inputMode="decimal" value={filters.priceMaxEok} onChange={event=>change('priceMaxEok',event.target.value)} placeholder="제한 없음" aria-label={`최대 ${trade==='sale'?'매매가':'보증금'} (억원)`}/></label>
      </fieldset>
      {trade==='rent'&&<p className="discovery-note">보증금만 필터합니다. 월세는 최근 계약에 따로 표시합니다.</p>}</>}
      {filterPanel==='area'&&<><div className="filter-presets">{[['60㎡ 이하','','60'],['60–85㎡','60','85'],['85–102㎡','85','102'],['102㎡ 이상','102','']].map(([label,min,max])=><button key={label} aria-pressed={filters.areaMinM2===min&&filters.areaMaxM2===max} onClick={()=>{setFilters(current=>({...current,areaMinM2:min,areaMaxM2:max}));setPage(0);}}>{label}</button>)}</div><fieldset>
        <legend>전용면적 · ㎡</legend>
        <label>최소<input inputMode="decimal" value={filters.areaMinM2} onChange={event=>change('areaMinM2',event.target.value)} placeholder="제한 없음" aria-label="최소 전용면적 (제곱미터)"/></label>
        <span aria-hidden="true">–</span>
        <label>최대<input inputMode="decimal" value={filters.areaMaxM2} onChange={event=>change('areaMaxM2',event.target.value)} placeholder="제한 없음" aria-label="최대 전용면적 (제곱미터)"/></label>
      </fieldset><p className="discovery-note">전용면적 기준입니다. 공급면적·평형과 구분합니다.</p></>}
      {filterPanel==='year'&&<><fieldset>
        <legend>건축연도</legend>
        <label>이후<input inputMode="numeric" value={filters.buildYearMin} onChange={event=>change('buildYearMin',event.target.value)} placeholder="예: 2000" aria-label="최소 건축연도"/></label>
        <span aria-hidden="true">–</span>
        <label>이전<input inputMode="numeric" value={filters.buildYearMax} onChange={event=>change('buildYearMax',event.target.value)} placeholder="예: 2026" aria-label="최대 건축연도"/></label>
      </fieldset>
      <p className="discovery-note">건축연도는 실거래 신고 원문 기준이며 입주 예정연도가 아닙니다.</p></>}
      <button className="quick-filter-done" onClick={closeFilter}>{result.errors.length?'입력 범위를 확인해 주세요':`${result.items.length.toLocaleString('ko-KR')}개 단지 보기`}</button>
    </div>}
    <div className="discovery-result-heading"><p role="status">{result.items.length.toLocaleString('ko-KR')}개 단지{result.items.length>PAGE_SIZE?` · ${start+1}–${Math.min(start+PAGE_SIZE,result.items.length)}`:''}</p>{active&&<button className="discovery-reset" onClick={()=>{setFilters(EMPTY_PROPERTY_DISCOVERY_FILTERS);setPage(0);}}>필터 초기화</button>}</div>
    <p id={captionId} className="discovery-note discovery-source-note">선택 월의 최근 신고 거래 · 단지 시세와 다릅니다.</p>
    {!dataReady&&<p className="discovery-pending" role="status">거래 자료 확인 전입니다. 단지 정보로 먼저 탐색할 수 있습니다.{result.transactionFiltersPending?' 금액·면적 조건은 거래를 확인한 뒤 적용됩니다.':''}</p>}
    {result.errors.length>0&&<p id={errorId} role="alert" className="discovery-error">{result.errors.join(' ')}</p>}
    {!result.errors.length&&!result.items.length&&<p className="discovery-empty">{complexes.length?'조건에 맞는 단지가 없습니다. 검색어와 필터를 확인해 주세요.':'이 지역에 연결된 단지 식별자료가 없습니다.'}</p>}
    <ol className="discovery-results">{visible.map(({complex,count,latest})=><li key={complex.id}>
      <button className="discovery-open" aria-pressed={selectedId===complex.id} aria-label={`${complex.name} · ${complex.legal_dong_name??'법정동 미확인'} ${complex.lot_number??''} 거래 보기`} onClick={()=>onSelect(complex.id)}>
        <span className="discovery-name"><strong>{complex.name}</strong><span>{selectedId===complex.id?'선택됨':'거래 보기 →'}</span></span>
        <span className="discovery-address">{complex.legal_dong_name??'법정동 미확인'} {complex.lot_number??''} · {complex.build_year===null?'건축연도 미확인':`${complex.build_year}년 건축`}</span>
        {complex.address_conflict&&<span className="discovery-note">신고 주소가 달라 확인이 필요한 단지입니다.</span>}
        {latest?<>
          <span className="discovery-price"><span>{trade==='sale'?'최근 매매':'최근 보증금'}</span><strong>{moneyLabel(trade==='sale'?latest.price_krw:latest.deposit_krw)}원</strong>{trade==='rent'&&<small>월세 {moneyLabel(latest.monthly_rent_krw)}원</small>}</span>
          <span className="discovery-contract">{latest.contract_date} · 전용 {latest.area_m2}㎡{latest.floor===null?'':` · ${latest.floor}층`}</span>
        </>:<span className="discovery-no-trade">{count===null?'거래 자료 확인 전':'선택 월의 유효 신고 없음'}</span>}
        <span className="discovery-count">{count===null?'신고 건수 미확인':`현재 조건 ${count.toLocaleString('ko-KR')}건`}</span>
      </button>
      {onWatch&&<button className="discovery-watch" aria-label={`${complex.name} 관심 ${watchedIds?.has(complex.id)?'해제':'저장'}`} aria-pressed={watchedIds?.has(complex.id)??false} onClick={()=>onWatch(complex)}>{watchedIds?.has(complex.id)?'★':'☆'}</button>}
    </li>)}</ol>
    {lastPage>0&&<nav className="discovery-pagination" aria-label="단지 목록 페이지"><button disabled={currentPage===0} onClick={()=>setPage(currentPage-1)}>이전</button><span>{currentPage+1} / {lastPage+1}</span><button disabled={currentPage===lastPage} onClick={()=>setPage(currentPage+1)}>다음</button></nav>}
  </section>;
}
