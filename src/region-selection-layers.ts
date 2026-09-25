import type {LayerSpecification} from 'maplibre-gl';

export const SELECTED_REGION_SOURCE='selected-region-boundary';
export const REGION_DONG_SOURCE='selected-region-dongs';
export const SELECTED_DONG_SOURCE='selected-administrative-dong';
export const DONG_HIT_LAYER='selected-region-dong-hit';

/** A fixed number of GPU layers, regardless of the selected region's size. */
export function regionSelectionLayers():LayerSpecification[]{
  return [
    {id:'selected-region-fill',type:'fill',source:SELECTED_REGION_SOURCE,paint:{'fill-color':'#8054d9','fill-opacity':.07,'fill-opacity-transition':{duration:180}}},
    {id:'selected-region-outline',type:'line',source:SELECTED_REGION_SOURCE,paint:{'line-color':'#7140c7','line-width':2.5,'line-opacity':.9}},
    {id:DONG_HIT_LAYER,type:'fill',source:REGION_DONG_SOURCE,minzoom:10,paint:{'fill-color':'#9163db','fill-opacity':['case',['boolean',['feature-state','hover'],false],.12,0]}},
    {id:'selected-region-dong-lines',type:'line',source:REGION_DONG_SOURCE,minzoom:10,paint:{'line-color':'#9779b9','line-width':1,'line-opacity':.5}},
    {id:'selected-dong-fill',type:'fill',source:SELECTED_DONG_SOURCE,paint:{'fill-color':'#7541ce','fill-opacity':.15}},
    {id:'selected-dong-outline',type:'line',source:SELECTED_DONG_SOURCE,paint:{'line-color':'#6330ba','line-width':3}},
  ];
}
