import type {PropertyComplex} from './property';

export interface MapLinkPoint {lat:number;lon:number;}
export interface ExternalMapLink {label:string;href:string;}

function validQuery(value:string):boolean {
  return typeof value==='string'&&value.trim().length>0&&value.length<=400&&![...value].some(char=>char.charCodeAt(0)<32||char.charCodeAt(0)===127);
}

export function isKoreanMapPoint(point:MapLinkPoint):boolean {
  return Number.isFinite(point.lat)&&Number.isFinite(point.lon)&&point.lat>=32&&point.lat<=39.5&&point.lon>=124&&point.lon<=132.5;
}

/** Official Kakao link API: latitude precedes longitude. No SDK or geocoding call. */
export function kakaoPointLinks(point:MapLinkPoint):ExternalMapLink[] {
  if(!isKoreanMapPoint(point))return [];
  const coordinates=`${point.lat.toFixed(6)},${point.lon.toFixed(6)}`;
  return [
    {label:'카카오 로드뷰',href:`https://map.kakao.com/link/roadview/${coordinates}`},
    {label:'카카오맵',href:`https://map.kakao.com/link/map/${coordinates}`},
    {label:'여기로 길찾기',href:`https://map.kakao.com/link/to/${encodeURIComponent('선택 위치')},${coordinates}`},
  ];
}

/** Address search is deliberately not presented as a verified place or route. */
export function addressMapLinks(query:string):ExternalMapLink[] {
  if(!validQuery(query))return [];
  const encoded=encodeURIComponent(query.trim());
  return [
    {label:'네이버 지도',href:`https://map.naver.com/p/search/${encoded}`},
    {label:'카카오맵',href:`https://map.kakao.com/link/search/${encoded}`},
  ];
}

export function complexMapLinks(complex:PropertyComplex,region:string):ExternalMapLink[] {
  const query=[region,complex.legal_dong_name,complex.lot_number,complex.name].filter(Boolean).join(' ');
  const search=addressMapLinks(query);
  // Never upgrade provider candidate points, name matches, or conflicting addresses.
  const position=complex.position;
  if(complex.address_conflict||!position||position.crs!=='EPSG:4326'||!position.evidence?.verified_at)return search;
  const point=kakaoPointLinks({lat:position.latitude,lon:position.longitude});
  return [...search,...point.filter(link=>link.label!=='카카오맵')];
}
