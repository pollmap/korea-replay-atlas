import type {ExpressionSpecification,SymbolLayerSpecification} from 'maplibre-gl';
import {REGION_MAP_IMAGE} from './region-map-layer';

export const APARTMENT_MAP_LAYER='seoul-kapt-apartment-cards';
export const APARTMENT_SELECTED_LAYER='seoul-kapt-selected-card';
/** A named card is the smallest property marker. Never replace identity with
 * a cluster count, or present apartment counts as transaction counts. */
export function apartmentMapLayer(source:string,release:string,trade:'sale'|'rent',selected=false):SymbolLayerSpecification {
  const linked:ExpressionSpecification=['all',['has','property_complex_id'],['==',['get','property_release_id'],release]];
  const saleLabel:ExpressionSpecification=['case',['==',['slice',['to-string',['get','recent_sale_label']],0,6],'최근 신고 '],['slice',['to-string',['get','recent_sale_label']],6],['get','recent_sale_label']];
  const hasSale:ExpressionSpecification=['all',linked,['has','recent_sale_label'],['literal',trade==='sale']];
  const detail:ExpressionSpecification=['case',hasSale,saleLabel,linked,'실거래 보기','공식 단지 정보'];
  return {id:selected?APARTMENT_SELECTED_LAYER:APARTMENT_MAP_LAYER,type:'symbol',source,minzoom:selected?10:13,
    filter:selected?['==',['get','kapt_code'],'']:['has','name'],
    layout:{'text-field':selected?['format',['get','name'],{'font-scale':.9},'\n',{},detail,{'font-scale':1}]:['case',hasSale,saleLabel,['get','name']],
      'text-font':['Malgun Gothic','sans-serif'],'text-size':selected?13:12,'text-line-height':1.1,'text-max-width':10,'text-padding':3,
      'text-allow-overlap':selected,'icon-allow-overlap':selected,'text-ignore-placement':false,'icon-ignore-placement':false,
      'icon-image':REGION_MAP_IMAGE,'icon-text-fit':'both','icon-text-fit-padding':[3,5,3,5],
      'symbol-sort-key':['case',linked,0,1]},
    paint:{'text-color':selected?'#5145cd':'#24344d','icon-opacity':1}};
}
