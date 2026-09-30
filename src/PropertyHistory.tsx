import {rentKindLabel,rentKindMatches,type RentKind} from '../shared/property-rent';
import {PRICE_BASES,pricingSummary,transactionPrice,type PriceBasis} from '../shared/property-pricing';
import {exclusivePyeong,NATIONAL_AREA} from '../shared/property-area';
import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {historyChartData,historyChartSelection} from './property-history-chart';
import {HISTORY_TABLE_PAGE_SIZE,historyRecordWindow,historyTableWindow,historySelectionIndex,type HistoryTableState} from './property-history-selection';
import type {PropertyComplex,PropertyRegionDetail,PropertySource,PropertyTransaction} from '../shared/property';
import {HISTORY_RANGES,historyDay,historyMonths,historyPrice,historySummary,historyRangeLabel,historyTick,type HistoryRange} from '../shared/property-history';
import {moneyLabel,monthLabel,propertyAreaOptions,transactionCsv,transactionRows} from '../shared/property-view';
import {historySourceStart,historySourceCoverage} from '../shared/property-source-period';
import {loadPropertyHistory,type HistoryResult} from './property-history-loader';
import {historyMonthStatus,historyRefreshSummary,publishedHistoryCoverage,shiftHistoryEnd} from './property-history-state';
import {defaultDetailArea} from './property-desktop';
import {loadComplexPriceSummaries} from './property-summary-client';
import PropertyMonthlySummary,{type MonthlySummaryData} from './PropertyMonthlySummary';

