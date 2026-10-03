import type {LayerSpecification} from 'maplibre-gl';

export const SELECTED_REGION_SOURCE='selected-region-boundary';
export const REGION_DONG_SOURCE='selected-region-dongs';
export const SELECTED_DONG_SOURCE='selected-administrative-dong';
export const DONG_HIT_LAYER='selected-region-dong-hit';

/** A fixed number of GPU layers, regardless of the selected region's size. */
export function regionSelectionLayers():LayerSpecification[]{
  return [
    {id:'selected-region-fill',type:'fill',source:SELECTED_REGION_SOURCE,paint:{'fill-color':'#1765cf','fill-opacity':.07,'fill-opacity-transition':{duration:180}}},
    {id:'selected-region-outline',type:'line',source:SELECTED_REGION_SOURCE,paint:{'line-color':'#1765cf','line-width':2.5,'line-opacity':.9}},
    {id:DONG_HIT_LAYER,type:'fill',source:REGION_DONG_SOURCE,minzoom:10,paint:{'fill-color':'#1765cf','fill-opacity':['case',['boolean',['feature-state','hover'],false],.12,0]}},
    {id:'selected-region-dong-lines',type:'line',source:REGION_DONG_SOURCE,minzoom:10,paint:{'line-color':'#7292bb','line-width':1,'line-opacity':.5}},
    {id:'selected-dong-fill',type:'fill',source:SELECTED_DONG_SOURCE,paint:{'fill-color':'#1765cf','fill-opacity':.15}},
    {id:'selected-dong-outline',type:'line',source:SELECTED_DONG_SOURCE,paint:{'line-color':'#1459b8','line-width':3}},
    {id:'selected-region-dong-labels',type:'symbol',source:REGION_DONG_SOURCE,minzoom:10,
      filter:['all',['has','name'],['!=',['get','name'],'']],
      layout:{'text-field':['get','name'],'text-font':['Malgun Gothic','sans-serif'],'text-size':12,'text-padding':4,'text-max-width':10,'text-allow-overlap':false},
      paint:{'text-color':'#36577e','text-halo-color':'#ffffff','text-halo-width':1.5}},
  ];
}
