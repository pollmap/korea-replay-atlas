import {useEffect,useId,useMemo,useRef,useState,type Dispatch,type SetStateAction} from 'react';
import type {PropertyComplex,PropertyTransaction} from '../shared/property';
import {discoverPropertyComplexes,EMPTY_PROPERTY_DISCOVERY_FILTERS,propertyDiscoveryDongs,type PropertyDiscoveryFilters,type PropertyDiscoverySort} from '../shared/property-discovery';
import {transactionPrice} from '../shared/property-pricing';
import {moneyLabel,monthLabel} from '../shared/property-view';
import {HISTORY_RANGES,historyMonths,historyRangeLabel,type HistoryRange} from '../shared/property-history';
import {areaMatches,areaFromBounds,exclusivePyeong,NATIONAL_AREA} from '../shared/property-area';
import {propertyFilterChips} from '../shared/property-filter-chips';
import PropertySavedFilters from './PropertySavedFilters';
import {confirmedPropertyMapIds} from './property-map-points';
import {generalPropertyFilters} from './property-general-filters';
import type {RentKind} from '../shared/property-rent';
import {loadComplexPriceSummaries} from './property-summary-client';
import {discoverPropertySummaryComplexes} from './property-summary-discovery';
import {summaryPrice} from './property-monthly-summary';
import type {MonthlySummaryData} from './PropertyMonthlySummary';
import {propertyListSummaryComplex} from './property-list-query';

export interface PropertyComplexListProps {
  visible?:boolean;
  compactControls?:boolean;
  qualificationKey?:string;onQualification?:(value:{key:string;id:string;eligible:boolean}|null)=>void;
  filtersState?:readonly [PropertyDiscoveryFilters,Dispatch<SetStateAction<PropertyDiscoveryFilters>>];
  dong?:string;onDong?:(dong:string)=>void;
  savedFilterRegion?:string;rentKind?:RentKind;onRentKind?:(kind:RentKind)=>void;
  /** PC period summaries. Omit these to retain the existing raw-month list. */
  release?:string;month?:string;periodMonths?:HistoryRange;onPeriodMonths?:(range:HistoryRange)=>void;
  area?:string;onArea?:(area:string)=>void;
  latestPrice?:{min:string;max:string};onLatestPrice?:(min:string,max:string)=>void;
  complexes:PropertyComplex[];rows:PropertyTransaction[];dataReady:boolean;trade:'sale'|'rent';
  onSelect:(id:string)=>void;selectedId?:string;watchedIds?:ReadonlySet<string>;onWatch?:(item:PropertyComplex)=>void;
}
const PAGE_SIZE=40;

