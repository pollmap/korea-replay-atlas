import type {Place} from './contracts';
import type {Feature,FeatureCollection} from 'geojson';

export type MeasurementMode='none'|'distance'|'area';
export type MapPoint=[number,number];
export interface Measurement {mode:MeasurementMode;points:MapPoint[];distance_m:number;area_m2:number;}
export interface MapBookmark {id:string;name:string;position:Place;created_at:string;}
export interface BookmarkFile {schema_version:1;kind:'korea-replay-bookmarks';bookmarks:MapBookmark[];}
const R=6371008.8,rad=Math.PI/180;
export const EMPTY_MEASUREMENT:Measurement={mode:'none',points:[],distance_m:0,area_m2:0};
export function validMapPoint(value:unknown):value is MapPoint {
  return Array.isArray(value)&&value.length===2&&value.every(v=>typeof v==='number'&&Number.isFinite(v))&&Math.abs(value[0])<=180&&Math.abs(value[1])<=85;
}
/** Geodesic approximation on a mean-radius sphere; never cadastral/survey area. */
export function measureMap(mode:MeasurementMode,points:MapPoint[]):Measurement {
  if(points.length>512||!points.every(validMapPoint))throw new Error('측정점은 유효한 좌표 512개 이하여야 합니다.');
  let distance=0,area=0;
  for(let i=1;i<points.length;i++){
    const [a,b]=[points[i-1],points[i]],dy=(b[1]-a[1])*rad,dx=(b[0]-a[0])*rad;
    const h=Math.sin(dy/2)**2+Math.cos(a[1]*rad)*Math.cos(b[1]*rad)*Math.sin(dx/2)**2;
    distance+=2*R*Math.atan2(Math.sqrt(Math.min(1,h)),Math.sqrt(Math.max(0,1-h)));
  }
  if(mode==='area'&&points.length>=3){
    for(let i=0;i<points.length;i++){
      const a=points[i],b=points[(i+1)%points.length];
      let delta=(b[0]-a[0])*rad;if(delta>Math.PI)delta-=2*Math.PI;if(delta< -Math.PI)delta+=2*Math.PI;
      area+=delta*(2+Math.sin(a[1]*rad)+Math.sin(b[1]*rad));
    }
    area=Math.abs(area*R*R/2);area=Math.min(area,4*Math.PI*R*R-area);
    distance+=measureMap('distance',[points.at(-1)!,points[0]]).distance_m;
  }
  return {mode,points:points.map(p=>[...p] as MapPoint),distance_m:distance,area_m2:area};
}
export function measurementGeoJSON(measurement:Measurement):FeatureCollection {
  const {points,mode}=measurement;
  const features:Feature[]=points.map((point,i)=>({type:'Feature',id:i,properties:{kind:'vertex'},geometry:{type:'Point',coordinates:point}}));
  if(points.length>=2)features.unshift({type:'Feature',properties:{kind:'line'},geometry:{type:'LineString',coordinates:mode==='area'&&points.length>=3?[...points,points[0]]:points}});
  if(mode==='area'&&points.length>=3)features.unshift({type:'Feature',properties:{kind:'area'},geometry:{type:'Polygon',coordinates:[[...points,points[0]]]}});
  return {type:'FeatureCollection',features};
}
const obj=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const text=(v:unknown,max:number):v is string=>typeof v==='string'&&v.trim().length>0&&v.length<=max&&Array.from(v).every(character=>character.charCodeAt(0)>=32);
export function validateBookmarks(value:unknown):BookmarkFile {
  if(!obj(value)||value.schema_version!==1||value.kind!=='korea-replay-bookmarks'||!Array.isArray(value.bookmarks)||value.bookmarks.length>100)throw new Error('지원되는 즐겨찾기 파일이 아닙니다. 최대 100개까지 저장할 수 있습니다.');
  const ids=new Set<string>();
  const bookmarks=value.bookmarks.map(row=>{
    if(!obj(row)||!text(row.id,80)||!text(row.name,120)||!text(row.created_at,40)||!Number.isFinite(Date.parse(row.created_at))||!obj(row.position))throw new Error('즐겨찾기 항목을 확인해 주세요.');
    const p=row.position;
    if(ids.has(row.id)||!text(p.id,180)||!text(p.name,120)||!text(p.region,120)||!validMapPoint([p.lon,p.lat])||Number(p.lon)<124||Number(p.lon)>132.5||Number(p.lat)<32||Number(p.lat)>39.5||typeof p.range!=='number'||!Number.isFinite(p.range)||p.range<100||p.range>2500000)throw new Error('즐겨찾기 위치 또는 중복 ID를 확인해 주세요.');
    ids.add(row.id);
    return {id:row.id,name:row.name,created_at:row.created_at,position:{id:p.id,name:p.name,region:p.region,lon:p.lon as number,lat:p.lat as number,range:p.range}};
  });
  return {schema_version:1,kind:'korea-replay-bookmarks',bookmarks};
}
export function mergeBookmarks(existing:MapBookmark[],incoming:MapBookmark[]):MapBookmark[] {
  const result=new Map(existing.map(row=>[row.id,row]));
  for(const row of incoming)if(!result.has(row.id))result.set(row.id,row);
  return validateBookmarks({schema_version:1,kind:'korea-replay-bookmarks',bookmarks:[...result.values()]}).bookmarks;
}
export function formatDistance(m:number):string{return m>=1000?`${(m/1000).toLocaleString('ko-KR',{maximumFractionDigits:2})} km`:`${m.toLocaleString('ko-KR',{maximumFractionDigits:1})} m`;}
export function formatArea(m:number):string{return m>=1e6?`${(m/1e6).toLocaleString('ko-KR',{maximumFractionDigits:2})} km²`:`${m.toLocaleString('ko-KR',{maximumFractionDigits:1})} m²`;}
