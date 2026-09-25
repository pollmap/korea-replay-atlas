import type {ExpressionSpecification} from 'maplibre-gl';
import {SEOUL_METRO_DISPLAY_COLORS} from './rail-style';
import identities from './data/map2d-rail-colors.json';

/** Identity evidence is OSM route membership; colours are the operator's web
 * display colours. Neither establishes surveyed geometry or a future opening.
 * See docs/MAP2D_RAIL_COLORS.md. No 3D style or source data is changed. */
export const MAP2D_RAIL_NEUTRAL='#97857d';
const explicitNames:Record<string,string>={};
for(const [line,color] of Object.entries(SEOUL_METRO_DISPLAY_COLORS)){
  for(const network of ['서울 지하철','수도권 전철'])explicitNames[`${network} ${line}호선`]=color;
}
const stableColors:Record<string,string>={},recordColors:Record<string,string>={};
for(const [stable,record] of Object.entries(identities.records)){
  stableColors[stable]=SEOUL_METRO_DISPLAY_COLORS[record.line];
  recordColors[record.source_record_id]=SEOUL_METRO_DISPLAY_COLORS[record.line];
}

/** For fallback GeoJSON: pass the real OSM source record ID, never a guessed
 * network from a bare line number, current camera position or station name. */
export function map2DRailColor(properties:Record<string,unknown>,sourceRecordId?:string):string {
  const name=typeof properties.name==='string'?properties.name:'';
  if(Object.prototype.hasOwnProperty.call(explicitNames,name))return explicitNames[name];
  if(properties.source_id!=='osm')return MAP2D_RAIL_NEUTRAL;
  const stable=typeof properties.stable_id==='string'?properties.stable_id:'';
  if(Object.prototype.hasOwnProperty.call(stableColors,stable))return stableColors[stable];
  const record=sourceRecordId??(typeof properties.source_record_id==='string'?properties.source_record_id:'');
  return Object.prototype.hasOwnProperty.call(recordColors,record)?recordColors[record]:MAP2D_RAIL_NEUTRAL;
}

const match=(property:string,entries:Record<string,string>,fallback:string|ExpressionSpecification):ExpressionSpecification=>{
  const [first,...rest]=Object.entries(entries);
  if(!first)return ['coalesce',fallback];
  // A match expression requires at least one label/output pair. Preserve that
  // tuple explicitly so the type checker can prove the minimum arity.
  return ['match',['coalesce',['get',property],''],first[0],first[1],
    ...rest.flatMap(([key,value])=>[key,value]),fallback];
};

/** Every vector rail presentation uses the same bounded, immutable lookup. */
export function map2DRailColorExpression():ExpressionSpecification {
  return match('name',explicitNames,['case',['==',['get','source_id'],'osm'],match('stable_id',stableColors,MAP2D_RAIL_NEUTRAL),MAP2D_RAIL_NEUTRAL]);
}
