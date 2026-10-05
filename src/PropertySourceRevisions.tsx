import {useEffect,useState} from 'react';
import {fetchPinnedPropertyRevisionJson} from './atlas-client';
import {moneyLabel} from '../shared/property-view';
import {revisionSource,parseRevisionArchive,type RevisionArchive} from './property-source-revisions';

/** Earlier published observations remain available without entering current
 * prices, counts or charts. Source absence is not labelled a cancellation. */
export default function PropertySourceRevisions({release,complexId,region}:{release:string;complexId?:string;region:string}){
  const source=revisionSource(release);
  const [open,setOpen]=useState(false),[attempt,setAttempt]=useState(0);
  const [result,setResult]=useState<{release:string;data?:RevisionArchive;error?:string}>({release:''});
  useEffect(()=>{
    if(!open||!source||result.release===release&&result.data)return;
    const controller=new AbortController();
    void fetchPinnedPropertyRevisionJson(source.url,{sha256:source.sha256,bytes:source.bytes},controller.signal).then(body=>{if(!controller.signal.aborted)setResult({release,data:parseRevisionArchive(body,release)});}).catch(()=>{if(!controller.signal.aborted)setResult({release,error:'이전 기록을 불러오지 못했습니다.'});});
    return()=>controller.abort();
  },[open,source,release,attempt,result.release,result.data]);
  if(!source)return null;
  const data=result.release===release?result.data:undefined;
  const rows=data?.records.filter(row=>row.lawd_code===region&&(!complexId||row.previous.complex_id===complexId))??[];
  return <details className="pricing-method property-source-revisions" onToggle={event=>setOpen(event.currentTarget.open)}>
    <summary>정정·이전 기록</summary>
    {open&&<>{data?<>
      {rows.length?<><p className="discovery-note">이전 공개 기록 {rows.length}건 · 현재 가격·거래량 집계에서 제외</p><div className="revision-records"><table><thead><tr><th>계약일</th><th>전용면적</th><th>이전 금액</th><th>원천 변경</th></tr></thead><tbody>{rows.map(row=>{
        const r=row.previous,hash=new URLSearchParams({regionCode:row.lawd_code,trade:row.trade_type,month:row.deal_month,historyMonths:'1',propertyRelease:data.previous_release,...(r.complex_id?{complex:r.complex_id}:{})});
        return <tr key={r.id}><td><a href={`${source.previousAppOrigin}/#${hash}`} target="_blank" rel="noreferrer">{r.contract_date??row.deal_month} ↗</a></td><td>{r.area_m2??'—'}㎡</td><td>{moneyLabel(row.trade_type==='sale'?r.price_krw:r.deposit_krw)}</td><td>{row.state==='report_fields_updated'?row.changed_fields.includes('cancellation')?'취소 정보 갱신':row.changed_fields.includes('registration_date')?'등기 정보 갱신':'원천 갱신':'이전 원천 기록'}</td></tr>;
      })}</tbody></table></div><p className="discovery-note">새 원천에서 확인되지 않는 기록을 취소 거래로 간주하지 않습니다.</p></>:<p>이 단지의 이전·변경 기록이 없습니다.</p>}
    </>:result.release===release&&result.error?<p role="alert">{result.error} <button onClick={()=>setAttempt(v=>v+1)}>다시 시도</button></p>:<p role="status">이전 기록 조회 중</p>}</>}
  </details>;
}
