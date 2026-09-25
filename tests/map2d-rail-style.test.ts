import {describe,expect,it} from 'vitest';
import {createExpression} from '@maplibre/maplibre-gl-style-spec';
import {MAP2D_RAIL_NEUTRAL,map2DRailColor,map2DRailColorExpression} from '../shared/map2d-rail-style';
import identities from '../shared/data/map2d-rail-colors.json';
import {SEOUL_METRO_DISPLAY_COLORS} from '../shared/rail-style';

describe('2D route colours with preserved source identity',()=>{
  it('uses operator colours for explicit Seoul line names and evidence-linked bare names',()=>{
    expect(map2DRailColor({name:'서울 지하철 2호선'})).toBe('#2fae35');
    expect(map2DRailColor({name:'2호선',source_id:'osm',stable_id:'000d1badafbffcc0'})).toBe('#2fae35');
    expect(map2DRailColor({name:'2호선',source_id:'osm'},'way/640443237')).toBe('#2fae35');
    for(const [id,row] of Object.entries(identities.records)){
      expect(map2DRailColor({source_id:'osm',stable_id:id})).toBe(SEOUL_METRO_DISPLAY_COLORS[row.line]);
      expect(map2DRailColor({source_id:'osm'},row.source_record_id)).toBe(SEOUL_METRO_DISPLAY_COLORS[row.line]);
    }
  });
  it('does not guess from bare numbers, station names, general railway names or a different source',()=>{
    for(const name of ['2호선','부산 도시철도 2호선','대구 도시철도 2호선','인천 도시철도 2호선','강남','경부선','constructor','__proto__']){
      expect(map2DRailColor({name,source_id:'osm'})).toBe(MAP2D_RAIL_NEUTRAL);
    }
    expect(map2DRailColor({name:'2호선',source_id:'other',stable_id:'000d1badafbffcc0'})).toBe(MAP2D_RAIL_NEUTRAL);
    expect(map2DRailColor({source_id:'osm'},'way/unknown')).toBe(MAP2D_RAIL_NEUTRAL);
  });
  it('keeps the MVT expression and fallback lookup identical for all published identities',()=>{
    const parsed=createExpression(map2DRailColorExpression(),'layers[0].paint.line-color');
    expect(parsed.result).toBe('success');
    if(parsed.result!=='success')throw new Error('Invalid colour expression');
    const rows=[{name:'서울 지하철 2호선'}, {name:'부산 도시철도 2호선'}, {name:'2호선'},
      ...Object.keys(identities.records).map(stable_id=>({name:'',source_id:'osm',stable_id}))];
    for(const properties of rows){
      expect(parsed.value.evaluate({zoom:12},{type:2,properties})).toBe(map2DRailColor(properties));
    }
  });
});
