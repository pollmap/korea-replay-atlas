import type {SurroundingsRecord,SurroundingsScope,SurroundingsSourceState} from './property-surroundings';
export const OFFICIAL_SCHOOLS_URL='https://www.data.go.kr/data/15021148/standard.do';
export const OFFICIAL_FEES_URL='https://data.seoul.go.kr/dataList/OA-15822/S/1/datasetView.do';
export interface OfficialSchool {id:string;name:string;type:'elementary'|'middle'|'high';address:string;longitude:number;latitude:number;asOf:string;}
export interface OfficialFees {month:string;rows:Map<string,{name:string;items:readonly (readonly [string,number])[]}>;}
const object=(value:unknown):Record<string,unknown>=>{if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('공식 자료 형식이 다릅니다.');return value as Record<string,unknown>;};
export function parseOfficialSchools(value:unknown):OfficialSchool[]{
  const data=object(value);
  if(data.schema_version!==1||data.source!==OFFICIAL_SCHOOLS_URL||!Array.isArray(data.rows)||data.rows.length>15000)throw new Error('학교 자료 형식이 다릅니다.');
  const seen=new Set<string>();
  return data.rows.map((row:unknown)=>{
    if(!Array.isArray(row)||row.length!==7)throw new Error('학교 자료 형식이 다릅니다.');
    const [id,name,type,address,longitude,latitude,asOf]=row;
    if(typeof id!=='string'||!/^B\d{9}$/.test(id)||seen.has(id)||typeof name!=='string'||!name||typeof address!=='string'||!address||!['elementary','middle','high'].includes(type)||typeof longitude!=='number'||!Number.isFinite(longitude)||longitude<124||longitude>132||typeof latitude!=='number'||!Number.isFinite(latitude)||latitude<32||latitude>39.5||typeof asOf!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(asOf))throw new Error('학교 위치 자료가 올바르지 않습니다.');
    seen.add(id);return {id,name,type,address,longitude,latitude,asOf};
  });
}
export function officialSchoolsAround(rows:readonly OfficialSchool[],center:{longitude:number;latitude:number},scope:SurroundingsScope):SurroundingsSourceState {
  if(!Number.isFinite(center.longitude)||center.longitude<124||center.longitude>132||!Number.isFinite(center.latitude)||center.latitude<32||center.latitude>39.5)throw new Error('학교 검색 기준점이 올바르지 않습니다.');
  const records:SurroundingsRecord[]=[];
  for(const row of rows){
    const radians=Math.PI/180,dlat=(row.latitude-center.latitude)*radians,dlon=(row.longitude-center.longitude)*radians;
    const a=Math.sin(dlat/2)**2+Math.cos(center.latitude*radians)*Math.cos(row.latitude*radians)*Math.sin(dlon/2)**2;
    const distanceMeters=6371008.8*2*Math.asin(Math.sqrt(Math.min(1,a)));
    if(distanceMeters>3000)continue;
    records.push({id:`official-school:${row.id}`,name:row.name,address:row.address,category:'school',type:row.type,distanceMeters,position:{longitude:row.longitude,latitude:row.latitude,method:'official_school_location'},source:{id:'koies-school-location',label:'한국교육시설안전원 학교 위치',url:OFFICIAL_SCHOOLS_URL,asOf:row.asOf}});
  }
  // A provider's location register establishes nearby records, never school assignment or all real-world facilities.
  return {status:'ready',scope,category:'school',coverageRadius:3000,complete:false,records,source:{id:'koies-school-location',label:'한국교육시설안전원 학교 위치',url:OFFICIAL_SCHOOLS_URL,asOf:rows[0]?.asOf??''}};
}
export function parseOfficialFees(value:unknown):OfficialFees {
  const data=object(value);
  if(data.schema_version!==1||data.source!==OFFICIAL_FEES_URL||data.unit!=='KRW'||data.semantics!=='reported_complex_line_items'||typeof data.month!=='string'||!/^\d{4}(0[1-9]|1[0-2])$/.test(data.month)||!Array.isArray(data.rows)||data.rows.length>10000)throw new Error('관리비 자료 형식이 다릅니다.');
  const rows:OfficialFees['rows']=new Map();
  for(const row of data.rows){
    if(!Array.isArray(row)||row.length!==3)throw new Error('관리비 자료 형식이 다릅니다.');
    const [code,name,items]=row;
    if(typeof code!=='string'||!/^[AB]\d{8,12}$/.test(code)||rows.has(code)||typeof name!=='string'||!name||!Array.isArray(items)||items.length>100)throw new Error('관리비 단지 코드가 올바르지 않습니다.');
    const labels=new Set<string>();
    for(const item of items){if(!Array.isArray(item)||item.length!==2||typeof item[0]!=='string'||!item[0]||labels.has(item[0])||typeof item[1]!=='number'||!Number.isSafeInteger(item[1]))throw new Error('관리비 금액이 올바르지 않습니다.');labels.add(item[0]);}
    rows.set(code,{name,items});
  }
  return {month:data.month,rows};
}
