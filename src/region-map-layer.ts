import type {FeatureCollection,Point} from 'geojson';
import type {LayerSpecification} from 'maplibre-gl';
import type {MapCatalog2D} from '../shared/map-tiles';
import type {PropertyRegions,PropertyRelease} from '../shared/property';
import {regionNavigation} from './region-navigation';

export const REGION_MAP_SOURCE='property-region-navigation';
export const REGION_MAP_LAYER='property-region-volume-labels';
export const REGION_MAP_IMAGE='property-region-volume-bubble';
export interface RegionMapInput {map:Pick<MapCatalog2D,'reference_dates'>;property:Pick<PropertyRelease,'release_id'|'period'>;regions:PropertyRegions;}
export interface RegionMapProperties {
  property_region_code:string;property_release:string;region_name:string;display_name:string;
  contract_month:string;trade_type:'sale';count:number;count_label:string;month_label:string;sort_key:number;
  anchor_purpose:'region-navigation-only';anchor_source_record_id:string;anchor_reference_date:string;
}
export interface RegionMapData {
  data:FeatureCollection<Point,RegionMapProperties>;month:string;releaseId:string;total:number;excluded:number;
  caption:string;notice:string;
}
const shortName=(name:string)=>name.trim().replace(/\s+/g,' ').replace(/^(서울|부산|대구|인천|광주|대전|울산)(?:특별시|광역시) /,'$1 ').replace(/^경기도 /,'경기 ').replace(/^강원특별자치도 /,'강원 ').replace(/^전북특별자치도 /,'전북 ').replace(/^제주특별자치도 /,'제주 ').replace(/^충청북도 /,'충북 ').replace(/^충청남도 /,'충남 ').replace(/^전라남도 /,'전남 ').replace(/^경상북도 /,'경북 ').replace(/^경상남도 /,'경남 ');

/** Markers are region-list shortcuts, not a boundary join or apartment position.
 * Only published counts from the same completed contract month are displayed.
 * Unmapped or incomplete regions remain absent, never a manufactured zero.
 */
export function regionMapData(input:RegionMapInput|null|undefined):RegionMapData {
  const data:FeatureCollection<Point,RegionMapProperties>={type:'FeatureCollection',features:[]};
  const month=input?.property.period.latest_complete_month??'',releaseId=input?.property.release_id??'',total=input?.regions.regions.length??0;
  const valid=!!input&&input.regions.release_id===releaseId&&/^\d{4}(?:0[1-9]|1[0-2])$/.test(month)&&month>=input.property.period.from&&month<=input.property.period.to;
  if(valid){
    const occurrences=new Map<string,number>();
    for(const row of input.regions.regions)occurrences.set(row.lawd_code,(occurrences.get(row.lawd_code)??0)+1);
    for(const row of input.regions.regions){
      const metric=row.latest.sale,count=metric.eligible_rows;
      if(occurrences.get(row.lawd_code)!==1||!/^\d{5}$/.test(row.lawd_code)||metric.lawd_code!==row.lawd_code||metric.trade_type!=='sale'||metric.deal_month!==month||!['complete','empty'].includes(metric.status)||!Number.isSafeInteger(count)||count===null||count<0||metric.status==='empty'&&count!==0)continue;
      const location=regionNavigation(row,input.map);if(!location)continue;
      data.features.push({type:'Feature',id:`${releaseId}:${row.lawd_code}`,geometry:{type:'Point',coordinates:[location.place.lon,location.place.lat]},
        properties:{property_region_code:row.lawd_code,property_release:releaseId,region_name:row.name.trim(),display_name:shortName(row.name),contract_month:month,trade_type:'sale',count,count_label:`${String(count).replace(/\B(?=(\d{3})+(?!\d))/g,',')}건`,month_label:`${month.slice(2,4)}.${month.slice(4)} 매매`,sort_key:-count,
          anchor_purpose:'region-navigation-only',anchor_source_record_id:location.sourceRecordId,anchor_reference_date:location.boundaryReferenceDate}});
    }
  }
  return {data,month,releaseId,total,excluded:total-data.features.length,
    caption:valid?`지역별 매매 거래량 · ${month.slice(0,4)}.${month.slice(4)} · 지역 탐색 위치`:'지역별 매매 거래량 · 자료 확인 중',
    notice:`최신 완료 계약월의 신고 자료에서 취소·통계 제외 건을 뺀 매매 거래량입니다. 패널의 기간·전월세 필터와 별개인 지도 탐색 지표입니다. 점은 지역 이름으로 연결한 SGIS 2025-06-30 탐색 위치이며 단지 좌표나 현행 법정동 경계의 통계 결합이 아닙니다. ${data.features.length}개 지역 표시, 미연결·미수집·검증 제외 ${total-data.features.length}개 지역은 0건으로 표시하지 않습니다.`};
}

export function regionMapLayer():LayerSpecification {
  return {id:REGION_MAP_LAYER,type:'symbol',source:REGION_MAP_SOURCE,minzoom:3,
    layout:{'symbol-sort-key':['get','sort_key'],'symbol-z-order':'source','text-field':['format',['get','display_name'],{'font-scale':.85},'\n',{},['get','count_label'],{'font-scale':1.1},'\n',{},['get','month_label'],{'font-scale':.7}],
      'text-font':['Malgun Gothic','sans-serif'],'text-size':['interpolate',['linear'],['zoom'],3,11,8,12,11,13],'text-line-height':1.2,'text-max-width':10,'text-padding':8,'text-allow-overlap':false,'text-ignore-placement':false,
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
