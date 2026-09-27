import {SURROUNDINGS_TYPES,type SurroundingsRecord,type SurroundingsScope,type SurroundingsSource,type SurroundingsSourceState} from './property-surroundings';

export interface PoiCenter {longitude:number;latitude:number;}
export interface PoiChunk {key:string;west:number;south:number;east:number;north:number;file:string;sha256:string;bytes:number;count:number;}
export interface PoiManifest {schema:number;source:SurroundingsSource&{sha256:string;license:string};scope:{boundaryDate:string;regionCodes:string[]};chunks:PoiChunk[];}
export const POI_MAX_QUERY_BYTES=4*1024*1024;
export const POI_MAX_QUERY_CHUNKS=64;
export function validPoiCenter(center:PoiCenter):boolean{return Number.isFinite(center.longitude)&&Number.isFinite(center.latitude)&&center.longitude>=124&&center.longitude<=132&&center.latitude>=32&&center.latitude<=40;}
/** Conservative search box; exact spherical distances decide radius membership. */
export function nearbyPoiChunks(manifest:PoiManifest,center:PoiCenter):PoiChunk[]{
  if(!validPoiCenter(center))throw new Error('시설 검색 기준 위치를 확인해 주세요.');
  const latDelta=3000/110000,lonDelta=latDelta/Math.cos((center.latitude+latDelta)*Math.PI/180);
  const chunks=manifest.chunks.filter(c=>c.west<=center.longitude+lonDelta&&c.east>=center.longitude-lonDelta&&c.south<=center.latitude+latDelta&&c.north>=center.latitude-latDelta);
  if(chunks.length>POI_MAX_QUERY_CHUNKS||chunks.reduce((sum,c)=>sum+c.bytes,0)>POI_MAX_QUERY_BYTES)throw new Error('이 위치의 시설 자료가 조회 한도를 초과했습니다.');
  return chunks;
}
export function poiDistance(a:PoiCenter,b:PoiCenter):number{
  const r=Math.PI/180,dlat=(b.latitude-a.latitude)*r,dlon=(b.longitude-a.longitude)*r;
  const h=Math.sin(dlat/2)**2+Math.cos(a.latitude*r)*Math.cos(b.latitude*r)*Math.sin(dlon/2)**2;
  return 6371008.8*2*Math.asin(Math.min(1,Math.sqrt(h)));
}
/** Validate every input row; one invalid/duplicate source record fails the query. */
export function parsePoiChunk(value:unknown,chunk:PoiChunk,center:PoiCenter,source:SurroundingsSource):SurroundingsRecord[]{
  const payload=value as {schema?:number;records?:unknown[]}|null;
  if(payload?.schema!==1||!Array.isArray(payload.records)||payload.records.length!==chunk.count||chunk.bytes>1024*1024)throw new Error('시설 자료의 목록이 검증 결과와 다릅니다.');
  const seen=new Set<string>(),result:SurroundingsRecord[]=[];
  for(const value of payload.records){
    if(!value||typeof value!=='object')throw new Error('시설 자료 형식 오류');
    const row=value as Record<string,unknown>;
    if(typeof row.id!=='string'||! /^(node|way|relation)\/[1-9][0-9]*$/.test(row.id)||seen.has(row.id)||typeof row.name!=='string'||!row.name.trim()||row.name.length>512||!['transport','school','life'].includes(String(row.category))||typeof row.type!=='string'||!['original_node','area_representative_point','line_midpoint'].includes(String(row.positionMethod))||typeof row.longitude!=='number'||typeof row.latitude!=='number'||!validPoiCenter({longitude:row.longitude,latitude:row.latitude})||row.longitude<chunk.west||row.longitude>chunk.east||row.latitude<chunk.south||row.latitude>chunk.north||(row.address!==undefined&&(typeof row.address!=='string'||row.address.length>1024)))throw new Error('시설의 출처·좌표를 검증하지 못했습니다.');
    const category=row.category as 'transport'|'school'|'life';
    if(!SURROUNDINGS_TYPES[category].some(([id])=>id===row.type))throw new Error('시설 분류를 검증하지 못했습니다.');
    seen.add(row.id);
    const distanceMeters=poiDistance(center,{longitude:row.longitude,latitude:row.latitude});
    if(distanceMeters<=3000)result.push({id:row.id,name:row.name,address:row.address as string|undefined,category,type:row.type,distanceMeters,position:{longitude:row.longitude,latitude:row.latitude,method:row.positionMethod},source:{...source,url:`https://www.openstreetmap.org/${row.id}`}} as SurroundingsRecord);
  }
  return result;
}
export function poiSourceStates(records:SurroundingsRecord[],scope:SurroundingsScope,source:SurroundingsSource):Partial<Record<'transport'|'school'|'life',SurroundingsSourceState>>{
  if(new Set(records.map(r=>r.id)).size!==records.length)throw new Error('시설 원본 ID가 중복되었습니다.');
  return Object.fromEntries((['transport','school','life'] as const).map(category=>[category,{status:'ready',scope,category,coverageRadius:3000,complete:false,records:records.filter(row=>row.category===category),source}]));
}
