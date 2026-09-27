import {useMemo} from 'react';
import source from './data/seoul-apartment-facts.json';
import historySource from './data/seoul-apartment-facts-54bf1817fdcc7bd9.json';
import refreshedSource from './data/seoul-apartment-facts-87d1c67336e97209.json';
import {apartmentFactsIndex,parkingPerHousehold,type ApartmentFacts as Facts} from '../shared/property-facts';
interface FactsIndex {release:string;source:string;retrieved:string;rows:Map<string,Facts>;}
/** Lazy validation/cache per exact release; matching complex IDs do not authorize reuse. */
export function createApartmentFactsLookup(sources:readonly unknown[]){
  const registered=new Map<string,unknown>(),cached=new Map<string,FactsIndex>();
  if(sources.length>32)throw new Error('단지 기본정보 버전 수가 제한을 초과했습니다.');
  for(const entry of sources){
    const release=entry&&typeof entry==='object'&&!Array.isArray(entry)?(entry as Record<string,unknown>).property_release_id:null;
    if(typeof release!=='string'||!/^property-[a-f0-9]{16}$/.test(release)||registered.has(release))throw new Error('단지 기본정보 버전 목록이 올바르지 않습니다.');
    registered.set(release,entry);
  }
  return (release:string):FactsIndex|undefined=>{
    if(!registered.has(release))return undefined;
    let result=cached.get(release);
    if(!result){
      const data=apartmentFactsIndex(registered.get(release));
      if(data.property_release_id!==release)throw new Error('단지 기본정보 버전이 다릅니다.');
      result={release,source:data.source,retrieved:data.retrieved_at,rows:new Map(data.rows.map(row=>[row.complex_id,row]))};
      cached.set(release,result);
    }
    return result;
  };
}
// Add new audited generated sources here; preserve older sources for pinned shares.
const index=createApartmentFactsLookup([source,historySource,refreshedSource]);
export default function ApartmentFacts({complexId,release}:{complexId:string;release:string}){
  const result=useMemo(()=>{try{const data=index(release);return {data,facts:data?.rows.get(complexId),invalid:false};}catch{return {data:undefined,facts:undefined,invalid:true};}},[complexId,release]);
  const {data,facts,invalid}=result,parking=facts?parkingPerHousehold(facts):null;
  const missing=invalid?'확인 필요':facts?'원문 미제공':'연결 전';
  const count=(value:number|null|undefined,unit:string)=>value==null?missing:`${value.toLocaleString('ko-KR')}${unit}`;
  const rows=[['도로명주소',facts?.road_address],['사용승인일',facts?.approved_on],['난방',facts?.heating],['복도 유형',facts?.corridor],['시공사',facts?.builder],['관리 방식',facts?.management]] as const;
  return <section className="apartment-official-facts" aria-label="단지 규모와 건물 정보">
    <div className="apartment-fact-heading"><h4>규모·주차</h4><span className={facts?'facts-state is-connected':'facts-state'}>{invalid?'자료 확인 필요':facts?'공식 자료':'자료 연결 전'}</span></div>
    <div className="apartment-fact-highlights"><div><span>세대수</span><strong className={facts?.households==null?'is-missing':undefined}>{count(facts?.households,'세대')}</strong><small>동수 {count(facts?.buildings,'개 동')}</small></div><div><span>주차</span><strong className={facts?.parking==null?'is-missing':undefined}>{count(facts?.parking,'대')}</strong><small>{parking===null?`세대당 ${missing}`:`세대당 ${parking.toLocaleString('ko-KR',{maximumFractionDigits:2})}대`}</small></div></div>
    <h4 className="apartment-fact-section-title">건물·관리</h4>
    <dl>{rows.map(([label,value])=><div key={label}><dt>{label}</dt><dd className={value==null?'fact-missing-value':undefined}>{value??missing}</dd></div>)}</dl>
    {facts&&data?<details className="apartment-fact-source"><summary>기본정보 출처·기준일</summary><a href={data.source} target="_blank" rel="noreferrer">서울시 공동주택 아파트 정보 ↗</a><p>수집 {data.retrieved.slice(0,10)} · 원천 수정 {facts.provider_updated_on??'미제공'} · {facts.kapt_code}</p><p>공공누리 제1유형. 세대당 주차는 제공 주차대수 ÷ 세대수이며 실제 이용 가능 공간과 다를 수 있습니다. 사용승인일은 입주 예정일과 구분합니다.</p></details>:<p className="apartment-fact-unconnected" role="status">{invalid?'기본정보 검증에 실패했습니다. 확인되지 않은 값은 표시하지 않습니다.':'이 단지의 기본정보는 아직 연결되지 않았습니다.'}</p>}
  </section>;
}
