import type {ExpressionSpecification,SymbolLayerSpecification} from 'maplibre-gl';
import {REGION_MAP_IMAGE} from './region-map-layer';

export const APARTMENT_MAP_LAYER='seoul-kapt-apartment-cards';
export const APARTMENT_SELECTED_LAYER='seoul-kapt-selected-card';
/** A named card is the smallest property marker. Never replace identity with
 * a cluster count, or present apartment counts as transaction counts. */
export function apartmentMapLayer(source:string,release:string,trade:'sale'|'rent',selected=false):SymbolLayerSpecification {
  const linked:ExpressionSpecification=['all',['has','property_complex_id'],['==',['get','property_release_id'],release]];
  const saleLabel:ExpressionSpecification=['case',['==',['slice',['to-string',['get','recent_sale_label']],0,6],'최근 신고 '],['slice',['to-string',['get','recent_sale_label']],6],['get','recent_sale_label']];
  return {id:selected?APARTMENT_SELECTED_LAYER:APARTMENT_MAP_LAYER,type:'symbol',source,minzoom:selected?10:13,
    filter:selected?['==',['get','kapt_code'],'']:['has','name'],
    layout:{'text-field':['format',['get','name'],{'font-scale':1},'\n',{},
      ['case',['all',linked,['has','recent_sale_label'],['literal',trade==='sale']],saleLabel,linked,'실거래 보기','공식 단지 정보'],{'font-scale':.85},
      ['case',['all',linked,['has','recent_sale_contract_date'],['literal',trade==='sale']],['concat','\n',['get','recent_sale_contract_date']],''],{'font-scale':.75}],
      'text-font':['Malgun Gothic','sans-serif'],'text-size':13,'text-line-height':1.25,'text-max-width':12,'text-padding':5,
      'text-allow-overlap':selected,'icon-allow-overlap':selected,'text-ignore-placement':false,'icon-ignore-placement':false,
      'icon-image':REGION_MAP_IMAGE,'icon-text-fit':'both','icon-text-fit-padding':[6,8,6,8],
      'symbol-sort-key':['case',linked,0,1]},
    paint:{'text-color':selected?'#5145cd':'#24344d','icon-opacity':1}};
}
