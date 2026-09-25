import type {LayerSpecification} from 'maplibre-gl';
import type {MapTileTopic} from '../shared/map-tiles';
import {map2DRailColorExpression} from '../shared/map2d-rail-style';

/** Fixed layer count per semantic topic, independent of archive count. */
export function vectorLayers(topic:MapTileTopic,source:string):LayerSpecification[]{
  const buildingOverview=/^buildings-overview-[0-3]$/.test(topic.id);
  const kind=buildingOverview?'buildings':topic.id==='detail-roads'?'roads':topic.id;
  const base={source,'source-layer':topic.source_layer},id=`vector-${topic.id}`,admin=kind.startsWith('admin-');
  if(admin)return [{...base,id,type:'line',filter:['==',['geometry-type'],'LineString'],minzoom:kind==='admin-dong'?11:kind==='admin-sigungu'?7:0,paint:{'line-color':kind==='admin-sido'?'#94a9bf':'#b7c5d5','line-width':kind==='admin-sido'?1.4:.8,'line-dasharray':kind==='admin-sido'?[4,2]:[2,3],'line-opacity':.8}},{...base,id:`${id}-label`,type:'symbol',minzoom:kind==='admin-dong'?11:kind==='admin-sigungu'?8:0,maxzoom:kind==='admin-sido'?10:24,filter:['==',['geometry-type'],'Point'],layout:{'text-field':['get','name'],'text-font':['Malgun Gothic','sans-serif'],'text-size':kind==='admin-sido'?13:11,'text-padding':15},paint:{'text-color':'#728297','text-halo-color':'#ffffff','text-halo-width':1.5}}];
  const rows:LayerSpecification[]=[];
  if(kind==='buildings')rows.push({...base,id:`${id}-outline`,type:'line',filter:['==',['geometry-type'],'LineString'],paint:{'line-color':'#aab8c8','line-width':1}});
  if(['land','water','buildings','facilities'].includes(kind))rows.push({...base,id:`${id}-fill`,type:'fill',filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':kind==='water'?'#9fd5e8':kind==='buildings'?'#e0ddd4':kind==='land'?'#f5f1e8':'#d3e9da','fill-outline-color':kind==='buildings'?'#cac6bb':kind==='water'?'#9fd5e8':'#e0e8ed','fill-opacity':.95}});
  if(['roads','rail','water'].includes(kind))rows.push({...base,id:`${id}-line`,type:'line',filter:['==',['geometry-type'],'LineString'],layout:{'line-join':'round','line-cap':'round'},paint:{'line-color':kind==='rail'?map2DRailColorExpression():kind==='water'?'#8ccddd':['match',['coalesce',['get','highway'],['get','class'],'other'],['motorway','motorway_link'],'#d89577',['trunk','trunk_link'],'#e2b875',['primary','primary_link'],'#e7ce89','#fffdf4'],'line-width':['interpolate',['linear'],['zoom'],4,.5,9,1.2,13,2.6,17,6],'line-opacity':kind==='rail'?.8:1}});
  rows.push({...base,id:`${id}-point`,type:'circle',minzoom:8,filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':['interpolate',['linear'],['zoom'],8,1.2,16,3],'circle-color':kind==='rail'?'#ffffff':kind==='water'?'#8ccddd':'#91a7b6','circle-stroke-color':kind==='rail'?map2DRailColorExpression():'#ffffff','circle-stroke-width':kind==='rail'?1.5:0}});
  if(['roads','rail','facilities'].includes(kind))rows.push({...base,id:`${id}-label`,type:'symbol',minzoom:kind==='roads'?13:11,filter:['all',['has','name'],['!=',['get','name'],'']],layout:{'symbol-placement':kind==='roads'?'line':'point','text-field':['get','name'],'text-font':['Malgun Gothic','sans-serif'],'text-size':11,'text-max-width':8,'text-padding':12,'text-allow-overlap':false},paint:{'text-color':kind==='rail'?map2DRailColorExpression():'#728297','text-halo-color':'#ffffff','text-halo-width':1.3}});
  return buildingOverview?rows.map(layer=>({...layer,minzoom:12,maxzoom:14})):rows;
}
