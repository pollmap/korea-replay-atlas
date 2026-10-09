import {useEffect,useState} from 'react';
import {apartmentFactsClient} from './apartment-facts-client';
import {loadOfficialFees} from './official-property-context';
import {OFFICIAL_FEES_URL,type OfficialFees} from '../shared/official-property-context';
interface Result {key:string;month?:string;row?:ReturnType<OfficialFees['rows']['get']>;error?:boolean;}
export default function PropertyManagementFees({complexId,release,active}:{complexId:string;release:string;active:boolean}){
  const [result,setResult]=useState<Result|null>(null),[attempt,setAttempt]=useState(0),[expanded,setExpanded]=useState(false);
  const key=`${release}:${complexId}`;
  useEffect(()=>{
    if(!active)return;
    let current=true;
    void apartmentFactsClient.load(release).then(async facts=>{
      const code=facts?.rows.get(complexId)?.kapt_code;
      if(!code)return {key};
      const fees=await loadOfficialFees();return {key,month:fees.month,row:fees.rows.get(code)};
    }).then(value=>{if(current)setResult(value);}).catch(()=>{if(current)setResult({key,error:true});});
    return()=>{current=false;};
  },[active,key,complexId,release,attempt]);
  const data=result?.key===key?result:null;
  const items=data?.row?.items??[];
  return <section className="property-management-fees" aria-label="공식 관리비 명세" aria-busy={active&&!data||undefined}>
    <h3>관리비</h3>
    {data?.row?<><p>{data.month?.slice(0,4)}년 {Number(data.month?.slice(4))}월 · 단지 항목별 금액(원)</p><table className="trade-table"><thead><tr><th>항목</th><th>금액</th></tr></thead><tbody>{(expanded?items:items.slice(0,6)).map(([label,amount])=><tr key={label}><th scope="row">{label}</th><td>{amount.toLocaleString('ko-KR')}</td></tr>)}</tbody></table>{items.length>6&&<button onClick={()=>setExpanded(value=>!value)}>{expanded?'접기':`${items.length}개 항목 모두 보기`}</button>}<p className="surroundings-source">단지 명세서의 제공 항목입니다. 세대별 청구액·㎡당 관리비와 다릅니다.</p></>:<p role={data?.error?'alert':'status'}>{!active?'관리비 메뉴에서 확인':!data?'불러오는 중':data.error?'자료를 불러오지 못했습니다.':'이 단지의 명세서가 연결되지 않았습니다.'}{data?.error&&<button onClick={()=>setAttempt(value=>value+1)}>다시 불러오기</button>}</p>}
    <a href={OFFICIAL_FEES_URL} target="_blank" rel="noopener noreferrer">서울시 관리비 원문 ↗</a>
  </section>;
}
