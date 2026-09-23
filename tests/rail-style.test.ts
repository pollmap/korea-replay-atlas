import {describe,it,expect} from 'vitest';
import {railDisplayColor,SEOUL_METRO_DISPLAY_COLORS} from '../shared/rail-style';
import {compileGeometry,renderFeatureProperties} from '../shared/geometry';

const depth={source_id:'seoul-depth',layer:'depth'} as const;
const rail={source_id:'osm',layer:'rail'} as const;
describe('source-scoped subway colours',()=>{
  it('gives every supported depth line and its stations the same distinct operator colour',()=>{
    expect(new Set(Object.values(SEOUL_METRO_DISPLAY_COLORS)).size).toBe(8);
    for(const [line,color] of Object.entries(SEOUL_METRO_DISPLAY_COLORS)){
      expect(railDisplayColor({line,source_station_ids:[`${line}:123`,`${line}:124`]},depth)).toBe(color);
      expect(railDisplayColor({provenance:{source_record_id:`${line}:123`}},depth,`${line}:123`)).toBe(color);
      expect(railDisplayColor({},depth,`${line}:123`)).toBe(color);
    }
    expect(railDisplayColor({line:'2'},depth)).toBe('#2fae35');
    expect(railDisplayColor({line:'3'},depth)).toBe('#ff6000');
  });
  it('does not assign Seoul colours to ambiguous names, other cities or unknown sources',()=>{
    for(const name of ['1호선','부산 도시철도 1호선','대구 도시철도 1호선','서울 지하철 11호선']){
      expect(railDisplayColor({name,line:'1'},rail)).toBe('#a79571');
    }
    expect(railDisplayColor({line:'1'},{source_id:'busan',layer:'depth'})).toBe('#64748b');
    expect(railDisplayColor({},depth)).toBe('#64748b');
  });
  it('recognizes only explicit network names on national track geometry',()=>{
    expect(railDisplayColor({name:'서울 지하철 2호선'},rail)).toBe('#2fae35');
    expect(railDisplayColor({name:'수도권 전철 4호선'},rail)).toBe('#1a97dd');
    expect(railDisplayColor({name:'서울 지하철 2호선 연결선'},rail)).toBe('#a79571');
  });
  it('rejects contradictory line identifiers and malformed station records',()=>{
    for(const properties of [{line:'3',source_station_ids:['2:200','2:201']},{line:'5',source_station_ids:['5:1','bad']},{line:'9'},{line:'1',provenance:{source_record_id:'2:200'}}]){
      expect(railDisplayColor(properties,depth)).toBe('#64748b');
    }
    expect(railDisplayColor({line:'3'},depth,'2:200')).toBe('#64748b');
  });
  it('keeps network identification after the renderer restores dictionary-compressed metadata',()=>{
    const data=compileGeometry({type:'FeatureCollection',metadata:{schema_version:1,shared:{kind:'rail'},rows:[{name:'수도권 전철 4호선'}]},features:[{
      type:'Feature',id:'way/123',properties:{metadata_index:0},geometry:{type:'LineString',coordinates:[[127,37],[127.1,37.1]]},
    }]},false,{preserveMetadata:true});
    expect(railDisplayColor(renderFeatureProperties(data.features[0],data.metadata),rail)).toBe('#1a97dd');
  });
});
