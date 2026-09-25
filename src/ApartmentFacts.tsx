import {useMemo} from 'react';
import source from './data/seoul-apartment-facts.json';
import {apartmentFactsIndex,parkingPerHousehold,type ApartmentFacts as Facts} from '../shared/property-facts';
let cached:{release:string;source:string;retrieved:string;rows:Map<string,Facts>}|undefined;
function index(){
  if(!cached){const data=apartmentFactsIndex(source);cached={release:data.property_release_id,source:data.source,retrieved:data.retrieved_at,rows:new Map(data.rows.map(row=>[row.complex_id,row]))};}
  return cached;
}
export default function ApartmentFacts({complexId,release}:{complexId:string;release:string}){
  const result=useMemo(()=>{try{const data=index();return {data,facts:data.release===release?data.rows.get(complexId):undefined};}catch{return null;}},[complexId,release]);
  if(!result?.facts)return null;
  const {data,facts}=result,parking=parkingPerHousehold(facts);
  const count=(value:number|null,unit:string)=>value===null?'자료 없음':`${value.toLocaleString('ko-KR')}${unit}`;
  return <section className="apartment-official-facts" aria-label="서울시 공동주택 기본정보">
    <div className="apartment-fact-highlights"><div><span>세대수</span><strong>{count(facts.households,'세대')}</strong><small>{count(facts.buildings,'개 동')}</small></div><div><span>주차</span><strong>{count(facts.parking,'대')}</strong><small>{parking===null?'세대당 주차 미확인':`세대당 ${parking.toLocaleString('ko-KR',{maximumFractionDigits:2})}대`}</small></div></div>
    <dl><div><dt>도로명주소</dt><dd>{facts.road_address??'자료 없음'}</dd></div><div><dt>사용승인일</dt><dd>{facts.approved_on??'자료 없음'}</dd></div><div><dt>난방</dt><dd>{facts.heating??'자료 없음'}</dd></div><div><dt>복도 유형</dt><dd>{facts.corridor??'자료 없음'}</dd></div><div><dt>시공사</dt><dd>{facts.builder??'자료 없음'}</dd></div><div><dt>관리 방식</dt><dd>{facts.management??'자료 없음'}</dd></div></dl>
    <details className="apartment-fact-source"><summary>기본정보 출처·기준일</summary><a href={data.source} target="_blank" rel="noreferrer">서울시 공동주택 아파트 정보 ↗</a><p>수집 {data.retrieved.slice(0,10)} · 원천 수정 {facts.provider_updated_on??'미제공'} · {facts.kapt_code}</p><p>공공누리 제1유형. 세대당 주차는 제공 주차대수 ÷ 세대수이며 실제 이용 가능 공간과 다를 수 있습니다. 사용승인일은 입주 예정일과 구분합니다.</p></details>
  </section>;
}
