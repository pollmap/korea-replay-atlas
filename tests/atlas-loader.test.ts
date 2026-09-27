import {expect,it,vi} from 'vitest';
import {loadAtlasContent,type AtlasContent} from '../src/atlas-loader';
import {type AtlasDescriptor,type fetchPinnedJson} from '../src/atlas-client';
import {observeAtlasSelection,type AtlasSelection} from '../src/atlas-selection';

const release='property-0123456789abcdef',mapRelease='map2d-'+'a'.repeat(20),sha='b'.repeat(64),stamp='2026-09-27T00:00:00Z';
const descriptor:AtlasDescriptor={origin:'https://data.example.test',manifest:{schema_version:1,release_id:'atlas-0123456789abcdef',
  map_catalog:{path:`/data/map-tiles/${mapRelease}/catalog.json`,sha256:sha,release_id:mapRelease},
  property_release:{path:`/data/property/${release}/manifest.json`,sha256:sha,release_id:release}}};
function fixtures(){return {
  map:{schema_version:1,release_id:mapRelease,source_release_id:'source',bounds:[124,33,132,39],sources:[],reference_dates:{},attribution:'test',
    topics:[{id:'roads',source_layer:'roads',minzoom:6,maxzoom:13,feature_count:0,bounds:[124,33,132,39],description:'test',geometry_precision:'test',chunks:[],details:[]}]},
  property:{schema_version:1,kind:'property-release',release_id:release,generated_at:stamp,
    period:{from:'202609',to:'202609',latest_complete_month:'202609'},
    coverage:{expected:0,complete:0,empty:0,failed:0,pending:0,partial:0,historical_coverage:'current_codes_only_pending_effective_date_crosswalk'},
    sources:[{id:'molit-apt-sale-detail',dataset_id:'15126468',label:'sale',page_url:'https://www.data.go.kr/data/15126468/openapi.do',evidence_type:'official_report'},
      {id:'molit-apt-rent',dataset_id:'15126474',label:'rent',page_url:'https://www.data.go.kr/data/15126474/openapi.do',evidence_type:'official_report'}],
    code_registry:{source_url:'https://www.code.go.kr/stdcodesrch/codeAllDownloadL.do',retrieved_at:stamp,sha256:sha,current_region_count:0},
    regions:{url:`/data/property/${release}/regions.json`,sha256:sha,bytes:100},coordinates:{verified_complexes:0,unresolved_complexes:0,name_only_join:false},caveats:[]},
  regions:{schema_version:1,kind:'property-regions',release_id:release,regions:[]},
};}
function deferred(){let resolve!:(value:unknown)=>void,reject!:(error:unknown)=>void;const promise=new Promise<unknown>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
const tick=()=>new Promise<void>(resolve=>setTimeout(resolve,0));
function setup(){
  const documents=fixtures(),pending={map:deferred(),property:deferred(),regions:deferred()};
  const read=vi.fn<typeof fetchPinnedJson>((ref,_origin,signal)=>{
    const key=ref.path===descriptor.manifest.map_catalog.path?'map':ref.path===descriptor.manifest.property_release.path?'property':'regions';
    signal.throwIfAborted();return pending[key].promise;
  });
  const states:AtlasSelection<AtlasContent>[]=[],events=new EventTarget();
  const stop=observeAtlasSelection({load:signal=>loadAtlasContent(descriptor,signal,read),events,readUrl:()=>`https://korea-replay.pages.dev/#propertyRelease=${release}`,replaceUrl:vi.fn(),publish:state=>states.push(state)});
  return {documents,pending,read,states,stop};
}

it('starts regions while the map is still pending, but publishes only the complete validated atlas',async()=>{
  const app=setup();expect(app.read).toHaveBeenCalledTimes(2);
  app.pending.property.resolve(app.documents.property);await tick();
  expect(app.read).toHaveBeenCalledTimes(3);expect(app.read.mock.calls[2][0]).toEqual(app.documents.property.regions);
  expect(app.read.mock.calls.every(call=>call[1]===descriptor.origin&&call[2]===app.read.mock.calls[0][2])).toBe(true);
  app.pending.regions.resolve(app.documents.regions);await tick();expect(app.states.at(-1)?.state).toBe('loading');
  app.pending.map.resolve(app.documents.map);await tick();
  expect(app.states.at(-1)).toEqual({state:'ready',error:'',content:{origin:descriptor.origin,...app.documents}});app.stop();
});

it('does not publish a completed map before regions finish',async()=>{
  const app=setup();app.pending.map.resolve(app.documents.map);app.pending.property.resolve(app.documents.property);await tick();
  expect(app.states.at(-1)?.state).toBe('loading');app.pending.regions.resolve(app.documents.regions);await tick();
  expect(app.states.at(-1)?.state).toBe('ready');app.stop();
});

it.each(['map','property','regions'] as const)('never publishes ready when the %s request fails and its sibling later succeeds',async key=>{
  const app=setup();
  if(key==='regions'){app.pending.property.resolve(app.documents.property);await tick();}
  app.pending[key].reject(new Error(`${key} hash mismatch`));await tick();
  expect(app.states.at(-1)).toEqual({state:'error',content:null,error:`${key} hash mismatch`});
  expect(app.read.mock.calls.every(call=>call[2].aborted)).toBe(true);
  for(const other of ['map','property','regions'] as const)if(other!==key)app.pending[other].resolve(app.documents[other]);
  await tick();expect(app.states.some(state=>state.state==='ready')).toBe(false);
  if(key==='map')expect(app.read).toHaveBeenCalledTimes(2);app.stop();
});

it.each(['map','property','regions'] as const)('retains the %s release identity check',async key=>{
  const app=setup();
  if(key==='map')app.documents.map.release_id='map2d-'+'c'.repeat(20);
  if(key==='property')app.documents.property.release_id='property-cccccccccccccccc';
  if(key==='regions')app.documents.regions.release_id='property-cccccccccccccccc';
  // Keep the substituted property document internally valid; the atlas pin must still reject it.
  if(key==='property')app.documents.property.regions.url='/data/property/property-cccccccccccccccc/regions.json';
  for(const name of ['map','property','regions'] as const)app.pending[name].resolve(app.documents[name]);
  await tick();expect(app.states.at(-1)).toMatchObject({state:'error',content:null});
  expect(app.states.at(-1)?.error).toContain(key==='regions'?'지역 목록':'지도·부동산');
  if(key==='property')expect(app.read).toHaveBeenCalledTimes(2);app.stop();
});

it.each(['map','property','regions'] as const)('retains the %s schema check before completion',async key=>{
  const app=setup();app.documents[key].schema_version=2;
  for(const name of ['map','property','regions'] as const)app.pending[name].resolve(app.documents[name]);
  await tick();expect(app.states.at(-1)).toMatchObject({state:'error',content:null});
  expect(app.states.some(state=>state.state==='ready')).toBe(false);app.stop();
});

it.each([false,true])('does not publish late results after cleanup (regions already requested: %s)',async regionsStarted=>{
  const app=setup();
  if(regionsStarted){app.pending.property.resolve(app.documents.property);await tick();}
  const calls=app.read.mock.calls.length;app.stop();
  expect(app.read.mock.calls.every(call=>call[2].aborted)).toBe(true);
  for(const name of ['map','property','regions'] as const)app.pending[name].resolve(app.documents[name]);
  await tick();expect(app.read).toHaveBeenCalledTimes(calls);
  expect(app.states).toEqual([{state:'loading',content:null,error:''}]);
});