export default function PropertyComplexList({visible:listVisible=true,qualificationKey,onQualification,compactControls=false,complexes,rows,dataReady,trade,onSelect,selectedId,watchedIds,onWatch,savedFilterRegion,rentKind,onRentKind,dong,onDong,release,month,periodMonths,onPeriodMonths,area,onArea,latestPrice,onLatestPrice,filtersState}:PropertyComplexListProps){
  const fallbackFilters=useState<PropertyDiscoveryFilters>(EMPTY_PROPERTY_DISCOVERY_FILTERS),[page,setPage]=useState(0);
  const [localFilters,setFilters]=filtersState??fallbackFilters;
  const filters=useMemo(()=>generalPropertyFilters(localFilters,{area,latestPrice,dong,rentKind}),[localFilters,rentKind,dong,area,latestPrice]);
  useEffect(()=>setPage(0),[rentKind,dong,area,periodMonths,month,latestPrice?.min,latestPrice?.max,localFilters.query,localFilters.sort,localFilters.buildYearMin,localFilters.buildYearMax,localFilters.hasTrades]);
  const summaryRequested=(listVisible||!!selectedId)&&!!release&&!!savedFilterRegion&&!!month&&periodMonths!==undefined&&HISTORY_RANGES.includes(periodMonths);
  const summaryComplex=propertyListSummaryComplex(listVisible,selectedId);
  const months=useMemo(()=>summaryRequested?historyMonths(month!,periodMonths!):[],[summaryRequested,month,periodMonths]);
  const summaryKey=summaryRequested?JSON.stringify([release,savedFilterRegion,month,periodMonths,trade,area??'',rentKind??'all',summaryComplex??'']):'';
  const [summary,setSummary]=useState<{key:string;state:'loading'|'ready'|'unavailable'|'error';data?:MonthlySummaryData;error?:string}>({key:'',state:'loading'});
  const [attempt,setAttempt]=useState(0);
  useEffect(()=>{if(!summaryRequested)return;const controller=new AbortController();
    void loadComplexPriceSummaries(release!,savedFilterRegion!,months,controller.signal,summaryComplex,{trade,area,rentKind}).then(data=>{if(!controller.signal.aborted)setSummary(data?{key:summaryKey,state:'ready',data}:{key:summaryKey,state:'unavailable'});}).catch(reason=>{if(!controller.signal.aborted)setSummary({key:summaryKey,state:'error',error:reason instanceof Error?reason.message:'선택 기간의 목록 요약을 불러오지 못했습니다.'});});
    return()=>controller.abort();
  },[summaryRequested,release,savedFilterRegion,months,summaryKey,attempt,trade,area,rentKind,summaryComplex]);
  const summaryActive=summaryRequested&&!(summary.key===summaryKey&&summary.state==='unavailable');
  const summaryReady=summaryActive&&summary.key===summaryKey&&summary.state==='ready';
  const summaryError=summaryActive&&summary.key===summaryKey&&summary.state==='error'?summary.error:undefined;
  const captionId=useId(),errorId=useId();
  const [filterPanel,setFilterPanel]=useState<'price'|'area'|'year'|null>(null);
  const filterButtons=useRef<HTMLDivElement>(null);
  const panelId=useId();
  const panel=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(filterPanel)panel.current?.querySelector<HTMLInputElement>('input')?.focus();},[filterPanel]);
  const closeFilter=()=>{filterButtons.current?.querySelector<HTMLButtonElement>(`[data-filter="${filterPanel}"]`)?.focus();setFilterPanel(null);};
  const rawResult=useMemo(()=>discoverPropertyComplexes(complexes,summaryRequested&&area?rows.filter(row=>areaMatches(row.area_m2,area)):rows,dataReady&&!summaryActive,trade,filters),[complexes,rows,dataReady,trade,filters,summaryRequested,area,summaryActive]);
  const summaryResult=useMemo(()=>summaryReady?discoverPropertySummaryComplexes(complexes,summary.data!.rows,summary.data!.partitions,{months,trade,area},filters):null,[summaryReady,summary.data,complexes,months,trade,area,filters]);
  const result=summaryResult??rawResult;
  const items=summaryResult?summaryResult.items.map(item=>({...item,kind:'summary' as const,summary:item.latest,latest:null})):rawResult.items.map(item=>({...item,kind:'raw' as const,summary:null}));
  const [locations,setLocations]=useState<{release:string;ids:readonly string[];mapped:ReadonlySet<string>;error:boolean}|null>(null);
  const locationIds=useMemo(()=>complexes.map(item=>item.id),[complexes]);
  const [locationAttempt,setLocationAttempt]=useState(0);
  useEffect(()=>{if(!release)return;let current=true;
    void confirmedPropertyMapIds(locationIds,release).then(mapped=>{if(current)setLocations({release,ids:locationIds,mapped,error:false});}).catch(()=>{if(current)setLocations({release,ids:locationIds,mapped:new Set(),error:true});});
    return()=>{current=false;};
  },[locationIds,release,locationAttempt]);
  const locationReady=locations&&locations.release===release&&locations.ids===locationIds&&!locations.error;
  const resultReady=summaryActive?summaryReady:dataReady||!rawResult.transactionFiltersPending;
  const selectedEligible=result.items.some(item=>item.complex.id===selectedId);
  useEffect(()=>{onQualification?.(resultReady&&qualificationKey&&selectedId?{key:qualificationKey,id:selectedId,eligible:selectedEligible}:null);},[onQualification,resultReady,qualificationKey,selectedId,selectedEligible]);
  const mappedCount=locationReady?result.items.filter(item=>!!item.complex.position||locations!.mapped.has(item.complex.id)).length:0;
  const dongs=useMemo(()=>propertyDiscoveryDongs(complexes),[complexes]);
  const lastPage=Math.max(0,Math.ceil(result.items.length/PAGE_SIZE)-1),currentPage=Math.min(page,lastPage),start=currentPage*PAGE_SIZE;
  const visible=items.slice(start,start+PAGE_SIZE);
  const change=<K extends keyof PropertyDiscoveryFilters>(key:K,value:PropertyDiscoveryFilters[K])=>{setFilters(current=>({...current,...(area?{areaMinM2:filters.areaMinM2,areaMaxM2:filters.areaMaxM2}:{}),[key]:value}));if(key==='priceMinEok'||key==='priceMaxEok')onLatestPrice?.(key==='priceMinEok'?String(value):filters.priceMinEok,key==='priceMaxEok'?String(value):filters.priceMaxEok);if(key==='dong')onDong?.(String(value));if(key==='areaMinM2'||key==='areaMaxM2')onArea?.(areaFromBounds(key==='areaMinM2'?String(value):filters.areaMinM2,key==='areaMaxM2'?String(value):filters.areaMaxM2));setPage(0);};
  const chips=propertyFilterChips(filters,trade),active=chips.length>0;
  const nationalArea=area===NATIONAL_AREA||filters.areaMinM2==='84'&&filters.areaMaxM2==='84.99999';
  return <section className={`property-discovery${compactControls?' discovery-compact':''}`} aria-label="아파트 단지 찾기">
    {!compactControls&&<>
    {savedFilterRegion&&<PropertySavedFilters month={month} region={savedFilterRegion} trade={trade} filters={filters} onApply={value=>{setFilters(value);onLatestPrice?.(value.priceMinEok,value.priceMaxEok);onArea?.(areaFromBounds(value.areaMinM2,value.areaMaxM2));if(value.dong!==filters.dong)onDong?.(value.dong);onRentKind?.(value.rentKind??'all');setPage(0);setFilterPanel(null);}}/>}
    <label className="discovery-search"><span className="sr-only">단지 찾기</span><input type="search" value={filters.query} placeholder="아파트 이름 또는 법정동" onChange={event=>change('query',event.target.value)} autoComplete="off"/></label>
    <div className="discovery-filter-chips" ref={filterButtons} role="group" aria-label="단지 조건 빠른 선택">{([['price',trade==='sale'?'매매가':'보증금',filters.priceMinEok||filters.priceMaxEok],['area','전용면적',filters.areaMinM2||filters.areaMaxM2],['year','건축연도',filters.buildYearMin||filters.buildYearMax]] as const).map(([id,label,enabled])=><button key={id} aria-expanded={filterPanel===id} aria-controls={panelId} data-filter={id} data-active={!!enabled} onClick={()=>setFilterPanel(current=>current===id?null:id)}>{label}{enabled?<span className="filter-active-dot" aria-label="적용 중"/>:<span aria-hidden="true">⌄</span>}</button>)}<button className="national-area-chip" aria-label="국평 전용 84㎡대 빠른 선택" aria-pressed={nationalArea} data-active={nationalArea} onClick={()=>{setFilters(current=>({...current,areaMinM2:nationalArea?'':'84',areaMaxM2:nationalArea?'':'84.99999'}));onArea?.(nationalArea?'':NATIONAL_AREA);setFilterPanel(null);setPage(0);}}>국평</button></div>
    <div className="discovery-selects discovery-primary-selects">
      {summaryRequested&&<><label title={`${monthLabel(months[0])}–${monthLabel(month!)}`}>기간{onPeriodMonths?<select aria-label="단지 목록 조회 기간" value={periodMonths} onChange={event=>onPeriodMonths(Number(event.target.value) as HistoryRange)}>{HISTORY_RANGES.map(value=><option key={value} value={value}>최근 {historyRangeLabel(value)}</option>)}</select>:<span>최근 {historyRangeLabel(periodMonths!)}</span>}</label></>}
      <label>법정동<select value={filters.dong} onChange={event=>change('dong',event.target.value)}><option value="">모든 법정동</option>{filters.dong&&!dongs.includes(filters.dong)&&<option value={filters.dong}>{filters.dong} · 연결된 단지 없음</option>}{dongs.map(dong=><option key={dong} value={dong}>{dong}</option>)}</select></label>
      <label>정렬<select value={filters.sort} onChange={event=>change('sort',event.target.value as PropertyDiscoverySort)}><option value="recent">최근 계약순</option><option value="count">신고 거래 많은순</option><option value="price-low">최근 거래금액 낮은순</option><option value="price-high">최근 거래금액 높은순</option><option value="pyeong-low">전용 평당가 낮은순</option><option value="pyeong-high">전용 평당가 높은순</option><option value="name">단지 이름순</option></select></label>
    </div>
    {filterPanel&&<div ref={panel} id={panelId} className="discovery-filters quick-filter-panel" onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeFilter();}}}>
      <div className="quick-filter-heading"><strong>{filterPanel==='price'?'최근 계약가격 조건':filterPanel==='area'?'전용면적 조건':'건축연도 조건'}</strong><button aria-label="조건 선택 닫기" onClick={closeFilter}>닫기</button></div>
      {filterPanel==='price'&&<><div className="filter-presets">{[['3억 이하','','3'],['3–5억','3','5'],['5–8억','5','8'],['8–12억','8','12'],['12억 이상','12','']].map(([label,min,max])=><button key={label} aria-pressed={filters.priceMinEok===min&&filters.priceMaxEok===max} onClick={()=>{setFilters(current=>({...current,priceMinEok:min,priceMaxEok:max}));onLatestPrice?.(min,max);setPage(0);}}>{label}</button>)}</div>
      <fieldset aria-describedby={`${captionId}${result.errors.length?` ${errorId}`:''}`}>
        <legend>{trade==='sale'?'매매가':'보증금'} · 억원</legend>
        <label>최소<input inputMode="decimal" value={filters.priceMinEok} onChange={event=>change('priceMinEok',event.target.value)} placeholder="제한 없음" aria-label={`최소 ${trade==='sale'?'매매가':'보증금'} (억원)`}/></label>
        <span aria-hidden="true">–</span>
        <label>최대<input inputMode="decimal" value={filters.priceMaxEok} onChange={event=>change('priceMaxEok',event.target.value)} placeholder="제한 없음" aria-label={`최대 ${trade==='sale'?'매매가':'보증금'} (억원)`}/></label>
      </fieldset>
      <p className="discovery-note">선택 기간·면적의 최근 계약 기준</p>{trade==='rent'&&<p className="discovery-note">보증금만 필터합니다. 월세는 최근 계약에 따로 표시합니다.</p>}</>}
      {filterPanel==='area'&&<><div className="filter-presets">{[['국평 · 84㎡대','84','84.99999'],['60㎡ 이하','','60'],['60–85㎡','60','85'],['85–102㎡','85','102'],['102㎡ 이상','102','']].map(([label,min,max])=><button key={label} aria-pressed={filters.areaMinM2===min&&filters.areaMaxM2===max} onClick={()=>{setFilters(current=>({...current,areaMinM2:min,areaMaxM2:max}));onArea?.(areaFromBounds(min,max));setPage(0);}}>{label}</button>)}</div><fieldset>
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
      <button className="quick-filter-done" disabled={!!result.errors.length} onClick={closeFilter}>{result.errors.length?'입력 범위를 확인해 주세요':`${result.items.length.toLocaleString('ko-KR')}개 단지 보기`}</button>
    </div>}
    {active&&<div className="discovery-active-filters" role="group" aria-label="적용한 조건">{chips.map(chip=><button key={chip.id} aria-label={`${chip.label} 조건 해제`} onClick={()=>{setFilters(current=>({...current,...chip.clear}));if(chip.clear.priceMinEok!==undefined||chip.clear.priceMaxEok!==undefined)onLatestPrice?.(chip.clear.priceMinEok??filters.priceMinEok,chip.clear.priceMaxEok??filters.priceMaxEok);if(chip.clear.areaMinM2!==undefined||chip.clear.areaMaxM2!==undefined)onArea?.('');if(chip.clear.dong!==undefined)onDong?.(chip.clear.dong);if(chip.clear.rentKind)onRentKind?.(chip.clear.rentKind);setPage(0);}}>{chip.label}<span aria-hidden="true"> ×</span></button>)}</div>}
    </>}
    <div className="discovery-result-heading"><p role="status">{resultReady?`${result.items.length.toLocaleString('ko-KR')}개 단지`:'후보 확인 중…'}{resultReady&&result.items.length>PAGE_SIZE?` · ${start+1}–${Math.min(start+PAGE_SIZE,result.items.length)}`:''}</p>{compactControls&&<label className="discovery-sort"><span className="sr-only">정렬</span><select aria-label="단지 정렬" value={filters.sort} onChange={event=>change('sort',event.target.value as PropertyDiscoverySort)}><option value="recent">최근 계약순</option><option value="count">거래 많은순</option><option value="price-low">가격 낮은순</option><option value="price-high">가격 높은순</option><option value="pyeong-low">평당가 낮은순</option><option value="pyeong-high">평당가 높은순</option><option value="name">이름순</option></select></label>}{!compactControls&&active&&<button className="discovery-reset" onClick={()=>{setFilters(EMPTY_PROPERTY_DISCOVERY_FILTERS);onLatestPrice?.('','');onArea?.('');onDong?.('');onRentKind?.('all');setPage(0);}}>초기화</button>}</div>
    {release&&resultReady&&<div className="discovery-map-coverage" role="status">{locations&&locations.release===release&&locations.ids===locationIds&&locations.error?<><span>위치 조회 실패</span><button onClick={()=>setLocationAttempt(value=>value+1)}>다시 확인</button></>:!locationReady?<span>위치 확인 중…</span>:<><span>지도 표시 가능 {mappedCount}개</span><span>위치 미연결 {result.items.length-mappedCount}개</span></>}</div>}
    {!compactControls&&<label className="discovery-trade-only"><input type="checkbox" checked={!!filters.hasTrades} onChange={event=>change('hasTrades',event.target.checked)}/>{summaryActive?'선택 기간 거래 있는 단지':'선택 월 거래 있는 단지'}</label>}
    {!compactControls&&<p id={captionId} className="discovery-note discovery-source-note" title="취소 거래를 제외한 최근 유효 거래입니다.">{summaryActive?'선택 기간의 최근 실거래':'선택 월의 최근 실거래'}</p>}
    {summaryRequested&&!summaryActive&&<p className="discovery-note">이 공개 버전에는 기간 요약이 없어 선택 월 원문을 표시합니다.</p>}
    {summaryActive&&!summaryReady&&!summaryError&&<p className="discovery-pending" role="status">거래 불러오는 중…</p>}
    {summaryError&&<p className="discovery-error" role="alert">{summaryError} <button onClick={()=>{setSummary({key:summaryKey,state:'loading'});setAttempt(value=>value+1);}}>요약 다시 확인</button></p>}
    {!summaryActive&&!dataReady&&<p className="discovery-pending" role="status">거래 확인 전{result.transactionFiltersPending?' 금액·면적 조건은 거래를 확인한 뒤 적용됩니다.':''}</p>}
    {result.errors.length>0&&<p id={errorId} role="alert" className="discovery-error">{result.errors.join(' ')}</p>}
    {resultReady&&!result.errors.length&&!result.items.length&&<p className="discovery-empty">{complexes.length?'조건에 맞는 단지가 없습니다. 검색어와 필터를 확인해 주세요.':'이 지역에 연결된 단지 식별자료가 없습니다.'}</p>}
    <ol className="discovery-results">{visible.map(item=>{const {complex,count,latest}=item,representative=item.summary;
      const contractDate=representative?.latest_contract_date??latest?.contract_date,contractArea=representative?.area_m2??latest?.area_m2;
      const price=representative?(trade==='sale'?representative.latest_price_krw:representative.latest_deposit_krw):latest?(trade==='sale'?latest.price_krw:latest.deposit_krw):null;
      const monthlyRent=representative?.latest_monthly_rent_krw??latest?.monthly_rent_krw??null;
      const perPyeong=representative?summaryPrice(representative,'pyeong'):latest?transactionPrice(latest,'pyeong'):null;
      return <li key={complex.id}>
      <button className="discovery-open" aria-pressed={selectedId===complex.id} aria-label={`${complex.name} · ${complex.legal_dong_name??'법정동 미확인'} ${complex.lot_number??''} 거래 보기`} onClick={()=>onSelect(complex.id)}>
        <span className="discovery-name"><strong>{complex.name}</strong><span>{selectedId===complex.id?'선택됨':'거래 보기 →'}</span></span>
        <span className="discovery-address">{complex.legal_dong_name??'법정동 미확인'} {complex.lot_number??''} · {complex.build_year===null?'건축연도 미확인':`${complex.build_year}년 건축`}</span>
        {release&&locationReady&&!complex.position&&!locations!.mapped.has(complex.id)&&<span className="discovery-location-status">위치 미연결 · 주소로 확인</span>}
        {complex.address_conflict&&<span className="discovery-note">신고 주소가 달라 확인이 필요한 단지입니다.</span>}
        {latest||representative?<>
          <span className="discovery-price"><span>{trade==='sale'?'최근 매매':'최근 보증금'}</span><strong>{moneyLabel(price)}원</strong>{compactControls&&<span className="discovery-price-area">전용 {contractArea}㎡</span>}{trade==='rent'&&<small>월세 {moneyLabel(monthlyRent)}원</small>}</span>
          {!compactControls&&<span className="discovery-unit-price">전용평당 {moneyLabel(perPyeong)}원{trade==='rent'?' · 보증금 기준':''}</span>}
          <span className="discovery-contract">{contractDate}{!compactControls&&<> · 전용 {contractArea}㎡{representative?` · ${exclusivePyeong(contractArea??null)}평`:latest?.floor==null?'':` · ${latest.floor}층`}</>}{compactControls&&latest?.floor!=null&&` · ${latest.floor}층`}</span>
        </>:<span className="discovery-no-trade">{count===null?summaryError?'거래 요약 조회 실패':summaryReady?summaryResult?.sourceUnavailableMonths===months.length?'선택 기간의 거래 원천 미제공':'가격 미확인':summaryActive?'가격 불러오는 중…':'거래 확인 전':summaryActive?'선택 기간 거래 없음':'선택 월 거래 없음'}</span>}
        <span className="discovery-count">{item.kind==='summary'?item.sourceUnavailableMonths===months.length?'거래 건수 미제공':`${item.missingMonths?'확보':'현재 조건'} ${item.confirmedCount.toLocaleString('ko-KR')}건${item.missingMonths?` · 미확인 ${item.missingMonths}개월`:''}`:count===null?'신고 건수 미확인':`현재 조건 ${count.toLocaleString('ko-KR')}건`}</span>
      </button>
      {onWatch&&<button className="discovery-watch" aria-label={`${complex.name} 관심 ${watchedIds?.has(complex.id)?'해제':'저장'}`} aria-pressed={watchedIds?.has(complex.id)??false} onClick={()=>onWatch(complex)}>{watchedIds?.has(complex.id)?'★':'☆'}</button>}
    </li>;})}</ol>
    {lastPage>0&&<nav className="discovery-pagination" aria-label="단지 목록 페이지"><button disabled={currentPage===0} onClick={()=>setPage(currentPage-1)}>이전</button><span>{currentPage+1} / {lastPage+1}</span><button disabled={currentPage===lastPage} onClick={()=>setPage(currentPage+1)}>다음</button></nav>}
    {summaryResult&&<details className="pricing-method"><summary>자료 기준</summary><p className="discovery-note">확인 {summaryResult.verifiedMonths}/{months.length-summaryResult.sourceUnavailableMonths}개월{summaryResult.missingMonths?` · 미수집·미확인 ${summaryResult.missingMonths}개월`:''}{summaryResult.sourceUnavailableMonths?` · 원천 제공 전 ${summaryResult.sourceUnavailableMonths}개월`:''}{summaryResult.priceFiltered?' · 금액 조건은 마지막 계약 기준, 건수는 면적·유형 조건의 확보 합계':''}</p></details>}
      {!compactControls&&summaryRequested&&<details className="discovery-advanced-conditions"><summary>단지·투자 조건</summary><p className="discovery-note">공식 자료 연결 전입니다. 전용면적과 실거래 조건을 먼저 이용해 주세요.</p><div className="discovery-filter-chips" role="group" aria-label="연결 전인 단지·투자 조건">{['공급면적','세대수','주차','용적률','건폐율','전세가율','갭가격'].map(label=><button key={label} disabled title="공식 자료 연결 전">{label} · 연결 전</button>)}</div></details>}
</section>;
}
