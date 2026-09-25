import type {FeatureCollection,Point} from 'geojson';
import type {LayerSpecification} from 'maplibre-gl';
import type {MapCatalog2D} from '../shared/map-tiles';
import type {PropertyRegions,PropertyRelease} from '../shared/property';
import {regionNavigation} from './region-navigation';

export const REGION_MAP_SOURCE='property-region-navigation';
export const REGION_MAP_LAYER='property-region-volume-labels';
export const PROVINCE_MAP_LAYER='property-province-volume-labels';
export const REGION_MAP_IMAGE='property-region-volume-bubble';
export interface RegionMapInput {map:Pick<MapCatalog2D,'reference_dates'>;property:Pick<PropertyRelease,'release_id'|'period'>;regions:PropertyRegions;trade?:'sale'|'rent';}
export interface RegionMapProperties {
  property_region_code:string;property_release:string;region_name:string;display_name:string;
  contract_month:string;trade_type:'sale'|'rent';count:number;count_label:string;month_label:string;sort_key:number;
  anchor_purpose:'region-navigation-only';anchor_source_record_id:string;anchor_reference_date:string;
}
export interface RegionMapData {
  data:FeatureCollection<Point,RegionMapProperties>;month:string;releaseId:string;total:number;excluded:number;
  provinces:FeatureCollection<Point,ProvinceMapProperties>;
  caption:string;notice:string;
}
export interface ProvinceMapProperties {
  property_province_name:string;property_release:string;display_name:string;count_label:string;
  member_count:number;contract_month:string;trade_type:'sale'|'rent';anchor_purpose:'province-navigation-only';
}
const shortName=(name:string)=>name.trim().replace(/\s+/g,' ').replace(/^(서울|부산|대구|인천|광주|대전|울산)(?:특별시|광역시) /,'$1 ').replace(/^경기도 /,'경기 ').replace(/^강원특별자치도 /,'강원 ').replace(/^전북특별자치도 /,'전북 ').replace(/^제주특별자치도 /,'제주 ').replace(/^충청북도 /,'충북 ').replace(/^충청남도 /,'충남 ').replace(/^전라남도 /,'전남 ').replace(/^경상북도 /,'경북 ').replace(/^경상남도 /,'경남 ');
const provinceShortName=(name:string)=>({서울특별시:'서울',부산광역시:'부산',대구광역시:'대구',인천광역시:'인천',광주광역시:'광주',대전광역시:'대전',울산광역시:'울산',세종특별자치시:'세종',경기도:'경기',강원특별자치도:'강원',충청북도:'충북',충청남도:'충남',전북특별자치도:'전북',전라남도:'전남',경상북도:'경북',경상남도:'경남',제주특별자치도:'제주'} as Record<string,string>)[name]??name;

/** Markers are region-list shortcuts, not a boundary join or apartment position.
 * Only published counts from the same completed contract month are displayed.
 * Unmapped or incomplete regions remain absent, never a manufactured zero.
 */
