import {useEffect,useState} from 'react';
import {parkingPerHousehold} from '../shared/property-facts';
import {apartmentFactsClient,type FactsIndex} from './apartment-facts-client';
export {createApartmentFactsLookup} from './apartment-facts-client';
export default function ApartmentFacts({complexId,release}:{complexId:string;release:string}){
  const [attempt,setAttempt]=useState(0);
  const [loaded,setLoaded]=useState<{release:string;data?:FactsIndex;invalid:boolean}|null>(()=>{
    const data=apartmentFactsClient.peek(release);return data?{release,data,invalid:false}:null;
  });
  useEffect(()=>{
    let active=true;
    void apartmentFactsClient.load(release).then(data=>{if(active)setLoaded({release,data,invalid:false});}).catch(()=>{if(active)setLoaded({release,invalid:true});});
    return()=>{active=false;};
  },[release,attempt]);
  const result=loaded?.release===release?loaded:null;
  const data=result?.data??apartmentFactsClient.peek(release),facts=data?.rows.get(complexId),invalid=result?.invalid??false;
  const busy=!data&&!result&&apartmentFactsClient.has(release),parking=facts?parkingPerHousehold(facts):null;
  const missing=busy?'불러오는 중':invalid?'확인 필요':facts?'원문 미제공':'연결 전';
  const count=(value:number|null|undefined,unit:string)=>value==null?missing:`${value.toLocaleString('ko-KR')}${unit}`;
  const rows=[['도로명주소',facts?.road_address],['사용승인일',facts?.approved_on],['난방',facts?.heating],['복도 유형',facts?.corridor],['시공사',facts?.builder],['관리 방식',facts?.management]] as const;
  return <section className="apartment-official-facts" aria-label="단지 규모와 건물 정보" aria-busy={busy||undefined}>
    <div className="apartment-fact-heading"><h4>규모·주차</h4><span className={facts?'facts-state is-connected':'facts-state'}>{busy?'불러오는 중':invalid?'자료 확인 필요':facts?'공식 자료':'자료 연결 전'}</span></div>
    <div className="apartment-fact-highlights"><div><span>세대수</span><strong className={facts?.households==null?'is-missing':undefined}>{count(facts?.households,'세대')}</strong><small>동수 {count(facts?.buildings,'개 동')}</small></div><div><span>주차</span><strong className={facts?.parking==null?'is-missing':undefined}>{count(facts?.parking,'대')}</strong><small>{parking===null?`세대당 ${missing}`:`세대당 ${parking.toLocaleString('ko-KR',{maximumFractionDigits:2})}대`}</small></div></div>
    <h4 className="apartment-fact-section-title">건물·관리</h4>
    <dl>{rows.map(([label,value])=><div key={label}><dt>{label}</dt><dd className={value==null?'fact-missing-value':undefined}>{value??missing}</dd></div>)}</dl>
    {facts&&data?<details className="apartment-fact-source"><summary>기본정보 출처·기준일</summary><a href={data.source} target="_blank" rel="noreferrer">서울시 공동주택 아파트 정보 ↗</a><p>수집 {data.retrieved.slice(0,10)} · 원천 수정 {facts.provider_updated_on??'미제공'} · {facts.kapt_code}</p><p>공공누리 제1유형. 세대당 주차는 제공 주차대수 ÷ 세대수이며 실제 이용 가능 공간과 다를 수 있습니다. 사용승인일은 입주 예정일과 구분합니다.</p></details>:<p className="apartment-fact-unconnected" role="status">{busy?'기본정보 불러오는 중':invalid?'기본정보 확인 실패':'기본정보 미연결'}{invalid&&<button onClick={()=>{setLoaded(null);setAttempt(value=>value+1);}}>다시 불러오기</button>}</p>}
  </section>;
}
