import {useEffect,useId,useRef,useState} from 'react';
import {areaFromBounds,areaLabel} from '../shared/property-area';
import {EMPTY_PROPERTY_DISCOVERY_FILTERS,propertyDiscoveryBounds,type PropertyDiscoveryFilters} from '../shared/property-discovery';
import {HISTORY_RANGES,historyRangeLabel,type HistoryRange} from '../shared/property-history';
import type {PropertyRegion} from '../shared/property';
import {monthLabel} from '../shared/property-view';
import {propertyProvinceLabel} from '../shared/property-scope';
import type {RentKind} from '../shared/property-rent';
import {propertyFilterChips} from '../shared/property-filter-chips';
import {propertyBudgetLabel} from './property-general-filters';
import PropertySavedFilters from './PropertySavedFilters';

export interface PropertyFilterBarProps {
  regions:readonly PropertyRegion[];province:string;region:string;dong:string;dongs:readonly string[];
  trade:'sale'|'rent';rentKind:RentKind;area:string;filters:PropertyDiscoveryFilters;
  month:string;months:readonly string[];provisionalMonth:string;range:HistoryRange;
  onProvince:(name:string)=>void;onRegion:(code:string)=>void;onDong:(name:string)=>void;
  onTrade:(trade:'sale'|'rent',kind:RentKind)=>void;onFilters:(next:PropertyDiscoveryFilters,explicitArea?:boolean)=>void;
  onRange:(range:HistoryRange)=>void;onMonth:(month:string)=>void;
}
type Panel='region'|'price'|'area'|'period'|'more';
const TITLES:Record<Panel,string>={region:'지역',price:'예산',area:'전용면적',period:'조회 기간',more:'상세조건'};
export default function PropertyFilterBar(props:PropertyFilterBarProps){
  const {regions,province,region,dong,dongs,trade,rentKind,area,filters,month,months,provisionalMonth,range,onProvince,onRegion,onDong,onTrade,onFilters,onRange,onMonth}=props;
  const [panel,setPanel]=useState<Panel|null>(null),[draft,setDraft]=useState(filters);
  const root=useRef<HTMLDivElement>(null),dialog=useRef<HTMLDivElement>(null),id=useId();
  const selected=regions.find(row=>row.lawd_code===region),provinces=[...new Set(regions.map(row=>row.name.trim().split(/\s+/)[0]))];
  const chips=propertyFilterChips(filters,trade),extraChips=chips.filter(chip=>['query','year','hasTrades'].includes(chip.id)),errors=propertyDiscoveryBounds(draft,trade).errors;
  const close=()=>{root.current?.querySelector<HTMLButtonElement>(`[data-panel="${panel}"]`)?.focus();setPanel(null);};
  useEffect(()=>{if(!panel)return;dialog.current?.querySelector<HTMLElement>('input, select, button')?.focus();const outside=(event:PointerEvent)=>{if(event.target instanceof Node&&!root.current?.contains(event.target))setPanel(null);};const back=()=>setPanel(null);document.addEventListener('pointerdown',outside);window.addEventListener('popstate',back);return()=>{document.removeEventListener('pointerdown',outside);window.removeEventListener('popstate',back);};},[panel]);
  const open=(next:Panel)=>{if(panel===next){close();return;}setDraft(filters);setPanel(next);};
  const change=(key:keyof PropertyDiscoveryFilters,value:string|boolean)=>setDraft(current=>({...current,[key]:value}));
  const apply=()=>{if(errors.length)return;onFilters(draft,panel==='area');close();};
  const label:Record<Panel,string>={region:dong?`${selected?.name.replace(province,'').trim()??''} ${dong}`:selected?.name??province??'지역',price:propertyBudgetLabel(filters.priceMinEok,filters.priceMaxEok),area:area?areaLabel(area):'전용면적',period:`최근 ${historyRangeLabel(range)}`,more:`상세조건${extraChips.length?` ${extraChips.length}`:''}`};
  const enabled:Record<Panel,boolean>={region:!!region||!!province,price:!!filters.priceMinEok||!!filters.priceMaxEok,area:!!area,period:false,more:!!filters.buildYearMin||!!filters.buildYearMax||!!filters.query||!!filters.hasTrades};
  return <div className="property-filter-bar" ref={root} aria-label="공통 아파트 조건" onKeyDown={event=>{if(event.key==='Escape'&&panel){event.preventDefault();event.stopPropagation();close();}}}>
    {(['region','price','area','period','more'] as const).map((value,index)=><div key={value} className={`general-filter-slot slot-${value}`}>
      {index===1&&<label className="general-trade"><span className="sr-only">거래 유형</span><select aria-label="거래 유형" value={trade==='sale'?'sale':rentKind} onChange={event=>onTrade(event.target.value==='sale'?'sale':'rent',event.target.value==='sale'?'all':event.target.value as RentKind)}><option value="sale">매매</option><option value="jeonse">전세</option><option value="monthly">월세</option><option value="all">전월세 전체</option></select></label>}
      <button type="button" data-panel={value} aria-haspopup="dialog" aria-expanded={panel===value} aria-controls={panel===value?id:undefined} data-active={enabled[value]} title={value==='more'&&extraChips.length?extraChips.map(chip=>chip.label).join(' · '):undefined} onClick={()=>open(value)}>{label[value]||'지역'}<span aria-hidden="true">⌄</span></button>
    </div>)}
    {chips.some(chip=>chip.id!=='dong'&&chip.id!=='rentKind')&&<button className="general-clear" title="지역·거래·기간은 유지" onClick={()=>onFilters({...EMPTY_PROPERTY_DISCOVERY_FILTERS,dong:filters.dong,rentKind:filters.rentKind,sort:filters.sort},true)}>조건 초기화</button>}
    {panel&&<div ref={dialog} id={id} className={`general-filter-popover popover-${panel}`} role="dialog" aria-label={`${TITLES[panel]} 선택`}>
      <header><strong>{TITLES[panel]}</strong><button type="button" aria-label="조건 선택 닫기" onClick={close}>×</button></header>
      {panel==='region'&&<div className="general-region-selects"><label>시도<select aria-label="시도 선택" value={province} onChange={event=>onProvince(event.target.value)}><option value="">전체 지역</option>{provinces.map(name=><option value={name} key={name}>{propertyProvinceLabel(name,[...regions])}</option>)}</select></label><label>시·군·구<select aria-label="시군구 선택" value={region} disabled={!province} onChange={event=>onRegion(event.target.value)}><option value="">전체 시·군·구</option>{regions.filter(row=>row.name.trim().split(/\s+/)[0]===province).map(row=><option key={row.lawd_code} value={row.lawd_code}>{row.name.replace(province,'').trim()||row.name}</option>)}</select></label><label>법정동<select aria-label="법정동 선택" disabled={!region} value={dong} onChange={event=>onDong(event.target.value)}><option value="">전체 법정동</option>{dong&&!dongs.includes(dong)&&<option value={dong}>{dong}</option>}{dongs.map(name=><option key={name} value={name}>{name}</option>)}</select></label></div>}
      {panel==='price'&&<><div className="general-presets">{[['3억 이하','','3'],['3–5억','3','5'],['5–8억','5','8'],['8–12억','8','12'],['12억 이상','12','']].map(([text,min,max])=><button key={text} aria-pressed={draft.priceMinEok===min&&draft.priceMaxEok===max} onClick={()=>setDraft(value=>({...value,priceMinEok:min,priceMaxEok:max}))}>{text}</button>)}</div><fieldset><legend>{trade==='sale'?'최근 매매가':'최근 보증금'} · 억원</legend><label>최소<input inputMode="decimal" aria-label="최소 예산 (억원)" placeholder="제한 없음" value={draft.priceMinEok} onChange={event=>change('priceMinEok',event.target.value)}/></label><span>–</span><label>최대<input inputMode="decimal" aria-label="최대 예산 (억원)" placeholder="제한 없음" value={draft.priceMaxEok} onChange={event=>change('priceMaxEok',event.target.value)}/></label></fieldset><p>선택 면적의 최근 거래로 후보를 찾습니다. 과거 거래는 그대로 표시합니다.</p>{trade==='rent'&&<p>월세는 예산 조건에 포함하지 않습니다.</p>}</>}
      {panel==='area'&&<><div className="general-presets">{[['국평 · 84㎡대','84','84.99999'],['60㎡ 이하','','60'],['60–85㎡','60','85'],['85–102㎡','85','102'],['102㎡ 이상','102','']].map(([text,min,max])=><button key={text} aria-pressed={draft.areaMinM2===min&&draft.areaMaxM2===max} onClick={()=>setDraft(value=>({...value,areaMinM2:min,areaMaxM2:max}))}>{text}</button>)}</div><fieldset><legend>전용면적 · ㎡</legend><label>최소<input inputMode="decimal" aria-label="최소 전용면적 (제곱미터)" placeholder="제한 없음" value={draft.areaMinM2} onChange={event=>change('areaMinM2',event.target.value)}/></label><span>–</span><label>최대<input inputMode="decimal" aria-label="최대 전용면적 (제곱미터)" placeholder="제한 없음" value={draft.areaMaxM2} onChange={event=>change('areaMaxM2',event.target.value)}/></label></fieldset><p>공급면적이 아닌 전용면적 기준입니다.</p></>}
      {panel==='period'&&<div className="general-period"><label>기간<select aria-label="공통 조회 기간" value={range} onChange={event=>onRange(Number(event.target.value) as HistoryRange)}>{HISTORY_RANGES.map(value=><option key={value} value={value}>최근 {historyRangeLabel(value)}</option>)}</select></label><label>기준 계약월<select aria-label="공통 기준 계약월" value={month} onChange={event=>onMonth(event.target.value)}>{[...new Set([month,...months])].sort().reverse().map(value=><option key={value} value={value}>{monthLabel(value)}{value===provisionalMonth?' · 잠정':''}</option>)}</select></label>{month===provisionalMonth&&<p>추가 신고에 따라 바뀌는 당월 자료입니다.</p>}</div>}
      {panel==='more'&&<><fieldset><legend>건축연도</legend><label>이후<input inputMode="numeric" aria-label="최소 건축연도" placeholder="예: 2000" value={draft.buildYearMin} onChange={event=>change('buildYearMin',event.target.value)}/></label><span>–</span><label>이전<input inputMode="numeric" aria-label="최대 건축연도" placeholder="예: 2026" value={draft.buildYearMax} onChange={event=>change('buildYearMax',event.target.value)}/></label></fieldset><label className="general-check"><input type="checkbox" checked={!!draft.hasTrades} onChange={event=>change('hasTrades',event.target.checked)}/>선택 기간 거래 있는 단지</label>{draft.query&&<label className="general-query">저장된 목록 검색어<input value={draft.query} onChange={event=>change('query',event.target.value)}/></label>}{region&&<PropertySavedFilters region={region} trade={trade} month={month} filters={filters} onApply={value=>{onFilters(value);setDraft(value);}}/>}<details className="general-unavailable"><summary>추가 단지 조건</summary><p>세대수·주차·용적률·건폐율·전세가율·갭은 자료 연결 전입니다.</p></details></>}
      {(panel==='price'||panel==='area'||panel==='more')&&<>{!!errors.length&&<p role="alert">{errors.join(' ')}</p>}<footer><button onClick={()=>setDraft(current=>panel==='price'?{...current,priceMinEok:'',priceMaxEok:''}:panel==='area'?{...current,areaMinM2:'',areaMaxM2:''}:{...current,buildYearMin:'',buildYearMax:'',hasTrades:false,query:''})}>해제</button><button className="general-apply" disabled={!!errors.length||panel==='area'&&(!!draft.areaMinM2||!!draft.areaMaxM2)&&!areaFromBounds(draft.areaMinM2,draft.areaMaxM2)} onClick={apply}>적용</button></footer></>}
      {(panel==='region'||panel==='period')&&<footer><button className="general-apply" onClick={close}>확인</button></footer>}
    </div>}
  </div>;
}
