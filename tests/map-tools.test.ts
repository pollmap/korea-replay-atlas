import {describe,it,expect} from 'vitest';
import {measureMap,measurementGeoJSON,mergeBookmarks,validateBookmarks} from '../shared/map-tools';
describe('map measurement and portable saved places',()=>{
  it('measures independently known spherical distances and preserves input',()=>{
    expect(measureMap('distance',[[0,0],[1,0]]).distance_m).toBeCloseTo(111195.08,1);
    expect(measureMap('distance',[[179.9,0],[-179.9,0]]).distance_m).toBeCloseTo(22239.02,1);
    expect(measureMap('area',[[0,0],[1,0],[1,1],[0,1]]).area_m2/1e6).toBeCloseTo(12363.718,2);
    expect(measurementGeoJSON(measureMap('area',[[127,37],[127.1,37],[127,37.1]])).features[0].geometry.type).toBe('Polygon');
    expect(()=>measureMap('area',[[NaN,37]])).toThrow();
    expect(()=>measureMap('distance',Array.from({length:513},()=>[127,37]))).toThrow();
  });
  const row={id:'a',name:'저장한 장소',position:{id:'place',name:'서울',region:'서울',lon:127,lat:37,range:1500},created_at:'2026-09-20T00:00:00Z'};
  const file={schema_version:1,kind:'korea-replay-bookmarks',bookmarks:[row]};
  it('validates the complete import before merging and preserves existing entries',()=>{
    expect(validateBookmarks(file).bookmarks).toEqual([row]);
    expect(mergeBookmarks([row],[{...row,name:'덮어쓰기'}])).toEqual([row]);
    for(const bad of [{...file,schema_version:2},{...file,bookmarks:[row,row]},{...file,bookmarks:[{...row,position:{...row.position,range:Infinity}}]},{...file,bookmarks:[{...row,position:{...row.position,lon:'127'}}]}])expect(()=>validateBookmarks(bad)).toThrow();
  });
});
