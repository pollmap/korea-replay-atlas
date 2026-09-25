export interface ApartmentFacts {
  complex_id:string;kapt_code:string;classification:string;
  households:number|null;buildings:number|null;parking:number|null;
  road_address:string|null;heating:string|null;corridor:string|null;management:string|null;builder:string|null;
  approved_on:string|null;provider_updated_on:string|null;
}
export interface ApartmentFactsIndex {property_release_id:string;source:string;retrieved_at:string;rows:ApartmentFacts[];}
/** Keep identity evidence separate from unverified provider coordinates. */
export function apartmentFactsIndex(value:unknown):ApartmentFactsIndex{
  const fail=():never=>{throw new Error('단지 기본정보의 원천·식별번호가 올바르지 않습니다.');};
  if(!value||typeof value!=='object'||Array.isArray(value))return fail();
  const data=value as Record<string,unknown>;
  if(data.schema_version!==1||typeof data.property_release_id!=='string'||!/^property-[a-f0-9]{16}$/.test(data.property_release_id)
    ||data.source!=='https://data.seoul.go.kr/dataList/OA-15818/A/1/datasetView.do'||typeof data.retrieved_at!=='string'||!Number.isFinite(Date.parse(data.retrieved_at))
    ||data.identity_method!=='existing_unique_official_road_address_and_name'||data.coordinate_verification!=='not_performed'
    ||!Array.isArray(data.rows)||data.rows.length>3000)return fail();
  const seen=new Set<string>(),codes=new Set<string>();
  const rows=data.rows.map(value=>{
    if(!value||typeof value!=='object'||Array.isArray(value))return fail();const row=value as Record<string,unknown>;
    if(typeof row.complex_id!=='string'||!/^molit-apt:11\d{3}:[A-Za-z0-9_-]{1,64}$/.test(row.complex_id)||seen.has(row.complex_id)
      ||typeof row.kapt_code!=='string'||!/^A\d{8,12}$/.test(row.kapt_code)||codes.has(row.kapt_code)||!['아파트','주상복합'].includes(String(row.classification)))return fail();
    for(const key of ['households','buildings','parking'])if(row[key]!==null&&(typeof row[key]!=='number'||!Number.isSafeInteger(row[key])||row[key]<0||row[key]>1_000_000))return fail();
    for(const key of ['road_address','heating','corridor','management','builder'])if(row[key]!==null&&(typeof row[key]!=='string'||row[key].length>300||[...row[key]].some(char=>char.charCodeAt(0)<32)))return fail();
    for(const key of ['approved_on','provider_updated_on'])if(row[key]!==null&&(typeof row[key]!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(row[key])||!Number.isFinite(Date.parse(row[key]))||new Date(row[key]).toISOString().slice(0,10)!==row[key]))return fail();
    seen.add(row.complex_id);codes.add(row.kapt_code);return row as unknown as ApartmentFacts;
  });
  return {property_release_id:data.property_release_id,source:data.source,retrieved_at:data.retrieved_at,rows};
}
export function parkingPerHousehold(facts:ApartmentFacts):number|null{
  return facts.parking!==null&&facts.households!==null&&facts.households>0?facts.parking/facts.households:null;
}