export function PropertyRawHistory({detail,complex,origin,month,trade,area,onArea,range,onRange,includeReview,rentKind='all',onMonth,sources,autoArea=false,priceBasis,onPriceBasis,onAutomaticArea,tableOnly=false,embedded=false,highlightTransaction,onShowRecordChart}:{detail:PropertyRegionDetail;complex:PropertyComplex;origin:string;month:string;trade:'sale'|'rent';area:string;onArea:(area:string)=>void;range:HistoryRange;onRange:(value:HistoryRange)=>void;includeReview:boolean;rentKind?:RentKind;onMonth?:(month:string)=>void;sources?:readonly PropertySource[];autoArea?:boolean;priceBasis?:PriceBasis;onPriceBasis?:(basis:PriceBasis)=>void;onAutomaticArea?:(area:string)=>void;tableOnly?:boolean;embedded?:boolean;highlightTransaction?:string;onShowRecordChart?:(row:PropertyTransaction)=>void;summaryFirst?:boolean}){
  const [loaded,setLoaded]=useState<{key:string;months:HistoryResult[]}>({key:'',months:[]}),[error,setError]=useState(''),[attempt,setAttempt]=useState(0),[selected,setSelected]=useState(''),[tableState,setTableState]=useState<HistoryTableState>({scope:'',start:0,expanded:false});
  const key=`${detail.release_id}:${complex.id}:${month}:${trade}:${range}`;
  const recordButtons=useRef(new Map<string,HTMLButtonElement>()),pendingRecord=useRef<string|null>(null),pendingChart=useRef(false),chartRef=useRef<SVGSVGElement>(null);
  const resolvedArea=useRef(new Set<string>());
  const [localBasis,setLocalBasis]=useState<PriceBasis>('total');
  const basis=priceBasis??localBasis,setBasis=onPriceBasis??setLocalBasis;
  const [chartMode,setChartMode]=useState<'price'|'volume'>('price');
  useEffect(()=>{const controller=new AbortController();setLoaded({key,months:[]});setError('');setSelected('');setTableState({scope:'',start:0,expanded:false});
    void loadPropertyHistory({detail,end:month,count:range,trade,complex:complex.id,origin,sources,signal:controller.signal,onMonth:result=>setLoaded(previous=>previous.key===key?{key,months:[...previous.months,result]}:previous)}).catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'거래 이력 조회 실패');});
    return()=>controller.abort();
  },[detail,complex.id,origin,month,trade,range,key,attempt,sources]);
  const months=useMemo(()=>historyMonths(month,range),[month,range]);
  const sourceCoverage=historySourceCoverage(months,historySourceStart(trade,sources));
  const refresh=useMemo(()=>historyRefreshSummary(detail,trade,months),[detail,trade,months]);
  const published=useMemo(()=>publishedHistoryCoverage(detail,trade,months),[detail,trade,months]);
  const results=loaded.key===key?loaded.months:[],ready=results.filter(r=>r.status==='ready').length,pending=results.length<months.length&&!error;
  const raw=useMemo(()=>loaded.key===key?loaded.months.flatMap(r=>r.rows):[],[loaded,key]);
  const rows=useMemo(()=>transactionRows(raw,{trade,complex:complex.id,area,cancelled:includeReview,rentKind}),[raw,trade,complex.id,area,includeReview,rentKind]);
  const areas=useMemo(()=>[...new Set(raw.filter(r=>rentKindMatches(r,rentKind)&&r.area_m2).map(r=>r.area_m2!))],[raw,rentKind]);
  useEffect(()=>{
    const scope=`${detail.release_id}:${complex.id}:${trade}:${rentKind}`;
    if(!autoArea||pending||error||resolvedArea.current.has(scope)||!ready)return;
    resolvedArea.current.add(scope);
    if(!area){const chosen=defaultDetailArea(transactionRows(raw,{trade,complex:complex.id,area:'',cancelled:false,rentKind}));if(chosen)(onAutomaticArea??onArea)(chosen);}
  },[autoArea,pending,error,ready,area,raw,trade,complex.id,detail.release_id,rentKind,onArea,onAutomaticArea]);
  const summary=useMemo(()=>historySummary(transactionRows(raw,{trade,complex:complex.id,area,cancelled:false,rentKind})),[raw,trade,complex.id,area,rentKind]);
  const pricing=useMemo(()=>pricingSummary(raw,{complexId:complex.id,trade,area,rentKind}),[raw,complex.id,trade,area,rentKind]);
  const chartPrice=(row:PropertyTransaction)=>transactionPrice(row,basis);
  const unit=basis==='total'?'원':basis==='pyeong'?'원/전용평':'원/전용㎡';
  const volumes=useMemo(()=>{const counts=new Map<string,number>();for(const row of rows){if(row.contract_date){const period=row.contract_date.slice(0,7).replace('-','');counts.set(period,(counts.get(period)??0)+1);}}return counts;},[rows]);
  const maxVolume=Math.max(1,...volumes.values());
  const first=`${months[0].slice(0,4)}-${months[0].slice(4)}-01`,endDate=new Date(Date.UTC(Number(month.slice(0,4)),Number(month.slice(4)),0)).toISOString().slice(0,10),from=historyDay(first),to=historyDay(endDate);
  const chart=useMemo(()=>historyChartData(rows,from,to,row=>transactionPrice(row,basis)),[rows,from,to,basis]);
  const points=useMemo(()=>historyChartSelection(chart,selected),[chart,selected]);
  const chartSummary=chart,chosen=chart.byId.get(selected)||chart.byId.get(highlightTransaction??'')||points[0];
  const tableScope=JSON.stringify([key,area,includeReview,rentKind]);
  const table=historyTableWindow(tableState,tableScope,rows.length);
  useEffect(()=>{setTableState({scope:tableScope,start:0,expanded:false});pendingRecord.current=null;},[tableScope]);
  useEffect(()=>{if(highlightTransaction)setSelected(highlightTransaction);},[highlightTransaction]);
  const selectedIndex=historySelectionIndex(points,chosen?.id);
  const choose=(row:PropertyTransaction)=>setSelected(row.id);
  const focusRecord=useCallback((id:string)=>{const button=recordButtons.current.get(id);if(!button)return false;button.focus({preventScroll:true});button.scrollIntoView({block:'nearest',behavior:'instant'});return true;},[]);
  const showRecord=()=>{if(!chosen)return;const next=historyRecordWindow(rows,chosen.id,tableState,tableScope);if(next===null)return;setSelected(chosen.id);if(!focusRecord(chosen.id)){pendingRecord.current=chosen.id;setTableState(next);}};
  useEffect(()=>{const id=pendingRecord.current;if(id){pendingRecord.current=null;focusRecord(id);}},[table.start,table.end,rows,focusRecord]);
  const showChart=(row:PropertyTransaction)=>{choose(row);setChartMode('price');pendingChart.current=!chartRef.current;chartRef.current?.scrollIntoView({block:'nearest',behavior:'instant'});chartRef.current?.focus({preventScroll:true});};
  useEffect(()=>{if(pendingChart.current&&chartRef.current){pendingChart.current=false;chartRef.current.scrollIntoView({block:'nearest',behavior:'instant'});chartRef.current.focus({preventScroll:true});}},[chartMode]);
  const download=()=>{const url=URL.createObjectURL(new Blob([transactionCsv(rows)],{type:'text/csv;charset=utf-8'}));const anchor=document.createElement('a');anchor.href=url;anchor.download=`korea-replay-${complex.source_complex_id}-${months[0]}-${month}-${trade}.csv`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  return <section className="property-history" aria-label="단지 실거래 이력">
    <h3 className="sr-only">{trade==='sale'?'매매 실거래':'전월세 실거래'}</h3>
    {!embedded&&<>
    <div className="history-controls"><label className="property-search"><span className="sr-only">전용면적</span><select aria-label="이력 전용면적" value={area} onChange={event=>onArea(event.target.value)}><option value="">전체 면적</option>{propertyAreaOptions(areas,area,!pending&&ready>0&&ready===sourceCoverage.eligible).map(item=><option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label className="history-period-select"><span className="sr-only">실거래 조회 기간</span><select aria-label="실거래 조회 기간" value={range} onChange={event=>onRange(Number(event.target.value) as HistoryRange)}>{HISTORY_RANGES.map(value=><option key={value} value={value}>최근 {historyRangeLabel(value)}</option>)}</select></label></div>
    {error&&<p role="alert">{error}</p>}
    {!pending&&(error||results.some(row=>row.status==='error'))&&<button className="history-retry" onClick={()=>setAttempt(value=>value+1)}>실패한 이력 다시 확인</button>}
    <div className="history-summary"><div className="history-main-price"><span>{trade==='rent'?`${rentKindLabel(rentKind)} 보증금`:area?'매매 실거래가':'매매 실거래가 · 면적 혼합'}</span><strong>{summary.latest?moneyLabel(historyPrice(summary.latest)):'—'}</strong><small>{summary.latest?`${summary.latest.contract_date} · ${summary.latest.area_m2}㎡ · ${summary.latest.floor??'미상'}층`:pending?'거래를 불러오는 중':'확인된 거래 없음'}</small>{trade==='rent'&&summary.latest&&<small>{summary.latest.monthly_rent_krw===null?'월세 미확인':summary.latest.monthly_rent_krw===0?'월세 없음':`월세 ${moneyLabel(summary.latest.monthly_rent_krw)}원`}</small>}</div><div className="history-total"><span>기간 내 거래</span><strong>{ready?summary.count.toLocaleString():'—'}<small>건</small></strong></div></div>

    <p className="history-coverage" role="status">{monthLabel(months[0])}–{monthLabel(month)} · {ready}/{sourceCoverage.eligible}개월 확인{sourceCoverage.before>0?` · 원천 자료 제공 전 ${sourceCoverage.before}개월`:''}{pending?' · 불러오는 중':''}{ready<sourceCoverage.eligible&&!pending?' · 미확인 기간 있음':''}{!area?' · 면적 혼합':''}</p>
    <div className="history-chart-toolbar"><div className="history-chart-modes" role="group" aria-label="차트 표시"><button aria-pressed={chartMode==='price'} onClick={()=>setChartMode('price')}>{trade==='rent'?'보증금':'거래 가격'}</button><button aria-pressed={chartMode==='volume'} onClick={()=>setChartMode('volume')}>월별 거래량</button></div>
    {chartMode==='price'&&<label className="chart-basis-select"><span className="sr-only">가격 표시 기준</span><select aria-label="가격 표시 기준" value={basis} onChange={event=>setBasis(event.target.value as PriceBasis)}>{PRICE_BASES.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>}
    </div>
    </>}
    {!tableOnly&&<>
    {embedded&&<div className="history-table-title"><h3>{monthLabel(month)} 원문 거래 차트</h3></div>}
    {chartMode==='price'&&chart.rows.length>points.length&&<p className="history-sampling" role="status">전체 {chart.rows.length.toLocaleString()}건 중 기간별 대표거래 {points.length.toLocaleString()}건 표시 · 전체는 표/CSV</p>}
    {chartMode==='volume'&&<svg className="history-chart history-volume" viewBox="0 0 320 180" role="img" aria-label="선택 단지의 월별 신고 거래량. 회색 구간은 미확인 기간입니다."><text x="8" y="13">최대 {maxVolume}건</text>{months.map((value,i)=>{const result=results.find(row=>row.month===value),known=result?.status==='ready',count=volumes.get(value)??0,step=296/months.length,x=12+i*step,h=count/maxVolume*118;return <g key={value}><title>{monthLabel(value)} · {known?`${count}건`:historyMonthStatus(result)}</title>{known?<rect x={x+step*.1} y={142-h} width={Math.max(.7,step*.8)} height={Math.max(1,h)} fill="var(--trade-color,#5145cd)" rx={months.length<=12?2:0}/>:<rect x={x} y="24" width={step} height="120" fill="#eef1f5"/>}{historyTick(i,months.length)&&<text x={i===months.length-1?308:x} y="169" textAnchor={i===months.length-1?'end':'start'}>{monthLabel(value)}</text>}</g>;})}</svg>}
    {chartMode==='price'&&!!points.length&&<><svg ref={chartRef} tabIndex={-1} className="history-chart" viewBox="0 0 320 180" role="group" aria-label="기간별 실거래 점. 점을 선택하면 계약 정보를 확인합니다.">
      <text x="8" y="13">{moneyLabel(chartSummary.max)}{unit}</text><text x="8" y="145">{moneyLabel(chartSummary.min)}{unit}</text>{[30,84,138].map(y=><line key={y} x1="12" y1={y} x2="308" y2={y} stroke="#e7ecf4" strokeDasharray="3 4"/>)}<line x1="12" y1="150" x2="308" y2="150" stroke="#dce3ec"/>
      {months.map((value,i)=>{const result=results.find(r=>r.month===value),start=historyDay(`${value.slice(0,4)}-${value.slice(4)}-01`),x=12+(start-from)/Math.max(1,to-from)*296;return <g key={value}>{result?.status!=='ready'&&<rect x={x} y="23" width={296/months.length} height="120" fill="#eef1ee"><title>{monthLabel(value)} {historyMonthStatus(result)}</title></rect>}{historyTick(i,months.length)&&<text x={Math.min(308,x)} y="170" textAnchor={i===months.length-1?'end':'start'}>{monthLabel(value)}</text>}</g>;})}
      {points.map(row=>{const x=12+(historyDay(row.contract_date!)-from)/Math.max(1,to-from)*296,y=chartSummary.max===chartSummary.min?85:138-(chartPrice(row)!-chartSummary.min!)/(chartSummary.max!-chartSummary.min!)*108,label=`${row.contract_date} · ${row.area_m2}㎡ · ${row.floor??'미상'}층 · ${moneyLabel(chartPrice(row))}${unit}${trade==='rent'?` · 월 ${moneyLabel(row.monthly_rent_krw)}원`:''}${row.cancellation==='cancelled'?' · 해제':''}`;return <circle key={row.id} cx={x} cy={y} r={row.id===chosen?.id?5:3.8} fill={row.cancellation==='cancelled'?'#a07963':'#247a5a'} opacity=".85" tabIndex={0} role="button" aria-label={label} onFocus={()=>choose(row)} onClick={()=>choose(row)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(row);}}}><title>{label}</title></circle>;})}
    </svg>{chosen&&<div className="history-selection-tools" role="group" aria-label="선택 거래 탐색"><button disabled={selectedIndex<0||selectedIndex>=points.length-1} onClick={()=>choose(points[selectedIndex+1])}>← 이전 거래</button><button onClick={showRecord}>계약 기록 보기</button><button disabled={selectedIndex<=0} onClick={()=>choose(points[selectedIndex-1])}>다음 거래 →</button></div>}{chosen&&<p className="history-point" aria-live="polite">{chosen.contract_date} · {chosen.area_m2}㎡ · {chosen.floor??'미상'}층 <strong>{moneyLabel(chartPrice(chosen))}{unit}</strong>{trade==='rent'&&` · 월 ${moneyLabel(chosen.monthly_rent_krw)}원`}{chosen.cancellation==='cancelled'?' · 해제':''}</p>}</>}
    {!points.length&&!pending&&<p className="property-empty">{rows.length?'기록은 있으나 차트에 표시할 기간 내 금액·계약일이 없습니다.':ready?'확인된 기간에 선택 조건의 신고 거래가 없습니다.':'선택 기간의 거래를 확인하지 못했습니다.'}</p>}
    </>}
    {embedded&&(pending||!ready)&&<p className="property-caption" role={results.some(result=>result.status==='error')?'alert':'status'}>{pending?'선택 월 원문을 불러오는 중…':historyMonthStatus(results[0],detail.partitions.find(partition=>partition.deal_month===month&&partition.trade_type===trade))}</p>}
    {embedded&&!pending&&ready>0&&rows.length===0&&<p className="property-empty">선택 월에 해당 조건의 유효 계약이 없습니다.</p>}
    <div className="history-table-title"><h3>{embedded?`${monthLabel(month)} 계약 기록`:'계약 기록'}</h3><button onClick={download} disabled={!rows.length||pending}>{embedded?`${monthLabel(month)} CSV`:'CSV 내려받기'}</button></div>
    <div className="transaction-table"><table><thead><tr><th>계약일·층</th><th>전용면적</th><th>{trade==='sale'?'매매가':'보증금 / 월세'}</th></tr></thead><tbody>{rows.slice(table.start,table.end).map(row=><tr key={row.id} className={chosen?.id===row.id?'history-selected':''}><td>{chart.byId.has(row.id)?<button className="history-record-link" ref={button=>{if(button)recordButtons.current.set(row.id,button);else recordButtons.current.delete(row.id);}} aria-pressed={chosen?.id===row.id} aria-label={`${row.contract_date} · ${row.area_m2}㎡ · ${row.floor??'미상'}층 · ${moneyLabel(historyPrice(row))} ${tableOnly?'해당 월 차트에서 보기':'차트에서 보기'}`} onClick={()=>tableOnly&&onShowRecordChart?onShowRecordChart(row):showChart(row)}>{row.contract_date}</button>:row.contract_date}<small>{row.floor??'미상'}층{row.cancellation==='cancelled'?' · 해제':row.quality==='invalid'?' · 검토':''}</small>{row.registration_date&&<small>등기 {row.registration_date}</small>}</td><td>{row.area_m2}㎡<small>전용 {exclusivePyeong(row.area_m2)}평</small></td><td>{moneyLabel(historyPrice(row))}<small>전용평당 {moneyLabel(transactionPrice(row,'pyeong'))}원</small>{trade==='rent'&&<small>월 {moneyLabel(row.monthly_rent_krw)}</small>}</td></tr>)}</tbody></table></div>
    {!table.expanded&&rows.length>12&&<button className="load-more" onClick={()=>setTableState({scope:tableScope,start:0,expanded:true})}>계약 기록 더 보기 (12/{rows.length})</button>}
    {table.expanded&&<nav className="history-table-pages" aria-label="계약 기록 페이지"><button disabled={table.start===0} onClick={()=>setTableState({scope:tableScope,start:Math.max(0,table.start-HISTORY_TABLE_PAGE_SIZE),expanded:true})}>이전 페이지</button><span role="status">{rows.length?table.start+1:0}–{table.end} / {rows.length}건</span><button disabled={table.end>=rows.length} onClick={()=>setTableState({scope:tableScope,start:table.start+HISTORY_TABLE_PAGE_SIZE,expanded:true})}>다음 페이지</button></nav>}
    {!embedded&&<>
    <div className="history-secondary-tools">
    {refresh.count>0&&<p className="property-caption" role="status">이전 확인본 유지 {refresh.count}개월 · 월별 마지막 성공(UTC) {refresh.firstSuccess}{refresh.lastSuccess!==refresh.firstSuccess?`–${refresh.lastSuccess}`:''}</p>}
    {onMonth&&<div className="property-table-tools" aria-label="거래 기간 이동"><button disabled={!published.first||months[0]<=published.first} onClick={()=>onMonth(shiftHistoryEnd(month,range,-1))}>← 이전 {historyRangeLabel(range)}</button><button disabled={month>=detail.period.latest_complete_month} onClick={()=>onMonth([shiftHistoryEnd(month,range,1),detail.period.latest_complete_month].sort()[0])}>다음 {historyRangeLabel(range)} →</button><button disabled={month===detail.period.latest_complete_month} onClick={()=>onMonth(detail.period.latest_complete_month)}>최근 계약월</button></div>}
    <details className="pricing-method"><summary>월별 자료 확보 상태 · 게시 {published.count}/{sourceCoverage.eligible}개월</summary><p className="property-caption">{published.first&&published.last?`이 지역의 게시 자료: ${monthLabel(published.first)}–${monthLabel(published.last)}. 중간에 미수집 월이 있을 수 있습니다.`:'이 거래 유형의 게시 자료가 없습니다.'} 기간 선택과 실제 자료 확보는 다릅니다. 미게시·조회 실패는 거래 0건이 아닙니다.</p><div className="transaction-table"><table><thead><tr><th>계약월</th><th>자료 상태</th></tr></thead><tbody>{[...months].reverse().map(value=><tr key={value}><th>{monthLabel(value)}</th><td>{error&&!results.some(result=>result.month===value)?'조회 중단 · 재시도 가능':historyMonthStatus(results.find(result=>result.month===value),detail.partitions.find(partition=>partition.deal_month===value&&partition.trade_type===trade))}</td></tr>)}</tbody></table></div></details>
    {results.some(result=>['download_budget','request_budget','retention_budget'].includes(result.reason??''))&&<p className="property-caption">한 번에 최근 자료부터 24MiB·512개 파일·단지 거래 20,000건까지 확인합니다. 일부 기간은 조회 한도에 도달했습니다. 이전 거래는 계약월을 과거로 바꿔 확인하세요. 회색 기간은 거래 0건을 뜻하지 않습니다.</p>}
    </div>
    <div className="pricing-cards" aria-label="가격 비교 지표"><div><span>{trade==='sale'?'전용 평당가 중앙값':'전용 평당 보증금 중앙값'}</span><strong>{pricing.perPyeong===null?'—':`${moneyLabel(pricing.perPyeong)}원`}</strong><small>선택 면적 · {ready?`확인 ${pricing.count}건`:'자료 확인 전'}</small></div><button onClick={()=>onArea(NATIONAL_AREA)} aria-pressed={area===NATIONAL_AREA}><span>국평 · 전용 84㎡대 {trade==='sale'?'매매':'보증금'}</span><strong>{pricing.nationalMedian===null?'—':`${moneyLabel(pricing.nationalMedian)}원`}</strong><small>거래 중앙값 · {ready?`확인 ${pricing.nationalCount}건`:'자료 확인 전'} · 보기 →</small></button></div>
    <details className="pricing-method"><summary>계산 기준·자료 안내</summary><p>전용 평당가 = 신고금액 ÷ 전용면적(㎡) × 3.305785. 공급면적 기준 평당가와 다릅니다. 국평은 이 화면에서 84㎡ 이상 85㎡ 미만의 실제 거래를 뜻하며, 다른 면적의 가격을 환산하지 않습니다. 선택 기간 중 확인한 자료만 집계합니다. 취소·검토 매매는 제외하며, 전세는 신고 월세 0원, 월세는 0원 초과인 계약입니다. 월세 미제공 계약은 검토 기록을 포함한 전월세 전체에서만 확인할 수 있으며 통계에서는 제외합니다. 월세 계약은 월 납부액에 따라 보증금이 다르므로 함께 확인하세요.</p>    <p className="property-caption">금액 단위 원 · 회색 영역은 원천 자료 제공 전 또는 미확인 기간으로 0건과 다릅니다.{includeReview?' 차트·표는 검토·취소 기록 포함, 상단 요약은 유효 거래만 표시합니다.':' 취소·미확인 매매 제외.'}{trade==='rent'?' 월세와 보증금은 함께 확인하세요.':''}{chart.rows.length>points.length?' 차트는 각 월의 최저·최고·중앙순위·최신 실제 거래와 현재 선택을 표시합니다. 짝수 건의 중앙순위는 아래쪽 실제 거래를 사용합니다. 가격축은 표시 가능한 전체 거래 기준이며 표·CSV는 선택한 전체 기록입니다.':''}</p></details>
    </>}

  </section>;
}


export type PropertyHistoryProps=Parameters<typeof PropertyRawHistory>[0];
export default function PropertyHistory(props:PropertyHistoryProps){
  const {detail,complex,month,trade,range,rentKind='all',priceBasis,onPriceBasis,includeReview,summaryFirst=true}=props;
  const scope=`${detail.release_id}:${complex.id}:${month}:${range}:${trade}`;
  const months=useMemo(()=>historyMonths(month,range),[month,range]);
  const [loaded,setLoaded]=useState<{scope:string;data:MonthlySummaryData|null;fallback:boolean;error:string}>({scope:'',data:null,fallback:false,error:''});
  const [attempt,setAttempt]=useState(0),[fullRaw,setFullRaw]=useState(''),[localBasis,setLocalBasis]=useState<PriceBasis>('total');
  const wantsSummary=summaryFirst&&range>=12&&!includeReview&&fullRaw!==scope;
  useEffect(()=>{
    if(!wantsSummary)return;
    const controller=new AbortController();setLoaded({scope,data:null,fallback:false,error:''});
    void loadComplexPriceSummaries(detail.release_id,detail.lawd_code,months,controller.signal,complex.id).then(data=>{if(!controller.signal.aborted)setLoaded({scope,data,fallback:data===null,error:''});},error=>{if(!controller.signal.aborted)setLoaded({scope,data:null,fallback:false,error:error instanceof Error?error.message:'월별 요약 조회 실패'});});
    return()=>controller.abort();
  },[wantsSummary,scope,detail.release_id,detail.lawd_code,complex.id,months,attempt]);
  const current=loaded.scope===scope?loaded:null;
  if(!wantsSummary||current?.fallback)return <PropertyRawHistory {...props}/>;
  return <PropertyMonthlySummary {...props} rentKind={rentKind} basis={priceBasis??localBasis} onBasis={onPriceBasis??setLocalBasis} data={current?.data??null} state={current?.error?'error':current?.data?'ready':'loading'} error={current?.error??''} onRetry={()=>setAttempt(value=>value+1)} onFullRaw={()=>setFullRaw(scope)} renderRaw={({month:recordMonth,highlightTransaction,scatter,onShowRecordChart})=><PropertyRawHistory {...props} key={`${complex.id}:${recordMonth}`} autoArea={false} priceBasis={priceBasis??localBasis} onPriceBasis={onPriceBasis??setLocalBasis} month={recordMonth} range={1} embedded tableOnly={!scatter} highlightTransaction={highlightTransaction} onShowRecordChart={onShowRecordChart}/>}/>;
}
