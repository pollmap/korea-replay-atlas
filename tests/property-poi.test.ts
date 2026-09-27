import {describe,expect,it} from 'vitest';
import {nearbyPoiChunks,parsePoiChunk,poiDistance,poiSourceStates,validPoiCenter,type PoiChunk,type PoiManifest} from '../shared/property-poi';
import {DEFAULT_SURROUNDINGS_FILTER,surroundingsView} from '../shared/property-surroundings';
const center={longitude:127,latitude:37.5};
const source={id:'osm-fixture',label:'OSM fixture',url:'https://www.openstreetmap.org/copyright',asOf:'2026-09-15',sha256:'a'.repeat(64),license:'ODbL-1.0'};
const chunk:PoiChunk={key:'sample',west:126.99,east:127.01,south:37.49,north:37.51,file:`poi-${'b'.repeat(64)}.json`,sha256:'b'.repeat(64),bytes:1000,count:1};
const record={id:'node/1',name:'역',category:'transport',type:'subway',longitude:127.001,latitude:37.5,positionMethod:'original_node'};
const manifest:PoiManifest={schema:1,source,scope:{boundaryDate:'2025-06-30',regionCodes:['11']},chunks:[chunk]};
describe('source-backed surrounding facilities',()=>{
  it('loads only neighboring cells and enforces total query bytes and slot input limits',()=>{
    expect(nearbyPoiChunks(manifest,center)).toEqual([chunk]);
    expect(nearbyPoiChunks({...manifest,chunks:[{...chunk,west:128,east:128.1}]},center)).toEqual([]);
    expect(()=>nearbyPoiChunks({...manifest,chunks:Array.from({length:65},()=>chunk)},center)).toThrow('한도');
    expect(()=>nearbyPoiChunks({...manifest,chunks:[{...chunk,bytes:4*1024*1024+1}]},center)).toThrow('한도');
    expect(()=>nearbyPoiChunks(manifest,{longitude:NaN,latitude:37})).toThrow('위치');
    expect(validPoiCenter({longitude:0,latitude:0})).toBe(false);
  });
  it('calculates straight-line distances and preserves original geometry semantics and source links',()=>{
    expect(poiDistance(center,center)).toBe(0);
    expect(poiDistance(center,{longitude:127,latitude:37.501})).toBeCloseTo(111.195,2);
    const rows=parsePoiChunk({schema:1,records:[record]},chunk,center,source);
    expect(rows[0]).toMatchObject({id:'node/1',position:{longitude:127.001,latitude:37.5,method:'original_node'},source:{url:'https://www.openstreetmap.org/node/1'}});
    expect(rows[0].distanceMeters).toBeGreaterThan(80);
    expect(rows[0].distanceMeters).toBeLessThan(90);
  });
  it.each([
    {...record,id:'javascript:alert(1)'}, {...record,longitude:127.1}, {...record,latitude:NaN},
    {...record,category:'school'}, {...record,type:'invented'}, {...record,positionMethod:'guessed'},
    {...record,name:''}, {...record,address:123},
  ])('rejects corrupted or unsupported source records',row=>{
    expect(()=>parsePoiChunk({schema:1,records:[row]},chunk,center,source)).toThrow();
  });
  it('does not classify a school from its name or call incomplete OSM coverage zero',()=>{
    const school={...record,id:'way/12',name:'초등학교처럼 보이는 이름',category:'school',type:'school',positionMethod:'area_representative_point'};
    const rows=parsePoiChunk({schema:1,records:[school]},chunk,center,source);
    expect(rows[0].type).toBe('school');
    const scope={complexId:'apt1',releaseId:'release1'},states=poiSourceStates(rows,scope,source);
    expect(surroundingsView(states.transport,scope,DEFAULT_SURROUNDINGS_FILTER)).toMatchObject({state:'partial',count:null,records:[]});
    expect(surroundingsView(states.school,scope,{...DEFAULT_SURROUNDINGS_FILTER,category:'school'})).toMatchObject({state:'partial',count:null,records:[expect.objectContaining({id:'way/12'})]});
  });
  it('rejects missing and duplicate records rather than silently deduplicating unrelated features',()=>{
    expect(()=>parsePoiChunk({schema:1,records:[]},chunk,center,source)).toThrow();
    expect(()=>parsePoiChunk({schema:1,records:[record,record]},{...chunk,count:2},center,source)).toThrow();
    const rows=parsePoiChunk({schema:1,records:[record]},chunk,center,source);
    expect(()=>poiSourceStates([...rows,...rows],{complexId:'a',releaseId:'b'},source)).toThrow('중복');
  });
});