export function regionMapData(input:RegionMapInput|null|undefined):RegionMapData {
  const data:FeatureCollection<Point,RegionMapProperties>={type:'FeatureCollection',features:[]};
  const month=input?.property.period.latest_complete_month??'',releaseId=input?.property.release_id??'',total=input?.regions.regions.length??0,trade=input?.trade??'sale';
  const valid=!!input&&input.regions.release_id===releaseId&&/^\d{4}(?:0[1-9]|1[0-2])$/.test(month)&&month>=input.property.period.from&&month<=input.property.period.to;
  if(valid){
    const occurrences=new Map<string,number>();
    for(const row of input.regions.regions)occurrences.set(row.lawd_code,(occurrences.get(row.lawd_code)??0)+1);
    for(const row of input.regions.regions){
      const metric=row.latest[trade],count=metric.eligible_rows;
      if(occurrences.get(row.lawd_code)!==1||!/^\d{5}$/.test(row.lawd_code)||metric.lawd_code!==row.lawd_code||metric.trade_type!==trade||metric.deal_month!==month||!['complete','empty'].includes(metric.status)||!Number.isSafeInteger(count)||count===null||count<0||metric.status==='empty'&&count!==0)continue;
      const location=regionNavigation(row,input.map);if(!location)continue;
      data.features.push({type:'Feature',id:`${releaseId}:${row.lawd_code}`,geometry:{type:'Point',coordinates:[location.place.lon,location.place.lat]},
        properties:{property_region_code:row.lawd_code,property_release:releaseId,region_name:row.name.trim(),display_name:shortName(row.name),contract_month:month,trade_type:trade,count,count_label:`${String(count).replace(/\B(?=(\d{3})+(?!\d))/g,',')}건`,month_label:`${month.slice(2,4)}.${month.slice(4)} ${trade==='sale'?'매매':'전월세'}`,sort_key:-count,
          anchor_purpose:'region-navigation-only',anchor_source_record_id:location.sourceRecordId,anchor_reference_date:location.boundaryReferenceDate}});
    }
  }
  return {data,provinces:provinceMapData(valid?input:null),month,releaseId,total,excluded:total-data.features.length,
    caption:valid?`지역별 ${trade==='sale'?'매매':'전월세'} 거래량 · ${month.slice(0,4)}.${month.slice(4)} · 지역 탐색 위치`:`지역별 ${trade==='sale'?'매매':'전월세'} 거래량 · 자료 확인 중`,
    notice:`최신 완료 계약월의 ${trade==='sale'?'취소·통계 제외 건을 뺀 매매':'통계 제외 건을 뺀 전월세'} 신고 거래량입니다. 패널의 거래 유형과 연동되며, 과거 계약월 선택과는 별개로 최신 완료월을 표시합니다. 점은 지역 이름으로 연결한 SGIS 2025-06-30 탐색 위치이며 단지 좌표나 현행 법정동 경계의 통계 결합이 아닙니다. ${data.features.length}개 지역 표시, 미연결·미수집·검증 제외 ${total-data.features.length}개 지역은 0건으로 표시하지 않습니다.`};
}

/** National overview labels use published rows only; one missing district suppresses
 * the province total rather than silently reporting a partial total as complete. */
export function provinceMapData(input:RegionMapInput|null|undefined):FeatureCollection<Point,ProvinceMapProperties> {
  const data:FeatureCollection<Point,ProvinceMapProperties>={type:'FeatureCollection',features:[]};
  if(!input||input.regions.release_id!==input.property.release_id)return data;
  const month=input.property.period.latest_complete_month,trade=input.trade??'sale';
  if(!/^\d{4}(?:0[1-9]|1[0-2])$/.test(month)||month<input.property.period.from||month>input.property.period.to)return data;
  const groups=new Map<string,typeof input.regions.regions>();
  for(const row of input.regions.regions){const name=row.name.trim().split(/\s+/)[0];const group=groups.get(name)??[];group.push(row);groups.set(name,group);}
  for(const [name,rows] of groups){
    const navigation=regionNavigation({name},input.map);if(!navigation||navigation.sourceRecordId.split(':')[2]!=='sido')continue;
    const seen=new Set<string>();let count=0,valid=true;
    for(const row of rows){
      const metric=row.latest[trade],value=metric.eligible_rows;
      if(seen.has(row.lawd_code)||!/^[0-9]{5}$/.test(row.lawd_code)||metric.lawd_code!==row.lawd_code||metric.trade_type!==trade||metric.deal_month!==month||!['complete','empty'].includes(metric.status)||!Number.isSafeInteger(value)||value===null||value<0||metric.status==='empty'&&value!==0){valid=false;break;}
      seen.add(row.lawd_code);count+=value;
      if(!Number.isSafeInteger(count)){valid=false;break;}
    }
    if(!valid||!rows.length)continue;
    data.features.push({type:'Feature',id:`${input.property.release_id}:${name}`,geometry:{type:'Point',coordinates:[navigation.place.lon,navigation.place.lat]},properties:{property_province_name:name,property_release:input.property.release_id,display_name:provinceShortName(name),count_label:`${count.toLocaleString('ko-KR')}건`,member_count:rows.length,contract_month:month,trade_type:trade,anchor_purpose:'province-navigation-only'}});
  }
  return data;
}

