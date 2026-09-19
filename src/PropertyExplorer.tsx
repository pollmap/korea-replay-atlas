import {useEffect,useMemo,useRef,useState} from 'react';
import type {Place} from '../shared/contracts';
import {parsePropertyComplexes,parsePropertyRegionDetail,parsePropertyTransactions,type PropertyComplex,type PropertyRegion,type PropertyRegionDetail,type PropertyTransaction,type RegionMetric} from '../shared/property';
import {metricCount,moneyLabel,monthLabel,propertyStatus,transactionCsv,transactionRows,readPropertyView,type PropertyViewState} from '../shared/property-view';
import {fetchPinnedJson} from './atlas-client';
import type {AtlasContent} from './useAtlas';
import ComplexComparison from './ComplexComparison';

export type {PropertyViewState} from '../shared/property-view';
interface Props {atlas:AtlasContent;hidden:boolean;onClose:()=>void;onLocate:(place:Place)=>void;onViewState:(state:PropertyViewState)=>void;}
function VolumeChart({metrics}:{metrics:RegionMetric[]}){
  const data=metrics.slice(-24),maximum=Math.max(1,...data.map(r=>metricCount(r)??0)),w=300,h=105,step=w/Math.max(1,data.length);
  return <svg className="volume-chart" viewBox={`0 0 ${w} ${h+22}`} role="img" aria-label="월별 신고 거래량. 점선은 수집되지 않은 기간이며 0건과 다릅니다."><title>월별 신고 거래량</title>{data.map((row,i)=>{const count=metricCount(row),height=count===null?0:count/maximum*(h-18);return <g key={row.deal_month}><title>{monthLabel(row.deal_month)} {count===null?propertyStatus(row.status):`${count}건`}</title>{count===null?<line x1={i*step+2} x2={(i+1)*step-2} y1={h-1} y2={h-1} stroke="#a8b5af" strokeDasharray="2 2"/>:<rect x={i*step+2} y={h-height} width={Math.max(1,step-4)} height={Math.max(1,height)} rx="1" fill="#3c7d66"/>}{(i===0||i===data.length-1||i%6===0)&&<text x={i*step+step/2} y={h+16} textAnchor={i===0?'start':i===data.length-1?'end':'middle'}>{row.deal_month.slice(2,4)}.{row.deal_month.slice(4)}</text>}</g>})}</svg>;
}
function PriceChart({rows,trade}:{rows:PropertyTransaction[];trade:'sale'|'rent'}){
  const points=rows.filter(r=>r.contract_date&&(trade==='sale'?r.price_krw:r.deposit_krw)!==null),values=points.map(r=>(trade==='sale'?r.price_krw:r.deposit_krw)!),min=Math.min(...values),max=Math.max(...values);
  if(!points.length)return <p className="property-empty">선택한 조건에 표시할 거래가 없습니다.</p>;
  return <svg className="price-chart" viewBox="0 0 300 140" role="img" aria-label={`${trade==='sale'?'실거래 가격':'임대차 보증금'} 분포. 개별 계약별 점이며 미래 가격 예측이 아닙니다.`}><title>{trade==='sale'?'매매 가격':'임대차 보증금'} · 동일 전용면적을 선택해 비교하세요</title><text x="4" y="13">{moneyLabel(max)}원</text><text x="4" y="134">{moneyLabel(min)}원</text><line x1="4" y1="115" x2="296" y2="115" stroke="#dce5dd"/>{points.slice(0,1000).map(row=>{const date=Number(row.contract_date!.slice(-2)),value=(trade==='sale'?row.price_krw:row.deposit_krw)!;return <circle key={row.id} cx={12+(date-1)/30*278} cy={max===min?70:105-(value-min)/(max-min)*78} r="3.3" fill="#307958" opacity=".7"><title>{row.contract_date} · {row.area_m2}㎡ · {row.floor??'미상'}층 · {moneyLabel(value)}원</title></circle>;})}<text x="10" y="134">1일</text><text x="290" y="134" textAnchor="end">31일</text></svg>;
}
export default function PropertyExplorer({atlas,hidden,onClose,onLocate,onViewState}:Props){
  const [initial]=useState(()=>readPropertyView(location.hash,atlas.property.period)),[regionCode,setRegionCode]=useState(initial.region),[trade,setTrade]=useState<'sale'|'rent'>(initial.trade),[month,setMonth]=useState(initial.month),[complexId,setComplexId]=useState(initial.complex),[area,setArea]=useState(initial.area),[compare,setCompare]=useState(initial.compare);
  const [filter,setFilter]=useState(''),[detail,setDetail]=useState<PropertyRegionDetail|null>(null),[complexes,setComplexes]=useState<PropertyComplex[]>([]),[rows,setRows]=useState<PropertyTransaction[]>([]),[error,setError]=useState(''),[rowError,setRowError]=useState(''),[compareError,setCompareError]=useState(''),[busy,setBusy]=useState(false),[rowBusy,setRowBusy]=useState(false),[expert,setExpert]=useState(false),[showCancelled,setShowCancelled]=useState(false),[sheet,setSheet]=useState<'peek'|'half'|'full'>('half'),[limit,setLimit]=useState(60);
  const heading=useRef<HTMLHeadingElement>(null),historyView=useRef<PropertyViewState|null>(null),restoring=useRef(false);
  const [compareComplexes,setCompareComplexes]=useState<PropertyComplex[]>([]);
  const [compareIds,setCompareIds]=useState(initial.compareComplexes);
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
  },[atlas,compareIds]);
  const region=atlas.regions.regions.find(r=>r.lawd_code===regionCode)??null;
  useEffect(()=>{
    const next={region:regionCode,trade,month,complex:complexId,area,compare,compareComplexes:compareIds};onViewState(next);
    const previous=historyView.current;historyView.current=next;
    if(!previous||restoring.current){restoring.current=false;return;}
    const url=new URL(location.href),params=new URLSearchParams(url.hash.slice(1));
    for(const [key,value] of Object.entries({regionCode,trade,month,complex:complexId,area,compareRegions:compare.join(','),compareComplexes:next.compareComplexes.join(','),propertyRelease:atlas.property.release_id}))if(value)params.set(key,value);else params.delete(key);
    url.hash=params.toString();
    if(previous.region!==regionCode||previous.complex!==complexId)history.pushState({property:true},'',url);else history.replaceState({property:true},'',url);
  },[regionCode,trade,month,complexId,area,compare,compareIds,onViewState,atlas.property.release_id]);
  useEffect(()=>{const back=()=>{const next=readPropertyView(location.hash,atlas.property.period);restoring.current=true;setRegionCode(next.region);setTrade(next.trade);setMonth(next.month);setComplexId(next.complex);setArea(next.area);setCompare(next.compare);setCompareIds(next.compareComplexes);};window.addEventListener('popstate',back);return()=>window.removeEventListener('popstate',back);},[atlas.property.period]);
  useEffect(()=>{
    if(!region)return;const controller=new AbortController();setBusy(true);setError('');setDetail(null);setRows([]);setComplexes([]);
    void(async()=>{const next=parsePropertyRegionDetail(await fetchPinnedJson(region.index,atlas.origin,controller.signal));if(next.release_id!==atlas.property.release_id||next.lawd_code!==region.lawd_code)throw new Error('선택 지역과 자료 버전이 다릅니다.');if(controller.signal.aborted)return;setDetail(next);if(next.complexes){const result=parsePropertyComplexes(await fetchPinnedJson(next.complexes,atlas.origin,controller.signal));if(result.release_id!==next.release_id||result.lawd_code!==next.lawd_code)throw new Error('단지 목록의 지역이 다릅니다.');if(!controller.signal.aborted)setComplexes(result.complexes);}})().catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:'지역 자료 오류');}).finally(()=>{if(!controller.signal.aborted)setBusy(false);});
    return()=>controller.abort();
  },[region,atlas.origin,atlas.property.release_id]);
  useEffect(()=>{
    if(!detail||detail.lawd_code!==regionCode)return;const partition=detail.partitions.find(p=>p.deal_month===month&&p.trade_type===trade),controller=new AbortController();setRows([]);setRowError('');setRowBusy(true);setLimit(60);
    if(!partition||!['complete','empty'].includes(partition.status)){setRowBusy(false);return()=>controller.abort();}
    void Promise.all(partition.transactions.map(async ref=>{const result=parsePropertyTransactions(await fetchPinnedJson(ref,atlas.origin,controller.signal));if(result.release_id!==atlas.property.release_id||result.lawd_code!==regionCode||result.deal_month!==month)throw new Error('거래 자료의 지역·기간·버전이 다릅니다.');return result.transactions;})).then(parts=>{if(!controller.signal.aborted)setRows(parts.flat());}).catch(e=>{if(!controller.signal.aborted)setRowError(e instanceof Error?e.message:'거래 자료 오류');}).finally(()=>{if(!controller.signal.aborted)setRowBusy(false);});return()=>controller.abort();
  },[detail,regionCode,month,trade,atlas.origin,atlas.property.release_id]);
  const availableRegions=useMemo(()=>atlas.regions.regions.filter(r=>!filter||r.name.includes(filter)||r.lawd_code.includes(filter)).sort((a,b)=>(metricCount(b.latest[trade])??-1)-(metricCount(a.latest[trade])??-1)||a.name.localeCompare(b.name,'ko')),[atlas.regions.regions,filter,trade]);
  const displayed=useMemo(()=>transactionRows(rows,{trade,complex:complexId||null,area,cancelled:expert&&showCancelled}),[rows,trade,complexId,area,expert,showCancelled]);
  const areas=useMemo(()=>[...new Set(rows.filter(r=>r.trade_type===trade&&(!complexId||r.complex_id===complexId)&&r.area_m2).map(r=>r.area_m2!))].sort((a,b)=>Number(a)-Number(b)),[rows,trade,complexId]);
  const complex=complexes.find(c=>c.id===complexId),series=detail?.metrics.filter(m=>m.trade_type===trade).sort((a,b)=>a.deal_month.localeCompare(b.deal_month))??[],current=series.find(m=>m.deal_month===month);
  const comparison=compare.flatMap(code=>atlas.regions.regions.find(r=>r.lawd_code===code)??[]);
  const chooseRegion=(row:PropertyRegion)=>{setRegionCode(row.lawd_code);setComplexId('');setArea('');setError('');setSheet('half');heading.current?.focus();};
  const toggleCompare=(code:string)=>setCompare(previous=>previous.includes(code)?previous.filter(v=>v!==code):previous.length<3?[...previous,code]:previous);
  const months=series.map(row=>row.deal_month),total=atlas.regions.regions.reduce((sum,r)=>sum+(metricCount(r.latest[trade])??0),0),collected=atlas.regions.regions.filter(r=>metricCount(r.latest[trade])!==null).length;
  const download=()=>{const url=URL.createObjectURL(new Blob([transactionCsv(displayed)],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download=`korea-replay-${regionCode}-${month}-${trade}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  return <aside className={`property-panel sheet-${sheet}`} hidden={hidden} aria-label="지역·아파트 분석">
    <div className="sheet-handle"><button aria-label={sheet==='peek'?'지역 분석 펼치기':sheet==='half'?'지역 분석 전체 높이':'지역 분석 접기'} onClick={()=>setSheet(sheet==='peek'?'half':sheet==='half'?'full':'peek')}><span/></button></div>
    <header className="property-header"><div><span className="eyebrow">지역·아파트 분석</span><h2 ref={heading} tabIndex={-1}>{complex?.name??region?.name??'대한민국 부동산'}</h2></div><button aria-label="지역 분석 닫기" onClick={onClose}>×</button></header>
    <div className="property-body">
    <div className="property-trade" role="group" aria-label="거래 유형"><button aria-pressed={trade==='sale'} onClick={()=>{setTrade('sale');setArea('');}}>매매</button><button aria-pressed={trade==='rent'} onClick={()=>{setTrade('rent');setArea('');}}>전월세</button></div>
    <p className="property-period">{monthLabel(region?month:atlas.property.period.latest_complete_month)} 계약 기준 · 공식 신고 기록{month===atlas.property.period.to&&region?' · 당월 잠정':''}</p>
    {(error||rowError||compareError)&&<p role="alert" className="property-error">{error||rowError||compareError}</p>}{regionCode&&!region&&<p role="alert">선택 지역은 이 자료 버전에 없습니다. 최신 지역으로 자동 대체하지 않습니다.</p>}{complexId&&!busy&&detail&&!complex&&<p role="alert">선택 단지는 이 자료 버전에 없습니다.</p>}
    {!region?<>
      <div className="property-stat"><span>{collected===atlas.regions.regions.length?'전국':'수집 완료 지역'} 거래량</span><strong>{total.toLocaleString('ko-KR')}<small>건</small></strong><span>{collected} / {atlas.regions.regions.length}개 지역 확인</span></div>
      <label className="property-search">지역 찾기<input value={filter} placeholder="시·군·구 이름" onChange={e=>setFilter(e.target.value)}/></label>
      <div className="property-list-heading"><span>지역</span><span>거래량 · 건</span></div>
      <ol className="region-results">{availableRegions.map(row=><li key={row.lawd_code}><button className="region-open" onClick={()=>chooseRegion(row)}><span>{row.name}</span><strong>{metricCount(row.latest[trade])?.toLocaleString('ko-KR')??propertyStatus(row.latest[trade].status)}</strong></button><button className="compare-add" aria-label={`${row.name} 비교 ${compare.includes(row.lawd_code)?'해제':'추가'}`} aria-pressed={compare.includes(row.lawd_code)} disabled={compare.length>=3&&!compare.includes(row.lawd_code)} onClick={()=>toggleCompare(row.lawd_code)}>{compare.includes(row.lawd_code)?'✓':'+'}</button></li>)}</ol>
    </>:<>
      <button className="property-back" onClick={()=>{if(complexId){setComplexId('');setArea('');}else setRegionCode('');}}>{complexId?'← 지역 분석':'← 전국 지역 목록'}</button>
      {busy?<p role="status">지역 자료를 확인하고 있습니다…</p>:detail&&<>
        <div className="property-select-row"><label>계약월<select value={month} onChange={e=>setMonth(e.target.value)}>{[...new Set(months)].reverse().map(m=><option value={m} key={m}>{monthLabel(m)}{m===detail.period.to?' · 잠정':''}</option>)}</select></label><button className="compare-region" aria-pressed={compare.includes(regionCode)} disabled={compare.length>=3&&!compare.includes(regionCode)} onClick={()=>toggleCompare(regionCode)}>지역 비교 {compare.includes(regionCode)?'✓':'+'}</button></div>
        <div className="property-stat"><span>{complexId?'선택 단지·면적 신고 거래량':'지역 전체 신고 거래량'}</span><strong>{current&&metricCount(current)!==null?(complexId?(rowBusy||rowError||!complex?'—':transactionRows(rows,{trade,complex:complexId,area,cancelled:false}).length.toLocaleString('ko-KR')):metricCount(current)!.toLocaleString('ko-KR')):'—'}<small>건</small></strong><span>{current?propertyStatus(current.status):'자료 없음'} · {trade==='sale'?'취소·취소 여부 미확인 제외':'원천에서 취소 여부 미제공'}</span></div>
        {!complexId&&<><h3>월별 거래량</h3><VolumeChart metrics={series}/><p className="property-caption">점선은 미수집 기간입니다. 최근 계약월은 추가 신고로 바뀔 수 있습니다.</p></>}
        <label className="property-search">단지 선택<select value={complexId} onChange={e=>{setComplexId(e.target.value);setArea('');}}><option value="">지역 전체 거래</option>{complexes.map(c=><option value={c.id} key={c.id}>{c.name} · {c.legal_dong_name??''}</option>)}</select></label>
        {complex&&<div className="complex-facts"><p>{[region.name,complex.legal_dong_name,complex.lot_number].filter(Boolean).join(' ')}</p><dl><dt>원천 건축연도</dt><dd>{complex.build_year??'미제공'}</dd><dt>단지 식별</dt><dd>국토부 공식 단지 ID</dd></dl>{complex.position?<button onClick={()=>onLocate({id:complex.id,name:complex.name,region:region.name,lon:complex.position!.longitude,lat:complex.position!.latitude,range:1300})}>지도에서 보기 ↗</button>:<p className="property-caption">공식 좌표 연결 전입니다. 거래 이름으로 위치를 추정하지 않습니다.</p>}</div>}
        <label className="property-search">전용면적<select value={area} onChange={e=>setArea(e.target.value)}><option value="">전체 면적 · 가격 비교 시 면적을 선택하세요</option>{areas.map(a=><option key={a} value={a}>{a} ㎡</option>)}</select></label>
        <h3>{trade==='sale'?'실거래 가격':'임대차 보증금'} <small>원</small></h3>
        {rowBusy?<p role="status">선택 월의 거래를 불러오는 중…</p>:rowError?<p className="property-empty">거래 자료를 불러오지 못했습니다. 거래 0건으로 표시하지 않습니다.</p>:<PriceChart rows={displayed} trade={trade}/>}
        <p className="property-caption">각 점은 계약 한 건입니다.{displayed.length>1000?' 화면에는 최근 1,000건을 표시하며 전체 기록은 표·CSV에서 확인할 수 있습니다.':''}{trade==='rent'?' 보증금과 월세는 함께 확인하세요.':''} 거래 구성의 변화가 가격 상승률을 뜻하지 않습니다.</p>
        {complex&&<button className="compare-region" disabled={compareIds.length>=3&&!compareIds.includes(complex.id)} aria-pressed={compareIds.includes(complex.id)} onClick={()=>setCompareIds(previous=>previous.includes(complex.id)?previous.filter(id=>id!==complex.id):[...previous,complex.id])}>단지 비교 {compareIds.includes(complex.id)?'✓':'+'}</button>}
        <button className="expert-toggle" aria-expanded={expert} onClick={()=>setExpert(v=>!v)}>거래 표·출처 {expert?'접기':'자세히 보기'}</button>
        {expert&&<><div className="property-table-tools"><label><input type="checkbox" checked={showCancelled} onChange={e=>setShowCancelled(e.target.checked)}/> 검토·취소 기록 포함</label><button onClick={download} disabled={!displayed.length}>CSV</button></div><p className="property-caption">{displayed.length.toLocaleString()}건 · {area?`${area}㎡`:'전체 면적'} · 금액 단위 원</p><div className="transaction-table"><table><thead><tr><th>계약일</th><th>면적/층</th><th>{trade==='sale'?'매매가':'보증금 / 월세'}</th><th>상태</th></tr></thead><tbody>{displayed.slice(0,limit).map(r=><tr key={r.id}><td>{r.contract_date}<small>{r.complex_name}</small></td><td>{r.area_m2}㎡<small>{r.floor??'미상'}층</small></td><td>{moneyLabel(trade==='sale'?r.price_krw:r.deposit_krw)}{trade==='rent'&&<small>월 {moneyLabel(r.monthly_rent_krw)}</small>}</td><td>{r.cancellation==='cancelled'?'해제':r.quality==='invalid'?'검토':r.trade_type==='rent'?'신고':'유효'}<small>{r.registration_date?`등기 ${r.registration_date}`:''}</small></td></tr>)}</tbody></table></div>{displayed.length>limit&&<button className="load-more" onClick={()=>setLimit(v=>v+60)}>거래 60건 더 보기</button>}</>}
      </>}
    </>}
    {comparison.length>0&&<section className="region-comparison" aria-label="지역 비교"><h3>지역 비교 <small>같은 계약월 · {monthLabel(atlas.property.period.latest_complete_month)}</small></h3><table><thead><tr><th>지역</th><th>거래량</th><th>제외</th></tr></thead><tbody>{comparison.map(row=><tr key={row.lawd_code}><td><button onClick={()=>chooseRegion(row)}>{row.name}</button></td><td>{metricCount(row.latest[trade])?.toLocaleString('ko-KR')??'미수집'}</td><td><button aria-label={`${row.name} 비교 제외`} onClick={()=>toggleCompare(row.lawd_code)}>×</button></td></tr>)}</tbody></table></section>}
    {compareComplexes.length>0&&<ComplexComparison atlas={atlas} items={compareComplexes} month={month} trade={trade} area={area} onArea={setArea} onRemove={id=>setCompareIds(previous=>previous.filter(row=>row!==id))}/>}
    <details className="property-provenance"><summary>출처와 자료 범위</summary><p>전국 목표 {atlas.property.coverage.expected.toLocaleString()}개 지역·월·거래유형 중 {(atlas.property.coverage.complete+atlas.property.coverage.empty).toLocaleString()}개 수집 완료.</p><p>경계와 법정동 코드의 기준일은 다를 수 있습니다. 과거 행정구역 개편의 코드 대응은 검토 중입니다.</p>{atlas.property.sources.map(source=><a key={source.id} href={source.page_url} target="_blank" rel="noreferrer">{source.label} ↗</a>)}<small>자료 생성 {new Date(atlas.property.generated_at).toLocaleString('ko-KR')} · {atlas.property.release_id}</small></details>
    </div>
  </aside>;
}
