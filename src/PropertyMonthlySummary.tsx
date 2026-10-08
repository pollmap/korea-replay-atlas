import {useEffect,useMemo,useRef,useState,type ReactNode} from 'react';
import type {PropertyComplex,PropertyRegionDetail,PropertySource,PropertyTransaction} from '../shared/property';
import {HISTORY_RANGES,historyMonths,historyRangeLabel,historyTick,type HistoryRange} from '../shared/property-history';
import {PRICE_BASES,type PriceBasis} from '../shared/property-pricing';
import {moneyLabel,monthLabel,propertyAreaOptions,propertyStatus} from '../shared/property-view';
import {historySourceCoverage,historySourceStart} from '../shared/property-source-period';
import {rentKindLabel,type RentKind} from '../shared/property-rent';
import {publishedHistoryCoverage,shiftHistoryEnd} from './property-history-state';
import type {ComplexPriceSummary,SummaryPartition} from './property-map-prices';
import {defaultSummaryArea,matchingSummaryRows,monthlySummaryPoints,summaryPrice} from './property-monthly-summary';

export interface MonthlySummaryData {rows:ComplexPriceSummary[];partitions:SummaryPartition[];}
interface Props {
  detail:PropertyRegionDetail;complex:PropertyComplex;month:string;trade:'sale'|'rent';area:string;range:HistoryRange;rentKind:RentKind;
  sources?:readonly PropertySource[];basis:PriceBasis;onBasis:(value:PriceBasis)=>void;onArea:(value:string)=>void;onRange:(value:HistoryRange)=>void;onMonth?:(value:string)=>void;
  autoArea?:boolean;onAutomaticArea?:(value:string)=>void;data:MonthlySummaryData|null;state:'loading'|'ready'|'error';error:string;onRetry:()=>void;onFullRaw:()=>void;
  renderRaw:(input:{month:string;highlightTransaction?:string;scatter:boolean;onShowRecordChart:(row:PropertyTransaction)=>void})=>ReactNode;
}

