import type {ExpressionSpecification,SymbolLayerSpecification} from 'maplibre-gl';
import {REGION_MAP_IMAGE} from './region-map-layer';

export const APARTMENT_MAP_LAYER='seoul-kapt-apartment-cards';
export const APARTMENT_SELECTED_LAYER='seoul-kapt-selected-card';
export type ApartmentLabelMode='price-area'|'price'|'unit-price'|'area'|'name';
/** A named card is the smallest property marker. Never replace identity with
 * a cluster count, or present apartment counts as transaction counts. */
export function apartmentMapLayer(source:string,release:string,trade:'sale'|'rent',selected=false,mode:ApartmentLabelMode='price-area',filterKey=''):SymbolLayerSpecification {
  const linked:ExpressionSpecification=['all',['has','property_complex_id'],['==',['get','property_release_id'],release]];
  const saleLabel:ExpressionSpecification=['case',['==',['slice',['to-string',['get','recent_sale_label']],0,6],'최근 신고 '],['slice',['to-string',['get','recent_sale_label']],6],['get','recent_sale_label']];
  const hasSale:ExpressionSpecification=['all',linked,['has','recent_sale_label'],['literal',trade==='sale']];
  const priceLabel:ExpressionSpecification=['case',['>=',['index-of',' · ',saleLabel],0],['slice',saleLabel,0,['index-of',' · ',saleLabel]],saleLabel];
  const unavailable:ExpressionSpecification=['case',linked,trade==='rent'?'전월세 미연결':'최근 거래 없음','단지 미연결'];
  const filteredValue:ExpressionSpecification=['coalesce',['get',mode==='unit-price'?'filtered_unit_label':mode==='price'?'filtered_price_label':'filtered_label'],'조건 확인 중'];
  const namedStatus=(value:ExpressionSpecification|string):ExpressionSpecification=>selected?['to-string',value]:['concat',['coalesce',['get','name'],'아파트'],'\n',value];
  const filtered:ExpressionSpecification=['case',['==',['get','property_filter_key'],filterKey],['case',['!=',['coalesce',['get','filtered_contract_date'],''],''],filteredValue,namedStatus(filteredValue)],namedStatus('조건 확인 중')];
  const detail:ExpressionSpecification=mode==='name'?['get','name']:filterKey?filtered:mode==='price-area'
    ?['case',hasSale,saleLabel,unavailable]:mode==='area'
    ?['case',['all',hasSale,['has','recent_sale_area_m2']],['concat',['to-string',['get','recent_sale_area_m2']],'㎡'],unavailable]
    :['case',hasSale,priceLabel,unavailable];
  return {id:selected?APARTMENT_SELECTED_LAYER:APARTMENT_MAP_LAYER,type:'symbol',source,minzoom:selected?10:12.5,
    filter:selected?['==',['get','kapt_code'],'']:['has','name'],
    layout:{'text-field':selected&&mode!=='name'?['format',['get','name'],{'font-scale':.9},'\n',{},detail,{'font-scale':1}]:detail,
      'text-font':['Malgun Gothic','sans-serif'],'text-size':selected?13:12,'text-line-height':1.1,'text-max-width':10,'text-padding':3,
      'text-allow-overlap':selected,'icon-allow-overlap':selected,'text-ignore-placement':false,'icon-ignore-placement':false,
      'icon-image':REGION_MAP_IMAGE,'icon-text-fit':'both','icon-text-fit-padding':[3,5,3,5],
      'symbol-sort-key':['case',linked,0,1]},
    paint:{'text-color':selected?'#5145cd':'#24344d','icon-opacity':1}};
}
