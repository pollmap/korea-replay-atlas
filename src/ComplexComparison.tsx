import {rentKindLabel,rentKindMatches,type RentKind} from '../shared/property-rent';
import {pricingSummary} from '../shared/property-pricing';
import {useEffect,useMemo,useState} from 'react';
import {summarizePropertyTransactions,type PropertyComplex} from '../shared/property';
import {HISTORY_RANGES,historyMonths,historyRangeLabel,type HistoryRange} from '../shared/property-history';
import {moneyLabel,monthLabel,propertyAreaOptions} from '../shared/property-view';
import {loadComparisonHistory} from './property-comparison-loader';
import {commonHistoryMonths,historyMonthStatus} from './property-history-state';
import type {HistoryResult} from './property-history-loader';
import type {AtlasContent} from './useAtlas';

export default function ComplexComparison({atlas,items,month,trade,area,onArea,onRemove,rentKind='all',range=1,onRange}:{atlas:AtlasContent;items:PropertyComplex[];month:string;trade:'sale'|'rent';area:string;onArea:(area:string)=>void;onRemove:(id:string)=>void;rentKind?:RentKind;range?:HistoryRange;onRange?:(range:HistoryRange)=>void}){
  const [loaded,setLoaded]=useState<{key:string;histories:Record<string,HistoryResult[]>}>({key:'',histories:{}}),[pending,setPending]=useState(true),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
  const selection=useMemo(()=>items.map(item=>item.id).sort().join(','),[items]);
  const codes=useMemo(()=>[...new Set(items.map(item=>item.lawd_code))].sort(),[items]);
  const queryKey=`${atlas.property.release_id}:${selection}:${month}:${range}:${trade}`;
  useEffect(()=>{
    const controller=new AbortController();setPending(true);setError('');
    void loadComparisonHistory({atlas,items,month,range,trade,signal:controller.signal}).then(histories=>{if(!controller.signal.aborted)setLoaded({key:queryKey,histories});}).catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'비교 자료 조회 실패');}).finally(()=>{if(!controller.signal.aborted)setPending(false);});
    return()=>controller.abort();
  },[atlas,items,month,range,trade,queryKey,attempt]);
  const months=useMemo(()=>historyMonths(month,range),[month,range]);
  const histories=loaded.key===queryKey?loaded.histories:{};
  const common=commonHistoryMonths(months,histories,codes),commonSet=new Set(common);
  const rows=Object.values(histories).flatMap(results=>results.filter(result=>commonSet.has(result.month)).flatMap(result=>result.rows));
  const ids=new Set(items.map(item=>item.id));
  const areas=[...new Set(rows.filter(row=>row.trade_type===trade&&rentKindMatches(row,rentKind)&&ids.has(row.complex_id??'')&&row.area_m2).map(row=>row.area_m2!))].sort((a,b)=>Number(a)-Number(b));
  const filteredRows=rows.filter(row=>rentKindMatches(row,rentKind));
  const waiting=pending||!error&&loaded.key!==queryKey;
  const failed=Object.values(histories).some(results=>results.some(result=>result.status==='error'));
  const end=new Date(Date.UTC(Number(month.slice(0,4)),Number(month.slice(4)),0)).toISOString().slice(0,10),start=`${months[0].slice(0,4)}-${months[0].slice(4)}-01`;
  return <section className="complex-comparison" aria-label="아파트 단지 비교"><h3>단지 비교 <small>{items.length} / 3</small></h3><p className="property-caption">{monthLabel(months[0])}–{monthLabel(month)} · {trade==='sale'?'매매':rentKindLabel(rentKind)} · 같은 전용면적 조건</p>
    <div className="property-table-tools" aria-label="선택한 비교 단지">{items.map(item=><button key={item.id} aria-label={`${item.name} 단지 비교 제외`} onClick={()=>onRemove(item.id)}>{item.name} ×</button>)}</div>
    <div className="history-controls"><label className="property-search">비교 면적<select value={area} onChange={event=>onArea(event.target.value)}><option value="">같은 전용면적을 선택하세요</option>{propertyAreaOptions(areas,area,!waiting&&!error&&common.length===months.length).map(option=><option key={option.value} value={option.value}>{option.label}</option>)}</select></label>{onRange&&<label className="history-period-select"><span className="sr-only">비교 조회 기간</span><select aria-label="비교 조회 기간" value={range} onChange={event=>onRange(Number(event.target.value) as HistoryRange)}>{HISTORY_RANGES.map(value=><option key={value} value={value}>최근 {historyRangeLabel(value)}</option>)}</select></label>}</div>
    {waiting?<p role="status">비교 자료를 불러오는 중…</p>:error?<p role="alert">{error}</p>:<><p className="history-coverage" role="status">모든 단지 공통 확인 {common.length}/{months.length}개월{common.length<months.length?' · 나머지 월은 비교에서 제외':''}</p><div className="transaction-table"><table><thead><tr><th>단지</th><th>표본</th><th>{trade==='sale'?'거래 중앙값':'보증금 / 월세 중앙값'}</th></tr></thead><tbody>{items.map(item=>{
      const summary=area&&common.length?summarizePropertyTransactions(filteredRows,{complex_id:item.id,trade_type:trade,area_m2:area,from:start,to:end}):null;
      const pricing=summary?pricingSummary(filteredRows.filter(row=>row.contract_date&&row.contract_date>=start&&row.contract_date<=end),{complexId:item.id,trade,area}):null;
      return <tr key={item.id}><th>{item.name}<small>{atlas.regions.regions.find(r=>r.lawd_code===item.lawd_code)?.name}</small></th><td>{summary?`${summary.count}건`:!common.length?'공통 자료 없음':'면적 선택'}</td><td>{summary?.count?moneyLabel(trade==='sale'?summary.median_price_krw:summary.median_deposit_krw):'—'}{!!summary?.count&&<small>전용평당 {moneyLabel(pricing?.perPyeong??null)}원</small>}{trade==='rent'&&!!summary?.count&&<small>월 {moneyLabel(summary.median_monthly_rent_krw)}</small>}</td></tr>;
    })}</tbody></table></div>{common.length<months.length&&<details className="pricing-method"><summary>비교에서 제외된 기간</summary><div className="transaction-table"><table><thead><tr><th>계약월</th>{codes.map(code=><th key={code}>{atlas.regions.regions.find(row=>row.lawd_code===code)?.name??code}</th>)}</tr></thead><tbody>{months.filter(value=>!commonSet.has(value)).map(value=><tr key={value}><th>{monthLabel(value)}</th>{codes.map(code=><td key={code}>{historyMonthStatus(histories[code]?.find(result=>result.month===value))}</td>)}</tr>)}</tbody></table></div></details>}</>}
    {!waiting&&(error||failed)&&<button onClick={()=>setAttempt(value=>value+1)}>비교 자료 다시 불러오기</button>}
    <p className="property-caption">모든 단지에 공통으로 확인된 월만 집계합니다. 금액은 원 단위 신고 거래 중앙값이며 공식 가격지수가 아닙니다. 취소·취소 여부 미확인 매매는 제외합니다.{trade==='rent'?' 월세와 보증금은 함께 비교하세요.':''}</p>
  </section>;
}
