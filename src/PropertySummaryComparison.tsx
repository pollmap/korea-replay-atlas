import {useEffect,useMemo,useState} from 'react';
import {HISTORY_RANGES,historyMonths,historyRangeLabel,type HistoryRange} from '../shared/property-history';
import {historySourceStart,historySourceCoverage} from '../shared/property-source-period';
import {moneyLabel,monthLabel,propertyAreaOptions} from '../shared/property-view';
import {rentKindLabel} from '../shared/property-rent';
import {PRICE_BASES,type PriceBasis} from '../shared/property-pricing';
import {loadComplexPriceSummaries} from './property-summary-client';
import {matchingSummaryRows,monthlySummaryPoints,summaryPrice} from './property-monthly-summary';
import type {MonthlySummaryData} from './PropertyMonthlySummary';
import type {ComplexComparisonProps} from './ComplexComparison';

export function commonSummaryMonths(months:readonly string[],data:Record<string,MonthlySummaryData>,ids:readonly string[],trade:'sale'|'rent',sourceStart:string|null){
  return months.filter(month=>(!sourceStart||month>=sourceStart)&&ids.length>0&&ids.every(id=>data[id]?.partitions.some(p=>p.deal_month===month&&p.trade_type===trade&&['complete','empty'].includes(p.status))));
}
export default function PropertySummaryComparison({atlas,items,month,trade,area,onArea,onRemove,rentKind='all',range=36,onRange,onRaw}:ComplexComparisonProps&{onRaw:()=>void}){
  const [loaded,setLoaded]=useState<{key:string;data:Record<string,MonthlySummaryData>}>({key:'',data:{}}),[error,setError]=useState(''),[attempt,setAttempt]=useState(0),[basis,setBasis]=useState<PriceBasis>('total');
  const selection=items.map(item=>item.id).sort().join(','),key=`${atlas.property.release_id}:${selection}:${month}:${range}`;
  const months=useMemo(()=>historyMonths(month,range),[month,range]);
  useEffect(()=>{const controller=new AbortController();setError('');
    void (async()=>{if(items.length>3)throw new Error('최대 3개 단지를 비교할 수 있습니다.');const data:Record<string,MonthlySummaryData>={};
      for(const item of items){const value=await loadComplexPriceSummaries(atlas.property.release_id,item.lawd_code,months,controller.signal,item.id);if(!value){if(!controller.signal.aborted)onRaw();return;}data[item.id]=value;}
      if(!controller.signal.aborted)setLoaded({key,data});
    })().catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'비교 요약을 불러오지 못했습니다.');});return()=>controller.abort();
  },[atlas.property.release_id,items,selection,months,key,attempt,onRaw]);
  const waiting=loaded.key!==key&&!error,data=loaded.key===key?loaded.data:{};
  const sourceStart=historySourceStart(trade,atlas.property.sources),coverage=historySourceCoverage(months,sourceStart);
  const common=commonSummaryMonths(months,data,items.map(item=>item.id),trade,sourceStart??null);
  const allRows=Object.values(data).flatMap(value=>value.rows),areas=[...new Set(allRows.filter(row=>row.trade_type===trade).map(row=>row.area_m2))];
  const summaries=items.map(item=>{const selection={complexId:item.id,trade,rentKind,area,months:common},rows=area?matchingSummaryRows(data[item.id]?.rows??[],selection):[];const latest=rows.slice().sort((a,b)=>b.latest_contract_date.localeCompare(a.latest_contract_date)||a.latest_transaction_id.localeCompare(b.latest_transaction_id))[0];return {item,count:rows.reduce((count,row)=>count+row.transaction_count,0),latest,points:area?monthlySummaryPoints(data[item.id]?.rows??[],data[item.id]?.partitions??[],selection):[]};});
  const values=summaries.flatMap(row=>row.points.flatMap(point=>point.latest?summaryPrice(point.latest,basis)??[]:[])),min=values.length?Math.min(...values):null,max=values.length?Math.max(...values):null;
  const colors=['#7651c5','#d97038','#168783'],unit=basis==='total'?'원':basis==='pyeong'?'원/전용평':'원/전용㎡';
  return <section className="complex-comparison" aria-label="아파트 단지 비교"><h3>단지 비교 <small>{items.length} / 3</small></h3><p className="property-caption">{monthLabel(months[0])}–{monthLabel(month)} · {trade==='sale'?'매매':rentKindLabel(rentKind)} · 같은 전용면적 조건</p>
    <div className="property-table-tools" aria-label="선택한 비교 단지">{items.map((item,i)=><button key={item.id} style={{borderColor:colors[i]}} aria-label={`${item.name} 단지 비교 제외`} onClick={()=>onRemove(item.id)}>{item.name} ×</button>)}</div>
    <div className="history-controls"><label className="property-search">비교 면적<select value={area} onChange={event=>onArea(event.target.value)}><option value="">같은 전용면적을 선택하세요</option>{propertyAreaOptions(areas,area,!waiting&&!error&&common.length===coverage.eligible).map(option=><option key={option.value} value={option.value}>{option.label}</option>)}</select></label>{onRange&&<label className="history-period-select"><span className="sr-only">비교 조회 기간</span><select aria-label="비교 조회 기간" value={range} onChange={event=>onRange(Number(event.target.value) as HistoryRange)}>{HISTORY_RANGES.map(value=><option key={value} value={value}>최근 {historyRangeLabel(value)}</option>)}</select></label>}</div>
    {waiting?<p role="status">비교 요약을 불러오는 중…</p>:error?<p role="alert">{error} <button onClick={()=>setAttempt(value=>value+1)}>요약 다시 확인</button></p>:<><p className="history-coverage" role="status">모든 단지 공통 확인 {common.length}/{coverage.eligible}개월{coverage.before?` · 원천 제공 전 ${coverage.before}개월`:''}{common.length<coverage.eligible?' · 미확인 월은 비교 제외':''}</p>
    <label className="chart-basis-select">가격 기준<select aria-label="비교 가격 기준" value={basis} onChange={event=>setBasis(event.target.value as PriceBasis)}>{PRICE_BASES.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
    <svg className="history-chart" viewBox="0 0 320 180" role="img" aria-label="단지별 월별 마지막 실제 유효 계약 비교. 각 월 중앙값이 아니며 결손 월을 연결하지 않습니다."><text x="8" y="13">{max===null?'확인 거래 없음':`${moneyLabel(max)}${unit}`}</text><text x="8" y="145">{min===null?'':`${moneyLabel(min)}${unit}`}</text>{[30,84,138].map(y=><line key={y} x1="12" y1={y} x2="308" y2={y} stroke="#e7ecf4" strokeDasharray="3 4"/>)}{months.map((month,i)=>!common.includes(month)?<rect key={month} x={12+i*296/months.length} y="23" width={296/months.length} height="120" fill="#eef1f5"><title>{monthLabel(month)} 공통 자료 미확인</title></rect>:null)}{summaries.map(({item,points},i)=>points.map(point=>{const value=point.latest?summaryPrice(point.latest,basis):null;if(value===null)return null;const index=months.indexOf(point.month);return <circle key={`${item.id}:${point.month}`} cx={12+(index+.5)*296/months.length} cy={max===min?85:138-(value-min!)/Math.max(1,max!-min!)*108} r="3.8" fill={colors[i]} stroke="#fff"><title>{item.name} · {point.latest!.latest_contract_date} · 전용 {point.latest!.area_m2}㎡ · {moneyLabel(value)}{unit}</title></circle>;}))}<text x="12" y="169">{monthLabel(months[0])}</text><text x="308" y="169" textAnchor="end">{monthLabel(month)}</text></svg>
    <div className="transaction-table"><table><thead><tr><th>단지</th><th>유효 거래</th><th>마지막 실제 계약</th></tr></thead><tbody>{summaries.map(({item,count,latest},i)=><tr key={item.id}><th style={{color:colors[i]}}>{item.name}<small>{atlas.regions.regions.find(row=>row.lawd_code===item.lawd_code)?.name}</small></th><td>{!area?'면적 선택':common.length?`${count}건`:'공통 자료 없음'}</td><td>{latest?moneyLabel(summaryPrice(latest,basis)):'—'}{latest&&<><small>{latest.latest_contract_date} · 전용 {latest.area_m2}㎡</small>{latest.rent_kind==='monthly'&&<small>월세 {moneyLabel(latest.latest_monthly_rent_krw)}원</small>}</>}</td></tr>)}</tbody></table></div></>}
    <p className="property-caption">공통으로 확인된 월의 거래 건수와 월별 마지막 실제 계약을 비교합니다. 전체 기간 중앙값이나 시세가 아닙니다. 전체 거래의 중앙값은 원문 조회로 확인합니다.</p><button onClick={onRaw}>전체 기간 원문·중앙값 확인</button>
  </section>;
}
