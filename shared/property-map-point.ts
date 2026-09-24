/** A provider point with audited identity, not a verified complex footprint. */
export interface PropertyMapPoint {complexId:string;kaptCode:string;longitude:number;latitude:number;releaseId:string;coordinateStatus:'provider_xy_crs_unconfirmed';}
export function parsePropertyMapPoints(value:unknown,release:string,sourceSha:string):Map<string,PropertyMapPoint>{
  const fail=()=>{throw new Error('단지 위치 연결 자료가 검증된 버전과 다릅니다.');};
  if(!value||typeof value!=='object'||Array.isArray(value))return fail();
  const data=value as Record<string,unknown>;
  if(data.schema_version!==1||data.property_release_id!==release||data.source_sha256!==sourceSha||data.coordinate_status!=='provider_xy_crs_unconfirmed'||!Array.isArray(data.points)||data.points.length>3000)return fail();
  const points=new Map<string,PropertyMapPoint>(),codes=new Set<string>();
  for(const row of data.points){
    if(!Array.isArray(row)||row.length!==4)return fail();
    const [id,code,lon,lat]=row;
    if(typeof id!=='string'||!/^molit-apt:11\d{3}:[A-Za-z0-9_-]{1,64}$/.test(id)||typeof code!=='string'||!/^A\d{8,12}$/.test(code)||points.has(id)||codes.has(code)||typeof lon!=='number'||!Number.isFinite(lon)||lon<126.6||lon>127.4||typeof lat!=='number'||!Number.isFinite(lat)||lat<37.3||lat>37.8)return fail();
    points.set(id,{complexId:id,kaptCode:code,longitude:lon,latitude:lat,releaseId:release,coordinateStatus:'provider_xy_crs_unconfirmed'});codes.add(code);
  }
  return points;
}
