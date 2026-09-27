import {describe,expect,it} from 'vitest';
import {createExpression} from '@maplibre/maplibre-gl-style-spec';
import {MAP2D_RAIL_NEUTRAL,map2DRailColor,map2DRailColorExpression,map2DRailLabelFilter,map2DRailLabelColor,map2DRailLabelColorExpression} from '../shared/map2d-rail-style';
import identities from '../shared/data/map2d-rail-colors.json';
import {SEOUL_METRO_DISPLAY_COLORS} from '../shared/rail-style';

describe('2D route colours with preserved source identity',()=>{
  it('meets unrounded 4.5:1 label contrast for every route and neutral fallback without altering track colours',()=>{
    const parsed=createExpression(map2DRailLabelColorExpression(),'layers[0].paint.text-color');
    expect(parsed.result).toBe('success');
    if(parsed.result!=='success')throw new Error('Invalid label colour expression');
    const rgb=(hex:string)=>[1,3,5].map(offset=>parseInt(hex.slice(offset,offset+2),16));
    const whiteContrast=(hex:string)=>{
      const [r,g,b]=rgb(hex).map(channel=>channel/255).map(s=>s<=.04045?s/12.92:Math.pow((s+.055)/1.055,2.4));
      return 1.05/(.2126*r+.7152*g+.0722*b+.05);
    };
    const properties=[{}, {source_id:'other',stable_id:'03705e0b11c59272'},
      {source_id:'osm',name:'안산선',stable_id:'003f9104ac2055ee'},
      ...Object.keys(identities.records).map(stable_id=>({source_id:'osm',stable_id})),
      ...Object.keys(SEOUL_METRO_DISPLAY_COLORS).map(line=>({name:`서울 지하철 ${line}호선`}))];
    for(const row of properties){
      const track=map2DRailColor(row),label=map2DRailLabelColor(row);
      expect(whiteContrast(label)).toBeGreaterThanOrEqual(4.5);
      expect(parsed.value.evaluate({zoom:15},{type:2,properties:row})).toBe(label);
      if(whiteContrast(track)>=4.5)expect(label).toBe(track);
      else{
        expect(whiteContrast(label)).toBeGreaterThanOrEqual(5);
        rgb(label).forEach((channel,index)=>expect(channel).toBeLessThanOrEqual(rgb(track)[index]));
      }
    }
    expect(map2DRailColor({source_id:'osm',stable_id:'03705e0b11c59272'})).toBe('#FFC500');
    expect(map2DRailLabelColor({source_id:'osm',stable_id:'03705e0b11c59272'})).not.toBe('#FFC500');
    expect(map2DRailLabelColor({source_id:'osm'},'way/847201132')).toBe(map2DRailLabelColor({source_id:'osm',stable_id:'03705e0b11c59272'}));
    expect(map2DRailLabelColor({source_id:'osm',stable_id:'003f9104ac2055ee'})).toBe(map2DRailLabelColor({}));
  });
  it('excludes only source-confirmed service-track labels, including the false Line 9 labels near Jonggak and Seoul station',()=>{
    const parsed=createExpression(map2DRailLabelFilter(),'layers[0].filter');
    expect(parsed.result).toBe('success');
    if(parsed.result!=='success')throw new Error('Invalid label expression');
    const evaluate=(properties:Record<string,unknown>)=>parsed.value.evaluate({zoom:15},{type:2,properties});
    for(const [id,row] of Object.entries(identities.suppressed_labels)){
      expect(['crossover','siding','yard']).toContain(row.service);
      expect(evaluate({source_id:'osm',stable_id:id})).toBe(false);
      expect(evaluate({source_id:'other',stable_id:id})).toBe(true);
    }
    expect(identities.suppressed_labels.cd396574ac2614d0).toMatchObject({source_record_id:'way/379994149',service:'crossover'});
    expect(identities.suppressed_labels.e17a69193a15c3f3).toMatchObject({source_record_id:'way/1116508685',service:'crossover'});
    for(const name of ['경부선','9호선','서울 지하철 1호선'])expect(evaluate({source_id:'osm',stable_id:'not-confirmed',name})).toBe(true);
    for(const properties of [{source_id:'osm'}, {source_id:'osm',stable_id:null}, {source_id:'osm',stable_id:''},
      {}, {stable_id:'cd396574ac2614d0'}, {source_id:null,stable_id:'cd396574ac2614d0'}])expect(evaluate(properties)).toBe(true);
    expect(JSON.stringify(map2DRailLabelFilter())).not.toContain('"in"');
  });
  it('uses operator colours for explicit Seoul line names and evidence-linked bare names',()=>{
    expect(map2DRailColor({name:'서울 지하철 2호선'})).toBe('#2fae35');
    expect(map2DRailColor({name:'2호선',source_id:'osm',stable_id:'000d1badafbffcc0'})).toBe('#2fae35');
    expect(map2DRailColor({name:'2호선',source_id:'osm'},'way/640443237')).toBe('#2fae35');
    for(const [id,row] of Object.entries(identities.records)){
      const expected=row.line==='suin-bundang'?'#FFC500':row.line==='daejeon-1'?'#016934':SEOUL_METRO_DISPLAY_COLORS[row.line];
      expect(map2DRailColor({source_id:'osm',stable_id:id})).toBe(expected);
      expect(map2DRailColor({source_id:'osm'},row.source_record_id)).toBe(expected);
    }
  });
  it('uses the dated city-map green only for the three confirmed operating Daejeon ways',()=>{
    expect(identities.display_colors['daejeon-1']).toMatchObject({web_display_rgb:'#016934',reference_date:'2025-05-09',scope:'existing_operating_line_1_only_no_planned_lines'});
    expect(Object.values(identities.records).filter(row=>row.line==='daejeon-1')).toHaveLength(3);
    for(const [stable,id] of [['9f19790d3f2b7248','way/552391863'],['a87a0a9f1bee25ab','way/897125546'],['f376e3a6286ac5ef','way/1004916827']]){
      expect(map2DRailColor({source_id:'osm',stable_id:stable})).toBe('#016934');
      expect(map2DRailColor({source_id:'osm'},id)).toBe('#016934');
    }
    expect(map2DRailColor({source_id:'osm',name:'대전 도시철도 2호선'})).toBe(MAP2D_RAIL_NEUTRAL);
  });
  it('uses the official web display colour only on exclusive Suin-Bundang member ways',()=>{
    expect(identities.display_colors['suin-bundang']).toMatchObject({web_display_rgb:'#FFC500',vector_device_cmyk:[0,.25,1,0],meaning:'operator_web_map_display_not_universal_rgb_standard'});
    expect(Object.values(identities.records).filter(row=>row.line==='suin-bundang')).toHaveLength(247);
    expect(map2DRailColor({name:'분당선',source_id:'osm',stable_id:'03705e0b11c59272'})).toBe('#FFC500');
    expect(map2DRailColor({name:'분당선',source_id:'osm'},'way/847201132')).toBe('#FFC500');
    // Real shared Line 4/Suin-Bundang way stays neutral; a name is insufficient.
    expect(map2DRailColor({name:'안산선',source_id:'osm',stable_id:'003f9104ac2055ee'})).toBe(MAP2D_RAIL_NEUTRAL);
    for(const name of ['분당선','수인선','수인분당선','대전 도시철도 1호선'])expect(map2DRailColor({name,source_id:'osm'})).toBe(MAP2D_RAIL_NEUTRAL);
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
