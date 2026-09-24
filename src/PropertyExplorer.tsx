import {useEffect,useMemo,useRef,useState} from 'react';
import type {Place} from '../shared/contracts';
import {parsePropertyComplexes,parsePropertyRegionDetail,parsePropertyTransactions,type PropertyComplex,type PropertyRegion,type PropertyRegionDetail,type PropertyTransaction,type RegionMetric} from '../shared/property';
import {metricCount,moneyLabel,monthLabel,propertyStatus,propertyRowsView,propertyAreaOptions,transactionCsv,transactionRows,readPropertyView,type PropertyViewState} from '../shared/property-view';
import {fetchPinnedJson} from './atlas-client';
import type {AtlasContent} from './useAtlas';
import ComplexComparison from './ComplexComparison';
import PropertyComplexList from './PropertyComplexList';
import PropertyHistory from './PropertyHistory';
import type {HistoryRange} from '../shared/property-history';
import {regionNavigationPlace} from './region-navigation';
import {PLACES} from '../shared/sources';
import type {PropertyMapPoint} from '../shared/property-map-point';
import {findPropertyMapPoint} from './property-map-points';

export type {PropertyViewState} from '../shared/property-view';
interface Props {atlas:AtlasContent;hidden:boolean;onClose:()=>void;onLocate:(place:Place)=>void;onViewState:(state:PropertyViewState)=>void;onMapPoint?:(point:PropertyMapPoint|null)=>void;requestedRegion?:{code:string;request:number;complexId?:string;skipLocate?:boolean};}
const REGION_SHORTCUTS=[['전국',''],['서울','서울특별시'],['경기','경기도'],['인천','인천광역시'],['부산','부산광역시'],['대구','대구광역시'],['제주','제주특별자치도']] as const;
function VolumeChart({metrics}:{metrics:RegionMetric[]}){
  const data=metrics.slice(-24),maximum=Math.max(1,...data.map(r=>metricCount(r)??0)),w=300,h=105,step=w/Math.max(1,data.length);
  return <svg className="volume-chart" viewBox={`0 0 ${w} ${h+22}`} role="img" aria-label="월별 신고 거래량. 점선은 수집되지 않은 기간이며 0건과 다릅니다."><title>월별 신고 거래량</title>{data.map((row,i)=>{const count=metricCount(row),height=count===null?0:count/maximum*(h-18);return <g key={row.deal_month}><title>{monthLabel(row.deal_month)} {count===null?propertyStatus(row.status):`${count}건`}</title>{count===null?<line x1={i*step+2} x2={(i+1)*step-2} y1={h-1} y2={h-1} stroke="#a8b5af" strokeDasharray="2 2"/>:<rect x={i*step+2} y={h-height} width={Math.max(1,step-4)} height={Math.max(1,height)} rx="1" fill="#3c7d66"/>}{(i===0||i===data.length-1||i%6===0)&&<text x={i*step+step/2} y={h+16} textAnchor={i===0?'start':i===data.length-1?'end':'middle'}>{row.deal_month.slice(2,4)}.{row.deal_month.slice(4)}</text>}</g>})}</svg>;
}
function PriceChart({rows,trade}:{rows:PropertyTransaction[];trade:'sale'|'rent'}){
  const points=rows.filter(r=>r.contract_date&&(trade==='sale'?r.price_krw:r.deposit_krw)!==null),values=points.map(r=>(trade==='sale'?r.price_krw:r.deposit_krw)!),min=Math.min(...values),max=Math.max(...values);
  if(!points.length)return <p className="property-empty">{rows.length?'이 기록에는 차트에 표시할 금액·계약일이 없습니다.':'선택한 조건에 해당하는 신고 거래가 없습니다.'}</p>;
  return <svg className="price-chart" viewBox="0 0 300 140" role="img" aria-label={`${trade==='sale'?'실거래 가격':'임대차 보증금'} 분포. 개별 계약별 점이며 미래 가격 예측이 아닙니다.`}><title>{trade==='sale'?'매매 가격':'임대차 보증금'} · 동일 전용면적을 선택해 비교하세요</title><text x="4" y="13">{moneyLabel(max)}원</text><text x="4" y="111">{moneyLabel(min)}원</text><line x1="4" y1="115" x2="296" y2="115" stroke="#dce5dd"/>{points.slice(0,1000).map(row=>{const date=Number(row.contract_date!.slice(-2)),value=(trade==='sale'?row.price_krw:row.deposit_krw)!;return <circle key={row.id} cx={12+(date-1)/30*278} cy={max===min?70:105-(value-min)/(max-min)*78} r="3.3" fill="#307958" opacity=".7"><title>{row.contract_date} · {row.area_m2}㎡ · {row.floor??'미상'}층 · {moneyLabel(value)}원</title></circle>;})}<text x="10" y="134">1일</text><text x="290" y="134" textAnchor="end">31일</text></svg>;
}
export default function PropertyExplorer({atlas,hidden,onClose,onLocate,onViewState,onMapPoint,requestedRegion}:Props){
  const [initial]=useState(()=>readPropertyView(location.hash,atlas.property.period)),[propertyType,setPropertyType]=useState<'apartment'|'officetel'>(initial.propertyType??'apartment'),[regionCode,setRegionCode]=useState(initial.propertyType==='officetel'?'':initial.region),[trade,setTrade]=useState<'sale'|'rent'>(initial.trade),[month,setMonth]=useState(initial.month),[complexId,setComplexId]=useState(initial.propertyType==='officetel'?'':initial.complex),[area,setArea]=useState(initial.area),[compare,setCompare]=useState(initial.compare);
  const [filter,setFilter]=useState(''),[detail,setDetail]=useState<PropertyRegionDetail|null>(null),[complexes,setComplexes]=useState<PropertyComplex[]>([]),[rows,setRows]=useState<PropertyTransaction[]>([]),[rowsKey,setRowsKey]=useState(''),[error,setError]=useState(''),[rowError,setRowError]=useState(''),[compareError,setCompareError]=useState(''),[busy,setBusy]=useState(false),[rowBusy,setRowBusy]=useState(false),[expert,setExpert]=useState(initial.includeReview),[showCancelled,setShowCancelled]=useState(initial.includeReview),[sheet,setSheet]=useState<'peek'|'half'|'full'>('half'),[limit,setLimit]=useState(60),[compareAttempt,setCompareAttempt]=useState(0);
  const heading=useRef<HTMLHeadingElement>(null),historyView=useRef<PropertyViewState|null>(null),restoring=useRef(false);
  const [compareComplexes,setCompareComplexes]=useState<PropertyComplex[]>([]);
  const [compareIds,setCompareIds]=useState(initial.propertyType==='officetel'?[]:initial.compareComplexes);
  const [historyRange,setHistoryRange]=useState<HistoryRange>(initial.historyMonths);
  const [mapPointCandidate,setMapPointCandidate]=useState(false);
  const [pointResult,setPointResult]=useState<{id:string;release:string;point:PropertyMapPoint|null;error:string}|null>(null);
  const [pointAttempt,setPointAttempt]=useState(0),locateAfterSelection=useRef('');
  const mapPoint=pointResult?.id===complexId&&pointResult.release===atlas.property.release_id?pointResult.point:null;
  const pointError=pointResult?.id===complexId&&pointResult.release===atlas.property.release_id?pointResult.error:'';
  const providerPointLinked=!!mapPoint||mapPointCandidate;
  useEffect(()=>{
    let active=true;const release=atlas.property.release_id;
    void findPropertyMapPoint(complexId,release).then(point=>{if(active)setPointResult({id:complexId,release,point,error:''});}).catch(()=>{if(active)setPointResult({id:complexId,release,point:null,error:'제공 위치를 불러오지 못했습니다.'});});
    return()=>{active=false;};
  },[complexId,atlas.property.release_id,pointAttempt]);
  const [detailNavigation,setDetailNavigation]=useState<{id:string;section:'trades'|'info'|'compare'}>({id:'',section:'trades'});
  const detailSection=detailNavigation.id===complexId?detailNavigation.section:'trades';
  useEffect(()=>{
    setCompareComplexes([]);setCompareError('');if(!compareIds.length)return;const controller=new AbortController();
    void Promise.all([...new Set(compareIds.map(id=>id.split(':')[1]))].map(async code=>{
      const row=atlas.regions.regions.find(r=>r.lawd_code===code);if(!row)throw new Error('공유된 비교 지역을 찾지 못했습니다.');
      const detail=parsePropertyRegionDetail(await fetchPinnedJson(row.index,atlas.origin,controller.signal));
      if(detail.release_id!==atlas.property.release_id||detail.lawd_code!==code||!detail.complexes)throw new Error('공유된 단지 목록을 찾지 못했습니다.');
      const list=parsePropertyComplexes(await fetchPinnedJson(detail.complexes,atlas.origin,controller.signal));
      if(list.release_id!==atlas.property.release_id||list.lawd_code!==code)throw new Error('공유된 단지 자료 버전이 다릅니다.');return list.complexes.filter(c=>compareIds.includes(c.id));
    })).then(values=>{if(controller.signal.aborted)return;const items=values.flat();if(items.length!==compareIds.length)throw new Error('공유된 비교 단지가 이 자료 버전에 없습니다.');setCompareComplexes(items);}).catch(reason=>{if(!controller.signal.aborted)setCompareError(reason instanceof Error?reason.message:'단지 비교 복원 오류');});
    return()=>controller.abort();
  },[atlas,compareIds,compareAttempt]);
  const region=atlas.regions.regions.find(r=>r.lawd_code===regionCode)??null;
  const queryKey=`${atlas.property.release_id}:${regionCode}:${month}:${trade}`;
  useEffect(()=>{
    const next={propertyType,region:regionCode,trade,month,complex:complexId,area,compare,compareComplexes:compareIds,includeReview:showCancelled,historyMonths:historyRange};onViewState(next);
    const previous=historyView.current;historyView.current=next;
    if(!previous||restoring.current){restoring.current=false;return;}
    const url=new URL(location.href),params=new URLSearchParams(url.hash.slice(1));
    for(const [key,value] of Object.entries({propertyType:propertyType==='officetel'?'officetel':'',regionCode,trade,month,complex:complexId,area,compareRegions:compare.join(','),compareComplexes:next.compareComplexes.join(','),review:showCancelled?'include':'',historyMonths:String(historyRange),propertyRelease:atlas.property.release_id}))if(value)params.set(key,value);else params.delete(key);
    url.hash=params.toString();
    if(previous.propertyType!==propertyType||previous.region!==regionCode||previous.complex!==complexId)history.pushState({property:true},'',url);else history.replaceState({property:true},'',url);
  },[propertyType,regionCode,trade,month,complexId,area,compare,compareIds,showCancelled,historyRange,onViewState,atlas.property.release_id]);
  useEffect(()=>{const back=()=>{const next=readPropertyView(location.hash,atlas.property.period);restoring.current=true;setMapPointCandidate(false);setPropertyType(next.propertyType??'apartment');setRegionCode(next.region);setTrade(next.trade);setMonth(next.month);setComplexId(next.complex);setArea(next.area);setCompare(next.compare);setCompareIds(next.compareComplexes);setShowCancelled(next.includeReview);setHistoryRange(next.historyMonths);if(next.includeReview)setExpert(true);if(next.region!==regionCode){const row=atlas.regions.regions.find(item=>item.lawd_code===next.region),place=row?regionNavigationPlace(row,atlas.map):!next.region?PLACES[0]:null;if(place)onLocate(place);}};window.addEventListener('popstate',back);return()=>window.removeEventListener('popstate',back);},[atlas,regionCode,onLocate]);
  useEffect(()=>{
    if(!region)return;const controller=new AbortController();setBusy(true);setError('');setDetail(null);setRows([]);setComplexes([]);
    void(async()=>{const next=parsePropertyRegionDetail(await fetchPinnedJson(region.index,atlas.origin,controller.signal));if(next.release_id!==atlas.property.release_id||next.lawd_code!==region.lawd_code)throw new Error('선택 지역과 자료 버전이 다릅니다.');if(controller.signal.aborted)return;setDetail(next);if(next.complexes){const result=parsePropertyComplexes(await fetchPinnedJson(next.complexes,atlas.origin,controller.signal));if(result.release_id!==next.release_id||result.lawd_code!==next.lawd_code)throw new Error('단지 목록의 지역이 다릅니다.');if(!controller.signal.aborted)setComplexes(result.complexes);}})().catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:'지역 자료 오류');}).finally(()=>{if(!controller.signal.aborted)setBusy(false);});
    return()=>controller.abort();
  },[region,atlas.origin,atlas.property.release_id]);
  useEffect(()=>{
    if(!detail||detail.lawd_code!==regionCode)return;const partition=detail.partitions.find(p=>p.deal_month===month&&p.trade_type===trade),controller=new AbortController();setRows([]);setRowsKey(queryKey);setRowError('');setRowBusy(true);setLimit(60);
    if(!partition||!['complete','empty'].includes(partition.status)){setRowBusy(false);return()=>controller.abort();}
    void Promise.all(partition.transactions.map(async ref=>{const result=parsePropertyTransactions(await fetchPinnedJson(ref,atlas.origin,controller.signal));if(result.release_id!==atlas.property.release_id||result.lawd_code!==regionCode||result.deal_month!==month)throw new Error('거래 자료의 지역·기간·버전이 다릅니다.');return result.transactions;})).then(parts=>{if(!controller.signal.aborted)setRows(parts.flat());}).catch(e=>{if(!controller.signal.aborted)setRowError(e instanceof Error?e.message:'거래 자료 오류');}).finally(()=>{if(!controller.signal.aborted)setRowBusy(false);});return()=>controller.abort();
  },[detail,regionCode,month,trade,atlas.origin,atlas.property.release_id,queryKey]);
  const availableRegions=useMemo(()=>atlas.regions.regions.filter(r=>!filter||r.name.includes(filter)||r.lawd_code.includes(filter)).sort((a,b)=>(metricCount(b.latest[trade])??-1)-(metricCount(a.latest[trade])??-1)||a.name.localeCompare(b.name,'ko')),[atlas.regions.regions,filter,trade]);
  const displayed=useMemo(()=>rowsKey===queryKey?transactionRows(rows,{trade,complex:complexId||null,area,cancelled:showCancelled}):[],[rows,rowsKey,queryKey,trade,complexId,area,showCancelled]);
  const areas=useMemo(()=>rowsKey===queryKey?[...new Set(rows.filter(r=>r.trade_type===trade&&(!complexId||r.complex_id===complexId)&&r.area_m2).map(r=>r.area_m2!))].sort((a,b)=>Number(a)-Number(b)):[],[rows,rowsKey,queryKey,trade,complexId]);
  const selectedDetail=detail?.lawd_code===regionCode?detail:null;
  const complex=complexes.find(c=>c.id===complexId&&c.lawd_code===regionCode),series=selectedDetail?.metrics.filter(m=>m.trade_type===trade).sort((a,b)=>a.deal_month.localeCompare(b.deal_month))??[],current=series.find(m=>m.deal_month===month);
  useEffect(()=>{onMapPoint?.(complex?mapPoint:null);return()=>onMapPoint?.(null);},[complex,mapPoint,onMapPoint]);
  useEffect(()=>{
    if(!complex||!mapPoint||locateAfterSelection.current!==complex.id)return;
    locateAfterSelection.current='';
    onLocate({id:complex.id,name:complex.name,region:region?.name??'',lon:mapPoint.longitude,lat:mapPoint.latitude,range:1300});
  },[complex,mapPoint,region,onLocate]);
  const partition=selectedDetail?.partitions.find(p=>p.deal_month===month&&p.trade_type===trade);
  const activeRowError=rowsKey===queryKey?rowError:'';
  const rowsView=propertyRowsView({status:partition?.status,loading:rowBusy,loaded:rowsKey===queryKey,error:activeRowError,count:displayed.length});
  const comparison=compare.flatMap(code=>atlas.regions.regions.find(r=>r.lawd_code===code)??[]);
  const resetScroll=()=>{const body=heading.current?.closest('aside')?.querySelector('.property-body');if(body)body.scrollTop=0;};
  const chooseRegion=(row:PropertyRegion)=>{setRegionCode(row.lawd_code);setComplexId('');setMapPointCandidate(false);setArea('');setError('');setSheet('half');heading.current?.focus();resetScroll();const place=regionNavigationPlace(row,atlas.map);if(place)onLocate(place);};
  const handledRegion=useRef<number|null>(null);
  useEffect(()=>{
    if(!requestedRegion||handledRegion.current===requestedRegion.request)return;
    if(!requestedRegion.code){handledRegion.current=requestedRegion.request;setRegionCode('');setComplexId('');setMapPointCandidate(false);setArea('');return;}
    const row=atlas.regions.regions.find(item=>item.lawd_code===requestedRegion.code);if(!row)return;
    handledRegion.current=requestedRegion.request;setRegionCode(row.lawd_code);setComplexId(requestedRegion.complexId??'');setMapPointCandidate(!!requestedRegion.complexId);setArea('');setError('');setSheet('half');
    const place=regionNavigationPlace(row,atlas.map);if(place&&!requestedRegion.skipLocate)onLocate(place);
    const body=heading.current?.closest('aside')?.querySelector('.property-body');if(body)body.scrollTop=0;
    requestAnimationFrame(()=>heading.current?.focus());
  },[requestedRegion,atlas,onLocate]);
  const toggleCompare=(code:string)=>setCompare(previous=>previous.includes(code)?previous.filter(v=>v!==code):previous.length<3?[...previous,code]:previous);
  const months=[...new Set([month,...series.filter(row=>metricCount(row)!==null).map(row=>row.deal_month)])].sort(),total=atlas.regions.regions.reduce((sum,r)=>sum+(metricCount(r.latest[trade])??0),0),collected=atlas.regions.regions.filter(r=>metricCount(r.latest[trade])!==null).length;
  const download=()=>{if(rowsView.state!=='ready'||!displayed.length)return;const url=URL.createObjectURL(new Blob([transactionCsv(displayed)],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download=`korea-replay-${regionCode}-${month}-${trade}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  return <aside className={`property-panel sheet-${sheet} ${complex?'showing-complex':region?'showing-region':'showing-country'}`} data-trade={trade} hidden={hidden} aria-label="지역·주거 분석">
    <div className="sheet-handle"><button aria-label={sheet==='peek'?'지역 분석 펼치기':sheet==='half'?'지역 분석 전체 높이':'지역 분석 접기'} onClick={()=>setSheet(sheet==='peek'?'half':sheet==='half'?'full':'peek')}><span/><small>{sheet==='full'?'지도와 함께 보기':sheet==='half'?'분석 화면 펼치기':'지역 분석 열기'}</small></button></div>
    <header className="property-header"><div>{region?<button className="property-back" onClick={()=>{setMapPointCandidate(false);if(complexId){setComplexId('');setArea('');}else{setRegionCode('');onLocate(PLACES[0]);}resetScroll();}}>{complexId?`← ${region.name}`:'← 전국 지역 목록'}</button>:<span className="eyebrow">전국 주거 실거래 분석</span>}<h2 ref={heading} tabIndex={-1}>{complex?.name??region?.name??'어느 지역을 살펴볼까요?'}</h2><div className="property-type-switch" role="group" aria-label="주거 유형">{([['apartment','아파트'],['officetel','오피스텔']] as const).map(([type,label])=><button key={type} aria-pressed={propertyType===type} onClick={()=>{if(propertyType===type)return;setPropertyType(type);setRegionCode('');setComplexId('');setArea('');setCompare([]);setCompareIds([]);setCompareComplexes([]);setMapPointCandidate(false);setFilter('');setError('');onLocate(PLACES[0]);resetScroll();}}>{label}</button>)}</div>{complex&&!complex.position&&(providerPointLinked?<span className="point-link-status">실거래 ID 연결 · 위치 검증 중</span>:<span className="point-link-status unlocated">단지 좌표 미연결 · 지도에는 지역만 표시</span>)}</div><button aria-label="지역 분석 닫기" onClick={onClose}>×</button></header>
    {complex&&mapPoint&&<button className="property-provider-location" onClick={()=>onLocate({id:complex.id,name:complex.name,region:region?.name??'',lon:mapPoint.longitude,lat:mapPoint.latitude,range:1300})}>지도에서 제공 위치 보기 <span>서울시 제공 점 · 위치 검증 중</span></button>}
    {complex&&pointError&&<p className="property-caption" role="status">{pointError} <button onClick={()=>setPointAttempt(value=>value+1)}>위치 다시 불러오기</button></p>}
    {complex&&<><div className="complex-identity"><span>{[complex.legal_dong_name,complex.lot_number].filter(Boolean).join(' ')}</span><span>{complex.build_year===null?'건축연도 미제공':`${complex.build_year}년 건축`}</span><button disabled={compareIds.length>=3&&!compareIds.includes(complex.id)} aria-pressed={compareIds.includes(complex.id)} onClick={()=>{setCompareIds(previous=>previous.includes(complex.id)?previous.filter(id=>id!==complex.id):[...previous,complex.id]);}}>{compareIds.includes(complex.id)?'비교 담음':'비교 담기'}</button></div><nav className="complex-section-nav" aria-label="단지 상세 메뉴">{([['trades','실거래'],['info','단지정보'],['compare',`비교${compareIds.length?` ${compareIds.length}`:''}`]] as const).map(([section,label])=><button key={section} aria-pressed={detailSection===section} onClick={()=>{setDetailNavigation({id:complexId,section});resetScroll();}}>{label}</button>)}</nav></>}
    <div className="property-body">
    {propertyType==='officetel'?<section className="property-data-pending" aria-live="polite"><span className="eyebrow">오피스텔 · 아파트 자료와 분리</span><h3>오피스텔 거래자료를 연결하고 있습니다</h3><p>아파트 거래·단지·가격을 오피스텔 데이터로 섞어 표시하지 않습니다. 오피스텔 매매·전월세 공식 원천 API는 별도 수집기로 준비되어 있지만, 현재 공개 릴리스에는 실제 응답 검증과 전국 배포가 완료된 오피스텔 자료가 없습니다.</p><dl><div><dt>매매</dt><dd>국토교통부 오피스텔 매매 신고자료 · 미게시</dd></div><div><dt>전월세</dt><dd>국토교통부 오피스텔 전월세 신고자료 · 미게시</dd></div><div><dt>표시 정책</dt><dd>검증된 자료가 게시되기 전까지 건수·가격을 추정하거나 0건으로 표시하지 않음</dd></div></dl><p className="property-caption">자료가 연결되면 같은 화면에서 지역·계약월·매매/전월세별 실거래를 확인할 수 있습니다. 건물 식별과 위치가 검증되지 않은 동명 건물은 임의로 합치지 않습니다.</p></section>:<>
    <div className="property-querybar"><div className="property-trade" role="group" aria-label="거래 유형"><button aria-pressed={trade==='sale'} onClick={()=>{setTrade('sale');setArea('');}}>매매</button><button aria-pressed={trade==='rent'} onClick={()=>{setTrade('rent');setArea('');}}>전월세</button></div>{region&&selectedDetail?<label className="property-month"><span className="sr-only">계약월</span><select aria-label="계약월" value={month} onChange={e=>setMonth(e.target.value)}>{[...new Set(months)].reverse().map(m=><option value={m} key={m}>{monthLabel(m)}{m===selectedDetail.period.to?' · 잠정':''}</option>)}</select></label>:<span className="property-period">{monthLabel(atlas.property.period.latest_complete_month)} 계약</span>}</div>
    {region&&month===atlas.property.period.to&&<p className="property-caption">당월 잠정 자료 · 추가 신고에 따라 바뀔 수 있습니다.</p>}
    {(error||activeRowError||compareError)&&<p role="alert" className="property-error">{error||activeRowError||compareError}</p>}{regionCode&&!region&&<p role="alert">선택 지역은 이 자료 버전에 없습니다. 최신 지역으로 자동 대체하지 않습니다.</p>}{complexId&&!busy&&selectedDetail&&!complex&&<p role="alert">선택 단지는 이 자료 버전에 없습니다.</p>}
    {compareError&&<div className="property-table-tools"><button onClick={()=>setCompareAttempt(value=>value+1)}>비교 다시 불러오기</button><button onClick={()=>setCompareIds([])}>비교 목록 비우기</button></div>}
    {!region?<>
      <div className="property-stat national-stat"><span>{collected===atlas.regions.regions.length?'전국':'수집 완료 지역'} {trade==='sale'?'매매':'전월세'} 거래량</span><strong>{collected?total.toLocaleString('ko-KR'):'—'}<small>건</small></strong><span>{collected} / {atlas.regions.regions.length}개 지역 확인 · 국토교통부</span></div>
      <div className="region-shortcuts" role="group" aria-label="권역 빠른 탐색">{REGION_SHORTCUTS.map(([label,value])=><button key={label} aria-pressed={filter===value} onClick={()=>setFilter(value)}>{label}</button>)}</div>
      <label className="property-search region-search"><span>지역 찾기</span><input type="search" value={filter} placeholder="시·군·구 검색" onChange={e=>setFilter(e.target.value)}/></label>
      <div className="property-list-heading"><span>{filter?'검색한 지역':'거래량 많은 지역'}</span><span>신고 거래 · 건</span></div>
      {!availableRegions.length&&<p className="property-empty">일치하는 지역이 없습니다. 시·군·구 이름을 확인해 주세요.</p>}
      <ol className="region-results">{availableRegions.map(row=><li key={row.lawd_code}><button className="region-open" onClick={()=>chooseRegion(row)}><span>{row.name}</span><strong>{metricCount(row.latest[trade])?.toLocaleString('ko-KR')??propertyStatus(row.latest[trade].status)}</strong></button><button className="compare-add" aria-label={`${row.name} 비교 ${compare.includes(row.lawd_code)?'해제':'추가'}`} aria-pressed={compare.includes(row.lawd_code)} disabled={compare.length>=3&&!compare.includes(row.lawd_code)} onClick={()=>toggleCompare(row.lawd_code)}>{compare.includes(row.lawd_code)?'✓':'+'}</button></li>)}</ol>
    </>:<>
      {busy?<div className="property-loading" role="status"><p>지역 자료를 확인하고 있습니다…</p><i/><i/><i/></div>:selectedDetail&&<>
        {!complexId&&<div className="property-stat region-inline-stat"><span>{complexId?'선택 단지·면적 신고 거래량':'지역 전체 신고 거래량'}</span><strong>{current&&metricCount(current)!==null?(complexId?(rowsView.state!=='ready'||!complex?'—':transactionRows(rows,{trade,complex:complexId,area,cancelled:false}).length.toLocaleString('ko-KR')):metricCount(current)!.toLocaleString('ko-KR')):'—'}<small>건</small></strong><span>{current?propertyStatus(current.status):'자료 없음'} · {trade==='sale'?'취소·취소 여부 미확인 제외':'원천에서 취소 여부 미제공'}</span></div>}
        <div hidden={!!complexId}><PropertyComplexList key={regionCode} complexes={complexes} rows={rowsKey===queryKey?rows:[]} dataReady={rowsView.state==='ready'} trade={trade} onSelect={id=>{locateAfterSelection.current=id;setComplexId(id);setMapPointCandidate(false);setArea('');heading.current?.focus();const body=heading.current?.closest('aside')?.querySelector('.property-body');if(body)body.scrollTop=0;}}/></div>
        {!complexId&&<details className="property-region-records"><summary>월별 지역 거래량</summary><VolumeChart metrics={series}/><p className="property-caption">점선은 미수집 기간입니다. 최근 계약월은 추가 신고로 바뀔 수 있습니다.</p></details>}
        {complex?<div hidden={detailSection!=='trades'}><PropertyHistory detail={selectedDetail} complex={complex} origin={atlas.origin} month={month} trade={trade} area={area} onArea={setArea} range={historyRange} onRange={setHistoryRange} includeReview={showCancelled}/></div>:<details className="property-region-records"><summary>지역 전체 거래 분포·원문</summary>
        <label className="property-search">전용면적<select value={area} onChange={e=>setArea(e.target.value)}><option value="">전체 면적 · 가격 비교 시 면적을 선택하세요</option>{propertyAreaOptions(areas,area,rowsView.state==='ready').map(option=><option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        <h3>{trade==='sale'?'실거래 가격':'임대차 보증금'} <small>원</small></h3>
        {rowsView.state==='ready'?<PriceChart rows={displayed} trade={trade}/>:<p className="property-empty" role={rowsView.state==='loading'?'status':undefined}>{rowsView.message}</p>}
        {showCancelled&&<p className="property-caption">차트·표에 검토·취소 기록을 포함하고 있습니다. <button onClick={()=>setShowCancelled(false)}>유효 거래만 보기</button></p>}
        <p className="property-caption">각 점은 계약 한 건입니다.{displayed.length>1000?' 화면에는 최근 1,000건을 표시하며 전체 기록은 표·CSV에서 확인할 수 있습니다.':''}{trade==='rent'?' 보증금과 월세는 함께 확인하세요.':''} 거래 구성의 변화가 가격 상승률을 뜻하지 않습니다.</p>
        </details>}
        {complex&&<section hidden={detailSection!=='info'} className="complex-facts compact complex-information" aria-label="공식 단지 정보"><h3>단지 기본정보</h3><dl><div><dt>단지명</dt><dd>{complex.name}</dd></div><div><dt>지역</dt><dd>{region.name}</dd></div><div><dt>법정동·지번</dt><dd>{[complex.legal_dong_name,complex.lot_number].filter(Boolean).join(' ')||'원문 미제공'}</dd></div><div><dt>건축연도</dt><dd>{complex.build_year??'원문 미제공'}</dd></div><div><dt>공식 식별번호</dt><dd>{complex.source_complex_id}</dd></div></dl><p className="property-caption">국토교통부 실거래 신고 원문 기준입니다. 건축연도는 입주 예정일과 구분합니다.</p><p>{[complex.legal_dong_name,complex.lot_number].filter(Boolean).join(' ')} <span>· {complex.build_year===null?'건축연도 미제공':`${complex.build_year}년 건축`}</span></p>{complex.position?<button onClick={()=>onLocate({id:complex.id,name:complex.name,region:region.name,lon:complex.position!.longitude,lat:complex.position!.latitude,range:1300})}>지도에서 보기 ↗</button>:<details className={providerPointLinked?'property-coordinate-note':undefined}><summary>{providerPointLinked?'서울시 제공 점 · 좌표 정확도 검토 중':'지역 지도 표시 중 · 단지 좌표 미연결'}</summary><p>{providerPointLinked?'서울시 단지 점과 국토부 실거래 ID를 유일한 도로명주소·단지명으로 연결했습니다. 지도 점의 좌표계와 위치 의미는 아직 검증 중이며, 실거래 단지의 확정 좌표로 취급하지 않습니다.':'공식 좌표가 확인되지 않아 단지 위치를 임의로 표시하지 않습니다.'}</p></details>}</section>}
        <div hidden={!!complex} className="property-comparison-action">{complex?<button className="compare-region" disabled={compareIds.length>=3&&!compareIds.includes(complex.id)} aria-pressed={compareIds.includes(complex.id)} onClick={()=>setCompareIds(previous=>previous.includes(complex.id)?previous.filter(id=>id!==complex.id):[...previous,complex.id])}>{compareIds.includes(complex.id)?'비교에 담음 ✓':'단지 비교에 담기 +'}</button>:<button className="compare-region" aria-pressed={compare.includes(regionCode)} disabled={compare.length>=3&&!compare.includes(regionCode)} onClick={()=>toggleCompare(regionCode)}>지역 비교 {compare.includes(regionCode)?'✓':'+'}</button>}</div>
        <button className="expert-toggle" aria-expanded={expert} onClick={()=>setExpert(v=>!v)}>{monthLabel(month)} 원문·취소 검토 {expert?'접기':'자세히 보기'}</button>
        {expert&&<><div className="property-table-tools"><label><input type="checkbox" checked={showCancelled} onChange={e=>setShowCancelled(e.target.checked)}/> 검토·취소 기록 포함</label><button onClick={download} disabled={rowsView.state!=='ready'||!displayed.length}>CSV</button></div>{rowsView.state==='ready'?<><p className="property-caption">{rowsView.count!.toLocaleString()}건 · {area?`${area}㎡`:'전체 면적'} · 금액 단위 원</p><div className="transaction-table"><table><thead><tr><th>계약일</th><th>면적/층</th><th>{trade==='sale'?'매매가':'보증금 / 월세'}</th><th>상태</th></tr></thead><tbody>{displayed.slice(0,limit).map(r=><tr key={r.id}><td>{r.contract_date}<small>{r.complex_name}</small></td><td>{r.area_m2}㎡<small>{r.floor??'미상'}층</small></td><td>{moneyLabel(trade==='sale'?r.price_krw:r.deposit_krw)}{trade==='rent'&&<small>월 {moneyLabel(r.monthly_rent_krw)}</small>}</td><td>{r.cancellation==='cancelled'?'해제':r.quality==='invalid'?'검토':r.trade_type==='rent'?'신고':'유효'}<small>{r.registration_date?`등기 ${r.registration_date}`:''}</small></td></tr>)}</tbody></table></div>{displayed.length>limit&&<button className="load-more" onClick={()=>setLimit(v=>v+60)}>거래 60건 더 보기</button>}</>:<p className="property-empty">{rowsView.message}</p>}</>}
      </>}
    </>}
    {comparison.length>0&&<section className="region-comparison" aria-label="지역 비교"><h3>지역 비교 <small>같은 계약월 · {monthLabel(atlas.property.period.latest_complete_month)}</small></h3><table><thead><tr><th>지역</th><th>거래량</th><th>제외</th></tr></thead><tbody>{comparison.map(row=><tr key={row.lawd_code}><td><button onClick={()=>chooseRegion(row)}>{row.name}</button></td><td>{metricCount(row.latest[trade])?.toLocaleString('ko-KR')??'미수집'}</td><td><button aria-label={`${row.name} 비교 제외`} onClick={()=>toggleCompare(row.lawd_code)}>×</button></td></tr>)}</tbody></table></section>}
    <div hidden={!!complex&&detailSection!=='compare'} className="comparison-workspace">{complex&&compareIds.length===0&&<div className="comparison-empty"><h3>관심 단지를 나란히 비교하세요</h3><p>위의 비교 담기로 최대 3개 단지를 고른 뒤 같은 계약월·전용면적의 신고 거래를 비교합니다.</p><button onClick={()=>setCompareIds([complex.id])}>이 단지를 비교에 담기</button></div>}{complex&&compareIds.length>0&&!compareComplexes.length&&!compareError&&<p role="status">비교 단지의 자료를 불러오고 있습니다…</p>}{compareComplexes.length>0&&<ComplexComparison atlas={atlas} items={compareComplexes} month={month} trade={trade} area={area} onArea={setArea} onRemove={id=>setCompareIds(previous=>previous.filter(row=>row!==id))}/>}</div>
    <details className="property-provenance"><summary>출처와 자료 범위</summary><p>전국 목표 {atlas.property.coverage.expected.toLocaleString()}개 지역·월·거래유형 중 {(atlas.property.coverage.complete+atlas.property.coverage.empty).toLocaleString()}개 수집 완료.</p><p>현행 {atlas.regions.regions.length}개 지역 코드 조회 기준입니다. 개편 전 코드 대응은 미반영 상태입니다. 경계와 법정동 코드의 기준일은 다를 수 있습니다.</p>{atlas.property.sources.map(source=><a key={source.id} href={source.page_url} target="_blank" rel="noreferrer">{source.label} ↗</a>)}<small>자료 생성 {new Date(atlas.property.generated_at).toLocaleString('ko-KR')} · {atlas.property.release_id}</small></details>
    </>}
    </div>
  </aside>;
}
