export interface SearchComplex {id:string;name:string;lawd_code:string;legal_dong_name:string|null;lot_number:string|null;region_name:string;aliases:string[];}
export interface SearchEntry {complex:SearchComplex;names:string[];address:string;}
const scope=new Set(["11110", "11140", "11170", "11200", "11215", "11230", "11260", "11290", "11305", "11320", "11350", "11380", "11410", "11440", "11470", "11500", "11530", "11545", "11560", "11590", "11620", "11650", "11680", "11710", "11740", "26110", "26140", "26170", "26200", "26230", "26260", "26290", "26320", "26350", "26380", "26410", "26440", "26470", "26500", "26530", "26710", "28125", "28155", "28177", "28185", "28200", "28237", "28245", "28275", "28290", "28710", "28720", "30110", "30140", "30170", "30200", "30230", "36110", "41111", "41113", "41115", "41117", "41131", "41133", "41135", "41150", "41171", "41173", "41192", "41194", "41196", "41210", "41220", "41250", "41271", "41273", "41281", "41285", "41287", "41290", "41310", "41360", "41370", "41390", "41410", "41430", "41450", "41461", "41463", "41465", "41480", "41500", "41550", "41570", "41591", "41593", "41595", "41597", "41610", "41630", "41650", "41670", "41800", "41820", "41830", "43111", "43112", "43113", "43114", "44131", "44133", "44200"]);
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const text=(v:unknown):v is string=>typeof v==='string'&&v.length>0&&v.length<=200;
const nullableText=(v:unknown)=>v===null||text(v);
export const normalizeSearch=(text:string)=>text.normalize('NFKC').toLocaleLowerCase('ko-KR').replace(/\s+/g,' ').trim();
const fail=():never=>{throw Error('검색 자료를 확인하지 못했습니다.');};
export function parseSearchIndex(value:unknown,release:string):SearchEntry[]{
 if(!object(value)||value.kind!=='property-complex-search-index'||value.schema_version!==1||value.property_release_id!==release||value.scope!=='priority-nine'||!Array.isArray(value.regions)||value.regions.length!==scope.size||!Array.isArray(value.rows)||value.rows.length>60000||!object(value.audit)||value.audit.complexes!==value.rows.length||value.audit.regions!==scope.size||value.audit.source_calls!==0||value.audit.position_inference!==false)return fail();
 const regions=new Map<string,string>();
 for(const row of value.regions){if(!Array.isArray(row)||row.length!==2||!scope.has(row[0])||regions.has(row[0])||!text(row[1]))return fail();regions.set(row[0],row[1]);}
 const ids=new Set<string>();
 return value.rows.map(row=>{
  if(!Array.isArray(row)||row.length!==6||!text(row[0])||!text(row[1])||!regions.has(row[2])||!row[0].startsWith(`molit-apt:${row[2]}:`)||ids.has(row[0])||!nullableText(row[3])||!nullableText(row[4])||!Array.isArray(row[5])||row[5].length>100||!row[5].every(text))return fail();
  ids.add(row[0]);const complex:SearchComplex={id:row[0],name:row[1],lawd_code:row[2],legal_dong_name:row[3],lot_number:row[4],aliases:row[5],region_name:regions.get(row[2])!};
  return {complex,names:[row[1],...row[5]].map(normalizeSearch),address:normalizeSearch([complex.region_name,row[3],row[4]].filter(Boolean).join(' '))};
 });
}
export function searchComplexIndex(index:readonly SearchEntry[],query:string,region:string):SearchComplex[]{
 const normalized=normalizeSearch(query).slice(0,120);if(normalized.length<2)return [];
 const tokens=normalized.split(' '),matches:{complex:SearchComplex;rank:number}[]=[];
 for(const entry of index){
  const name=entry.names.join(' ');if(!tokens.every(token=>name.includes(token)||entry.address.includes(token)))continue;
  const rank=(entry.names.includes(normalized)?0:entry.names.some(name=>name.startsWith(normalized))?2:entry.names.some(name=>name.includes(normalized))?4:6)+(entry.complex.lawd_code===region?0:1);
  matches.push({complex:entry.complex,rank});
 }
 return matches.sort((a,b)=>a.rank-b.rank||a.complex.name.localeCompare(b.complex.name,'ko')||a.complex.lawd_code.localeCompare(b.complex.lawd_code)||a.complex.id.localeCompare(b.complex.id)).slice(0,8).map(row=>row.complex);
}
export function currentSearchReply(reply:{sequence:number;query:string},sequence:number,query:string){return reply.sequence===sequence&&reply.query===query;}