export function provinceMapLayer():LayerSpecification {
  return {id:PROVINCE_MAP_LAYER,type:'symbol',source:REGION_MAP_SOURCE,minzoom:3,maxzoom:6.5,
    filter:['has','property_province_name'],
    layout:{'text-field':['format',['get','display_name'],{'font-scale':1},'\n',{},['get','count_label'],{'font-scale':1.05}],
      'text-font':['Malgun Gothic','sans-serif'],'text-size':13,'text-line-height':1.25,'text-max-width':8,'text-padding':5,'text-allow-overlap':false,
      'icon-image':REGION_MAP_IMAGE,'icon-text-fit':'both','icon-text-fit-padding':[4,6,4,6],'icon-allow-overlap':false},paint:{'text-color':'#102b46','icon-opacity':.98}};
}

export function regionMapLayer():LayerSpecification {
  return {id:REGION_MAP_LAYER,type:'symbol',source:REGION_MAP_SOURCE,minzoom:6.5,
    filter:['has','property_region_code'],
    layout:{'symbol-sort-key':['get','sort_key'],'symbol-z-order':'source','text-field':['format',['get','display_name'],{'font-scale':1},'\n',{},['get','count_label'],{'font-scale':1.1},'\n',{},['get','month_label'],{'font-scale':.7}],
      'text-font':['Malgun Gothic','sans-serif'],'text-size':14,'text-line-height':1.25,'text-max-width':10,'text-padding':8,'text-allow-overlap':false,'text-ignore-placement':false,
      'icon-image':REGION_MAP_IMAGE,'icon-text-fit':'both','icon-text-fit-padding':[5,8,5,8],'icon-allow-overlap':false,'icon-ignore-placement':false},
    paint:{'text-color':'#102b46','icon-opacity':.98}};
}

/** One stretchable RGBA sprite shared by every label; no HTML markers or per-region images. */
export function regionMapBubbleImage(){
  const width=80,height=64,radius=12,data=new Uint8Array(width*height*4);
  const inside=(x:number,y:number,inset:number)=>{const dx=Math.max(Math.abs(x-width/2)-(width/2-radius),0),dy=Math.max(Math.abs(y-height/2)-(height/2-radius),0);return Math.hypot(dx,dy)<=radius-inset&&x>=inset&&x<=width-inset&&y>=inset&&y<=height-inset;};
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
    if(!inside(x+.5,y+.5,0))continue;
    const offset=(y*width+x)*4,stroke=!inside(x+.5,y+.5,2);
    data[offset]=stroke?37:255;data[offset+1]=stroke?99:255;data[offset+2]=stroke?235:255;data[offset+3]=255;
  }
  return {width,height,data};
}

interface Hit {source?:string;layer?:{id?:string};properties?:Record<string,unknown>|null;}
/** Revalidate the currently displayed release; unrelated map picks cannot impersonate a region. */
export function pickedPropertyRegion(hits:readonly Hit[],current:RegionMapData,measurementMode='none'):string|null {
  if(measurementMode!=='none')return null;
  for(const hit of hits){
    if(hit.source!==REGION_MAP_SOURCE||hit.layer?.id!==REGION_MAP_LAYER||hit.properties?.property_release!==current.releaseId)continue;
    const code=hit.properties.property_region_code;
    if(typeof code==='string'&&current.data.features.some(row=>row.properties.property_region_code===code))return code;
  }
  return null;
}

export function pickedPropertyProvince(hits:readonly Hit[],current:RegionMapData,measurementMode='none'):[number,number]|null {
  if(measurementMode!=='none')return null;
  for(const hit of hits){
    if(hit.source!==REGION_MAP_SOURCE||hit.layer?.id!==PROVINCE_MAP_LAYER||hit.properties?.property_release!==current.releaseId)continue;
    const name=hit.properties.property_province_name;
    const feature=current.provinces.features.find(row=>row.properties.property_province_name===name);
    if(feature)return feature.geometry.coordinates as [number,number];
  }
  return null;
}
