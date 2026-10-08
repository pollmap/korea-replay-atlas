/** A provider point with audited identity, not a verified complex footprint. */
export interface PropertyMapPoint {complexId:string;kaptCode:string;longitude:number;latitude:number;releaseId:string;coordinateStatus:'provider_xy_crs_unconfirmed';navigationEvidence?:PropertyNavigationEvidence;}
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

/** Navigation corroboration is independent of parcel, entrance and survey accuracy. */
export interface PropertyNavigationEvidence {
  method:'official_site_marker_same_kapt_code_and_coordinates';
  pointSemantics:'provider_map_navigation_marker';
  sourceUrl:string;checkedAt:string;markerResponseSha256:string;navigationPageSha256:string;
}
const NAVIGATION_SOURCE='https://openapt.seoul.go.kr/commonPortal/programLink.do?jspNm=/portal/aMenu/aptInfo/aptInfo.open';
export function confirmPropertyNavigationPoints(value:unknown,points:ReadonlyMap<string,PropertyMapPoint>,release:string,sourceSha:string):Map<string,PropertyMapPoint>{
  const fail=():never=>{throw new Error('공식 지도 위치 근거가 일치하지 않습니다.');};
  if(!value||typeof value!=='object'||Array.isArray(value))return fail();
  const data=value as Record<string,unknown>;
  if(data.schema_version!==1||data.property_release_id!==release||data.source_sha256!==sourceSha||data.identity_rule!=='unique_official_road_address_and_name'||data.method!=='official_site_marker_same_kapt_code_and_coordinates'||data.point_semantics!=='provider_map_navigation_marker'||data.source_url!==NAVIGATION_SOURCE||typeof data.checked_at!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(data.checked_at)||!Number.isFinite(Date.parse(data.checked_at))||typeof data.marker_response_sha256!=='string'||!/^[a-f0-9]{64}$/.test(data.marker_response_sha256)||typeof data.navigation_page_sha256!=='string'||!/^[a-f0-9]{64}$/.test(data.navigation_page_sha256)||!Array.isArray(data.points)||data.points.length>3000)return fail();
  const result=new Map(points),seen=new Set<string>();
  for(const row of data.points){
    if(!Array.isArray(row)||row.length!==4||typeof row[0]!=='string'||seen.has(row[0]))return fail();
    const point=points.get(row[0]);
    if(!point||point.releaseId!==release||point.kaptCode!==row[1]||point.longitude!==row[2]||point.latitude!==row[3])return fail();
    seen.add(row[0]);
    result.set(point.complexId,{...point,navigationEvidence:{method:data.method,pointSemantics:data.point_semantics,sourceUrl:data.source_url,checkedAt:data.checked_at,markerResponseSha256:data.marker_response_sha256,navigationPageSha256:data.navigation_page_sha256}});
  }
  return result;
}
export function confirmedPropertyNavigationPoint(point:PropertyMapPoint|null|undefined,release:string,complexId?:string):PropertyMapPoint|null{
  return point?.releaseId===release&&(!complexId||point.complexId===complexId)&&point.navigationEvidence?.method==='official_site_marker_same_kapt_code_and_coordinates'?point:null;
}

export function propertyNavigationCamera(point:PropertyMapPoint|null|undefined,release:string):{key:string;center:[number,number];zoom:15}|null{
  const confirmed=confirmedPropertyNavigationPoint(point,release);
  return confirmed?{key:`${confirmed.releaseId}:${confirmed.complexId}`,center:[confirmed.longitude,confirmed.latitude],zoom:15}:null;
}
