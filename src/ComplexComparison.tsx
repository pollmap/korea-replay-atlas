import {pricingSummary} from '../shared/property-pricing';
import {useEffect,useMemo,useState} from 'react';
import {parsePropertyRegionDetail,parsePropertyTransactions,summarizePropertyTransactions,type PropertyComplex,type PropertyTransaction} from '../shared/property';
import {moneyLabel,monthLabel,propertyAreaOptions} from '../shared/property-view';
import {fetchPinnedJson} from './atlas-client';
import type {AtlasContent} from './useAtlas';

export default function ComplexComparison({atlas,items,month,trade,area,onArea,onRemove}:{atlas:AtlasContent;items:PropertyComplex[];month:string;trade:'sale'|'rent';area:string;onArea:(area:string)=>void;onRemove:(id:string)=>void}){
  const [rows,setRows]=useState<PropertyTransaction[]>([]),[pending,setPending]=useState(true),[error,setError]=useState(''),[missing,setMissing]=useState<string[]>([]),[rowsKey,setRowsKey]=useState(''),[attempt,setAttempt]=useState(0);
  const codes=useMemo(()=>[...new Set(items.map(item=>item.lawd_code))].sort().join(','),[items]);
  const queryKey=`${atlas.property.release_id}:${codes}:${month}:${trade}`;
  useEffect(()=>{
    const controller=new AbortController();setPending(true);setError('');setRows([]);setRowsKey(queryKey);setMissing([]);
    void Promise.all(codes.split(',').filter(Boolean).map(async code=>{
      const region=atlas.regions.regions.find(row=>row.lawd_code===code);if(!region)throw new Error('비교할 지역이 이 자료 버전에 없습니다.');
      const detail=parsePropertyRegionDetail(await fetchPinnedJson(region.index,atlas.origin,controller.signal));
      if(detail.lawd_code!==code||detail.release_id!==atlas.property.release_id)throw new Error('비교 지역 자료 버전 불일치');
      const partition=detail.partitions.find(row=>row.deal_month===month&&row.trade_type===trade);
      if(!partition||!['complete','empty'].includes(partition.status))return {rows:[],missing:code};
      const parts=await Promise.all(partition.transactions.map(async ref=>{const result=parsePropertyTransactions(await fetchPinnedJson(ref,atlas.origin,controller.signal));if(result.release_id!==detail.release_id||result.lawd_code!==code||result.deal_month!==month)throw new Error('비교 거래 자료 범위 불일치');return result.transactions;}));
      return {rows:parts.flat(),missing:null};
    })).then(result=>{if(!controller.signal.aborted){setRows(result.flatMap(row=>row.rows));setMissing(result.flatMap(row=>row.missing?[row.missing]:[]));}}).catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'비교 자료 조회 실패');}).finally(()=>{if(!controller.signal.aborted)setPending(false);});
    return()=>controller.abort();
  },[atlas,codes,month,trade,queryKey,attempt]);
  const ids=useMemo(()=>new Set(items.map(item=>item.id)),[items]);
  const areas=useMemo(()=>rowsKey===queryKey?[...new Set(rows.filter(row=>row.trade_type===trade&&ids.has(row.complex_id??'')&&row.area_m2).map(row=>row.area_m2!))].sort((a,b)=>Number(a)-Number(b)):[],[rows,rowsKey,queryKey,ids,trade]);
  const waiting=pending||rowsKey!==queryKey;
  const end=new Date(Date.UTC(Number(month.slice(0,4)),Number(month.slice(4)),0)).toISOString().slice(0,10),start=`${month.slice(0,4)}-${month.slice(4)}-01`;
  return <section className="complex-comparison" aria-label="아파트 단지 비교"><h3>단지 비교 <small>{items.length} / 3</small></h3><p className="property-caption">{monthLabel(month)} · {trade==='sale'?'매매':'전월세'} · 같은 전용면적 조건 · 84㎡대 선택 가능</p>
    <div className="property-table-tools" aria-label="선택한 비교 단지">{items.map(item=><button key={item.id} aria-label={`${item.name} 단지 비교 제외`} onClick={()=>onRemove(item.id)}>{item.name} ×</button>)}</div>
    <label className="property-search">비교 면적<select value={area} onChange={event=>onArea(event.target.value)}><option value="">같은 전용면적을 선택하세요</option>{propertyAreaOptions(areas,area,!waiting&&!error&&!missing.length).map(option=><option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
    {waiting?<p role="status">비교 자료를 불러오는 중…</p>:error?<><p role="alert">{error}</p><button onClick={()=>setAttempt(value=>value+1)}>비교 자료 다시 불러오기</button></>:<div className="transaction-table"><table><thead><tr><th>단지</th><th>표본</th><th>{trade==='sale'?'거래 중앙값':'보증금 / 월세 중앙값'}</th></tr></thead><tbody>{items.map(item=>{
      const summary=area&&!missing.includes(item.lawd_code)?summarizePropertyTransactions(rows,{complex_id:item.id,trade_type:trade,area_m2:area,from:start,to:end}):null;
      const pricing=summary?pricingSummary(rows.filter(row=>row.contract_date&&row.contract_date>=start&&row.contract_date<=end),{complexId:item.id,trade,area}):null;
      return <tr key={item.id}><th>{item.name}<small>{atlas.regions.regions.find(r=>r.lawd_code===item.lawd_code)?.name}</small></th><td>{summary?`${summary.count}건`:missing.includes(item.lawd_code)?'미수집':'면적 선택'}</td><td>{summary?.count?moneyLabel(trade==='sale'?summary.median_price_krw:summary.median_deposit_krw):'—'}{!!summary?.count&&<small>전용평당 {moneyLabel(pricing?.perPyeong??null)}원</small>}{trade==='rent'&&!!summary?.count&&<small>월 {moneyLabel(summary.median_monthly_rent_krw)}</small>}</td></tr>;
    })}</tbody></table></div>}
    <p className="property-caption">원 단위 신고 거래 중앙값이며 공식 가격지수가 아닙니다. 수집 완료 자료의 0건은 현행 지역 코드로 조회한 해당 면적·기간의 신고 거래가 없다는 뜻입니다. 취소·취소 여부 미확인 매매는 제외합니다.</p>
  </section>;
}
