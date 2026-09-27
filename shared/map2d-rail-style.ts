import type {ExpressionSpecification} from 'maplibre-gl';
import {SEOUL_METRO_DISPLAY_COLORS} from './rail-style';
import identities from './data/map2d-rail-colors.json';

/** Identity evidence is OSM route membership; colours come from official web
 * display colours. Neither establishes surveyed geometry or a future opening.
 * See docs/MAP2D_RAIL_COLORS.md. No 3D style or source data is changed. */
export const MAP2D_RAIL_NEUTRAL='#97857d';
const explicitNames:Record<string,string>={};
for(const [line,color] of Object.entries(SEOUL_METRO_DISPLAY_COLORS)){
  for(const network of ['서울 지하철','수도권 전철'])explicitNames[`${network} ${line}호선`]=color;
}
const stableColors:Record<string,string>={},recordColors:Record<string,string>={};
const routeColors:Record<string,string>={...SEOUL_METRO_DISPLAY_COLORS,
  ...Object.fromEntries(Object.entries(identities.display_colors).map(([line,evidence])=>[line,evidence.web_display_rgb]))};
for(const [stable,record] of Object.entries(identities.records)){
  stableColors[stable]=routeColors[record.line];
  recordColors[record.source_record_id]=routeColors[record.line];
}

function readableOnWhite(hex:string):string {
  if(!/^#[\da-f]{6}$/i.test(hex))throw new Error('Invalid rail palette colour');
  const rgb=[1,3,5].map(start=>Number.parseInt(hex.slice(start,start+2),16));
  const contrast=(channels:number[])=>{
    const linear=channels.map(value=>{const s=value/255;return s<=.04045?s/12.92:((s+.055)/1.055)**2.4;});
    return 1.05/(.2126*linear[0]+.7152*linear[1]+.0722*linear[2]+.05);
  };
  if(contrast(rgb)>=4.5)return hex;
  // Preserve the colour family by mixing with black. A 5:1 target gives small
  // labels extra headroom beyond WCAG's unrounded 4.5:1 minimum.
  let low=0,high=1;
  for(let iteration=0;iteration<20;iteration++){
    const middle=(low+high)/2;
    if(contrast(rgb.map(value=>Math.floor(value*middle)))>=5)low=middle;else high=middle;
  }
  return '#'+rgb.map(value=>Math.floor(value*low).toString(16).padStart(2,'0')).join('');
}

// Transform only the small unique palette once during module initialization,
// never per map feature or render frame. Track/border colours remain unchanged.
const labelColors=Object.fromEntries([...new Set([MAP2D_RAIL_NEUTRAL,...Object.values(routeColors)])]
  .map(color=>[color,readableOnWhite(color)]));

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

export function map2DRailLabelColor(properties:Record<string,unknown>,sourceRecordId?:string):string {
  return labelColors[map2DRailColor(properties,sourceRecordId)];
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

/** Contrast-safe text lookup layered over the exact same route identity rules. */
export function map2DRailLabelColorExpression():ExpressionSpecification {
  const [first,...rest]=Object.entries(labelColors);
  return ['match',map2DRailColorExpression(),first[0],first[1],
    ...rest.flatMap(([color,label])=>[color,label]),labelColors[MAP2D_RAIL_NEUTRAL]];
}

/** Operational crossover/siding/yard names are not passenger route names.
 * Keep their track geometry and colour, excluding only evidence-linked labels. */
export function map2DRailLabelFilter():ExpressionSpecification {
  // Match compiles labels into keyed cases; `in` would scan 1,118 IDs per feature.
  return ['case', ['==', ['get', 'source_id'], 'osm'],
    ['match', ['coalesce', ['get', 'stable_id'], ''], Object.keys(identities.suppressed_labels), false, true], true];
}