export default function PropertyMonthlySummary({detail,complex,month,trade,area,range,rentKind,sources,basis,onBasis,onArea,onRange,onMonth,autoArea,onAutomaticArea,data,state,error,onRetry,onFullRaw,renderRaw}:Props){
  const months=useMemo(()=>historyMonths(month,range),[month,range]);
  const selection=useMemo(()=>({complexId:complex.id,trade,rentKind,area,months}),[complex.id,trade,rentKind,area,months]);
  const sourceStart=historySourceStart(trade,sources),sourceCoverage=historySourceCoverage(months,sourceStart),published=publishedHistoryCoverage(detail,trade,months);
  const points=useMemo(()=>monthlySummaryPoints(data?.rows??[],data?.partitions??[],selection).map(point=>sourceStart&&point.month<sourceStart?{...point,status:'source_unavailable',count:null,latest:null}:point),[data,selection,sourceStart]);
  const trusted=useMemo(()=>{const known=new Set((data?.partitions??[]).filter(row=>['complete','empty'].includes(row.status)).map(row=>`${row.deal_month}:${row.trade_type}`));return (data?.rows??[]).filter(row=>known.has(`${row.deal_month}:${row.trade_type}`));},[data]);
  const rows=useMemo(()=>matchingSummaryRows(trusted,selection),[trusted,selection]);
  const areaRows=useMemo(()=>matchingSummaryRows(trusted,{...selection,area:''}),[trusted,selection]);
  const areas=[...new Set(areaRows.map(row=>row.area_m2))];
  const resolvedArea=useRef(new Set<string>());
  useEffect(()=>{const scope=`${detail.release_id}:${complex.id}:${trade}:${rentKind}`;if(!autoArea||state!=='ready'||resolvedArea.current.has(scope))return;resolvedArea.current.add(scope);if(!area){const value=defaultSummaryArea(trusted,selection);if(value)(onAutomaticArea??onArea)(value);}},[autoArea,state,detail.release_id,complex.id,trade,rentKind,area,trusted,selection,onAutomaticArea,onArea]);
  const scope=`${detail.release_id}:${complex.id}:${month}:${range}:${trade}:${area}:${rentKind}`;
  const [focus,setFocus]=useState<{scope:string;month:string;record?:string;scatter:boolean}>({scope:'',month:'',scatter:false});
  const latest=rows.slice().sort((a,b)=>b.latest_contract_date.localeCompare(a.latest_contract_date)||a.latest_transaction_id.localeCompare(b.latest_transaction_id))[0];
  const recordMonth=focus.scope===scope?focus.month:latest?.deal_month??month;
  const chosen=points.find(point=>point.month===recordMonth)?.latest??null;
  const ready=points.filter(point=>point.count!==null).length,count=points.reduce((total,point)=>total+(point.count??0),0);
  const prices=points.flatMap(point=>point.latest?summaryPrice(point.latest,basis)??[]:[]),minimum=prices.length?Math.min(...prices):null,maximum=prices.length?Math.max(...prices):null,maxVolume=Math.max(0,...points.map(point=>point.count??0));
  const [mode,setMode]=useState<'price'|'volume'>('price');
  const recordHeading=useRef<HTMLDivElement>(null),chartDialog=useRef<HTMLDialogElement>(null);
  const chooseMonth=(value:string)=>{chartDialog.current?.close();setFocus({scope,month:value,scatter:false});requestAnimationFrame(()=>recordHeading.current?.scrollIntoView({block:'nearest',behavior:'instant'}));};
  const unit=basis==='total'?'원':basis==='pyeong'?'원/전용평':'원/전용㎡';
  const chart=<svg className={`history-chart${mode==='volume'?' history-volume':''}`} viewBox="0 0 320 180" role="group" aria-label={mode==='price'?'월별 마지막 유효 계약 한 건의 가격. 전체 거래 점이나 월 중앙값이 아닙니다.':'선택 조건의 월별 유효 거래량. 회색 구간은 미확인 기간입니다.'}>
      <title>{mode==='price'?'월별 마지막 실제 계약 · 점을 눌러 해당 월 원문 보기':'월별 거래량 · 막대를 눌러 해당 월 원문 보기'}</title>
      {mode==='price'?<><text x="8" y="13">{maximum===null?(ready?'해당 거래 없음':'자료 확인 전'):`${moneyLabel(maximum)}${unit}`}</text><text x="8" y="145">{minimum===null?'':`${moneyLabel(minimum)}${unit}`}</text></>:<text x="8" y="13">{ready?`최대 ${maxVolume}건`:'자료 확인 전'}</text>}
      {[30,84,138].map(y=><line key={y} x1="12" y1={y} x2="308" y2={y} stroke="#e7ecf4" strokeDasharray="3 4"/>)}
      {points.map((point,i)=>{const step=296/months.length,x=12+(i+.5)*step,value=point.latest?summaryPrice(point.latest,basis):null,y=maximum===minimum?85:138-(value!-minimum!)/Math.max(1,maximum!-minimum!)*108,label=point.count===null?`${monthLabel(point.month)} · ${propertyStatus(point.status)}`:`${monthLabel(point.month)} · ${point.count}건${point.latest?` · 마지막 계약 ${point.latest.latest_contract_date} · ${point.latest.area_m2}㎡ · ${moneyLabel(value)}${unit}${point.latest.rent_kind==='monthly'?` · 월세 ${moneyLabel(point.latest.latest_monthly_rent_krw)}원`:''}`:''}`;return <g key={point.month}><title>{label}</title>{point.count===null?<rect x={12+i*step} y="23" width={step} height="120" fill="#eef1f5"/>:mode==='volume'?<rect x={12+i*step+step*.1} y={142-(point.count/Math.max(1,maxVolume)*118)} width={Math.max(.7,step*.8)} height={point.count===0?0:point.count/Math.max(1,maxVolume)*118} fill="var(--trade-color,#5145cd)" tabIndex={0} role="button" aria-label={label} onClick={()=>chooseMonth(point.month)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();chooseMonth(point.month);}}}/>:point.latest&&value!==null?<circle cx={x} cy={y} r={recordMonth===point.month?5:3.8} fill="#7850bd" stroke="#fff" tabIndex={0} role="button" aria-label={label} onClick={()=>chooseMonth(point.month)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();chooseMonth(point.month);}}}/>:<line x1={x-2} x2={x+2} y1="150" y2="150" stroke="#b8b0c8"><title>{label}</title></line>}{historyTick(i,months.length)&&<text x={i===months.length-1?308:12+i*step} y="169" textAnchor={i===months.length-1?'end':'start'}>{monthLabel(point.month)}</text>}</g>;})}
    </svg>;
  return <section className="property-history monthly-summary-history" aria-label="월별 요약과 선택 월 원문 거래">
    <h3 className="sr-only">월별 마지막 유효 계약과 거래량</h3>
    <div className="history-summary"><div className="history-main-price"><span>{trade==='rent'?`${rentKindLabel(rentKind)} 보증금`:'매매 실거래가'}{!area?' · 면적 혼합':''}</span><strong>{latest?moneyLabel(trade==='sale'?latest.latest_price_krw:latest.latest_deposit_krw):'—'}</strong><small>{latest?`${latest.latest_contract_date} · 전용 ${latest.area_m2}㎡`:state==='loading'?'요약을 불러오는 중':'확인된 거래 없음'}</small>{latest?.rent_kind==='monthly'&&<small>월세 {moneyLabel(latest.latest_monthly_rent_krw)}원</small>}</div><div className="history-total"><span>선택 기간 거래</span><strong>{state==='ready'&&ready?count.toLocaleString('ko-KR'):'—'}<small>건</small></strong></div></div>
    <div className="history-controls"><label className="property-search"><span className="sr-only">전용면적</span><select aria-label="이력 전용면적" value={area} onChange={event=>onArea(event.target.value)}><option value="">전체 면적</option>{propertyAreaOptions(areas,area,state==='ready'&&ready===sourceCoverage.eligible).map(item=><option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label className="history-period-select"><span className="sr-only">실거래 조회 기간</span><select aria-label="실거래 조회 기간" value={range} onChange={event=>onRange(Number(event.target.value) as HistoryRange)}>{HISTORY_RANGES.map(value=><option key={value} value={value}>최근 {historyRangeLabel(value)}</option>)}</select></label></div>
    <div className="history-chart-toolbar"><div className="history-chart-modes" role="group" aria-label="차트 표시"><button aria-pressed={mode==='price'} onClick={()=>setMode('price')}>월별 최근 거래</button><button aria-pressed={mode==='volume'} onClick={()=>setMode('volume')}>월별 거래량</button></div>{mode==='price'&&<label className="chart-basis-select"><span className="sr-only">가격 표시 기준</span><select aria-label="가격 표시 기준" value={basis} onChange={event=>onBasis(event.target.value as PriceBasis)}>{PRICE_BASES.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>}<button className="chart-expand" onClick={()=>chartDialog.current?.showModal()}>확대</button></div>
    {chart}
    <dialog ref={chartDialog} className="property-chart-dialog" aria-label={`${complex.name} 실거래 차트 확대`}><header><div><strong>{complex.name}</strong><span>{historyRangeLabel(range)} · {mode==='price'?'월별 최근 거래':'월별 거래량'}</span></div><button aria-label="차트 확대 닫기" onClick={()=>chartDialog.current?.close()}>닫기</button></header>{chart}<p>점을 선택하면 해당 월 거래표로 이동합니다.</p></dialog>
    {chosen&&<p className="history-point" aria-live="polite">{chosen.latest_contract_date} · 전용 {chosen.area_m2}㎡ <strong>{moneyLabel(summaryPrice(chosen,basis))}{unit}</strong>{chosen.rent_kind==='monthly'?` · 월세 ${moneyLabel(chosen.latest_monthly_rent_krw)}원`:''}</p>}
    {state==='error'&&<p className="property-error" role="alert">{error} <button onClick={onRetry}>요약 다시 확인</button></p>}
    <div ref={recordHeading} className="monthly-summary-records" aria-label={`${monthLabel(recordMonth)} 원문 계약`}>
      {renderRaw({month:recordMonth,highlightTransaction:focus.scope===scope?focus.record??chosen?.latest_transaction_id:chosen?.latest_transaction_id,scatter:focus.scope===scope&&focus.scatter,onShowRecordChart:row=>setFocus({scope,month:recordMonth,record:row.id,scatter:true})})}
    </div>
    <div className="history-secondary-tools"><div className="property-table-tools"><button disabled={state==='loading'} onClick={onFullRaw}>선택 기간 전체 원문 조회</button><button onClick={()=>setFocus(previous=>({scope,month:recordMonth,record:previous.scope===scope?previous.record:chosen?.latest_transaction_id,scatter:!(previous.scope===scope&&previous.scatter)}))}>{focus.scope===scope&&focus.scatter?'월 원문 차트 접기':`${monthLabel(recordMonth)} 전체 거래 차트`}</button></div>
    <details className="pricing-method"><summary>자료 기준</summary><p className="history-coverage">{monthLabel(months[0])}–{monthLabel(month)} · {ready}/{sourceCoverage.eligible}개월 확인{sourceCoverage.before>0?` · 원천 자료 제공 전 ${sourceCoverage.before}개월`:''}{state==='loading'?' · 요약 확인 중':ready<sourceCoverage.eligible?' · 미확인 기간 있음':''}</p></details>
    {onMonth&&<div className="property-table-tools" aria-label="거래 기간 이동"><button disabled={!published.first||months[0]<=published.first} onClick={()=>onMonth(shiftHistoryEnd(month,range,-1))}>← 이전 {historyRangeLabel(range)}</button><button disabled={month>=detail.period.latest_complete_month} onClick={()=>onMonth([shiftHistoryEnd(month,range,1),detail.period.latest_complete_month].sort()[0])}>다음 {historyRangeLabel(range)} →</button></div>}
    <details className="pricing-method"><summary>월별 자료 보기</summary><p>게시 {published.count}/{sourceCoverage.eligible}개월</p><p className="property-caption">{published.first&&published.last?`이 지역의 게시 자료: ${monthLabel(published.first)}–${monthLabel(published.last)}. 중간에 미수집 월이 있을 수 있습니다.`:'이 거래 유형의 게시 자료가 없습니다.'} 미확인 기간은 거래 0건과 다릅니다.</p><div className="transaction-table"><table><thead><tr><th>계약월</th><th>자료 상태</th><th>유효 거래</th></tr></thead><tbody>{[...points].reverse().map(point=><tr key={point.month}><td><button onClick={()=>chooseMonth(point.month)}>{monthLabel(point.month)}</button></td><td>{state==='loading'?'요약 확인 중':propertyStatus(point.status)}</td><td>{point.count===null?'미확인':`${point.count}건`}</td></tr>)}</tbody></table></div></details>
    <details className="pricing-method"><summary>요약 기준·원문 조회</summary><p>가격 점은 선택 조건에서 각 월의 마지막 유효 계약 한 건입니다. 월 중앙값·평균·전체 거래 분포가 아닙니다. 회색 구간을 선으로 연결하지 않으며 미확인 월의 건수를 0으로 채우지 않습니다. 초기 계약표는 선택한 한 달의 원문을 조회합니다. 전체 기간 원문은 명시적으로 요청할 때 기존 24MiB·512파일·20,000건 예산 안에서 확인합니다. 전용면적 기준이며 월세 계약은 보증금과 월세를 함께 봅니다. 요약의 월별 중앙값을 다시 평균·중앙값으로 계산하지 않습니다.</p></details>
    </div>
  </section>;
}
