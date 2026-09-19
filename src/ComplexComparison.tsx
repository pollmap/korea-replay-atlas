import {useEffect,useMemo,useState} from 'react';
import {parsePropertyRegionDetail,parsePropertyTransactions,summarizePropertyTransactions,type PropertyComplex,type PropertyTransaction} from '../shared/property';
import {moneyLabel,monthLabel} from '../shared/property-view';
import {fetchPinnedJson} from './atlas-client';
import type {AtlasContent} from './useAtlas';

export default function ComplexComparison({atlas,items,month,trade,area,onArea,onRemove}:{atlas:AtlasContent;items:PropertyComplex[];month:string;trade:'sale'|'rent';area:string;onArea:(area:string)=>void;onRemove:(id:string)=>void}){
  const [rows,setRows]=useState<PropertyTransaction[]>([]),[pending,setPending]=useState(true),[error,setError]=useState(''),[missing,setMissing]=useState<string[]>([]);
  const codes=useMemo(()=>[...new Set(items.map(item=>item.lawd_code))].sort().join(','),[items]);
  useEffect(()=>{
    const controller=new AbortController();setPending(true);setError('');setRows([]);setMissing([]);
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
  },[atlas,codes,month,trade]);
  const ids=useMemo(()=>new Set(items.map(item=>item.id)),[items]);
  const areas=useMemo(()=>[...new Set(rows.filter(row=>row.trade_type===trade&&ids.has(row.complex_id??'')&&row.area_m2).map(row=>row.area_m2!))].sort((a,b)=>Number(a)-Number(b)),[rows,ids,trade]);
  const end=new Date(Date.UTC(Number(month.slice(0,4)),Number(month.slice(4)),0)).toISOString().slice(0,10),start=`${month.slice(0,4)}-${month.slice(4)}-01`;
  return <section className="complex-comparison" aria-label="아파트 단지 비교"><h3>단지 비교 <small>{items.length} / 3</small></h3><p className="property-caption">{monthLabel(month)} · {trade==='sale'?'매매':'전월세'} · 같은 전용면적 조건</p>
    <label className="property-search">비교 면적<select value={area} onChange={event=>onArea(event.target.value)}><option value="">같은 전용면적을 선택하세요</option>{[...new Set([...areas,...(area?[area]:[])])].sort((a,b)=>Number(a)-Number(b)).map(value=><option key={value} value={value}>{value} ㎡</option>)}</select></label>
    {pending?<p role="status">비교 자료를 불러오는 중…</p>:error?<p role="alert">{error}</p>:<div className="transaction-table"><table><thead><tr><th>단지</th><th>표본</th><th>{trade==='sale'?'거래 중앙값':'보증금 / 월세 중앙값'}</th><th>제외</th></tr></thead><tbody>{items.map(item=>{
      const summary=area&&!missing.includes(item.lawd_code)?summarizePropertyTransactions(rows,{complex_id:item.id,trade_type:trade,area_m2:area,from:start,to:end}):null;
      return <tr key={item.id}><th>{item.name}<small>{atlas.regions.regions.find(r=>r.lawd_code===item.lawd_code)?.name}</small></th><td>{summary?`${summary.count}건`:missing.includes(item.lawd_code)?'미수집':'면적 선택'}</td><td>{summary?.count?moneyLabel(trade==='sale'?summary.median_price_krw:summary.median_deposit_krw):'—'}{trade==='rent'&&!!summary?.count&&<small>월 {moneyLabel(summary.median_monthly_rent_krw)}</small>}</td><td><button aria-label={`${item.name} 단지 비교 제외`} onClick={()=>onRemove(item.id)}>×</button></td></tr>;
    })}</tbody></table></div>}
    <p className="property-caption">원 단위 신고 거래 중앙값이며 공식 가격지수가 아닙니다. 0건은 해당 면적·기간의 거래가 없다는 뜻입니다. 취소·취소 여부 미확인 매매는 제외합니다.</p>
  </section>;
}
