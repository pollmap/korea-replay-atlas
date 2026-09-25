import {it,expect} from 'vitest';
import {validateStyleMin} from '@maplibre/maplibre-gl-style-spec';
import {regionSelectionLayers,SELECTED_REGION_SOURCE,REGION_DONG_SOURCE,SELECTED_DONG_SOURCE,DONG_HIT_LAYER} from '../src/region-selection-layers';

it('validates boundary fills, hover state, and outlines as fixed MapLibre layers',()=>{
  const sources=Object.fromEntries([SELECTED_REGION_SOURCE,REGION_DONG_SOURCE,SELECTED_DONG_SOURCE].map(key=>[key,{type:'geojson' as const,data:{type:'FeatureCollection' as const,features:[]}}]));
  const layers=regionSelectionLayers();
  expect(validateStyleMin({version:8,sources,layers})).toEqual([]);
  expect(layers).toHaveLength(6);
  expect(layers.find(row=>row.id===DONG_HIT_LAYER)?.minzoom).toBe(10);
  expect(new Set(layers.map(row=>row.id)).size).toBe(layers.length);
});
