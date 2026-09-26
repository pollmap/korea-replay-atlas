import {createHash} from 'node:crypto';
import {gzipSync} from 'node:zlib';
import {describe,it,expect,vi} from 'vitest';
import {Compression} from 'pmtiles';
import {findDetailChunk,findTileChunk,validateMapCatalog2D,type MapCatalog2D,type MapTileFile} from '../shared/map-tiles';
import {VerifiedArchiveSource,VerifiedMapTileStore,createMapTilesProtocol,decompressMapTile} from '../src/map-tiles-protocol';

const origin='https://data.example.test',release='map2d-'+'a'.repeat(20),prefix=`/data/map-tiles/${release}`;
const sha=(bytes:Uint8Array)=>createHash('sha256').update(bytes).digest('hex');
function ref(bytes:Uint8Array,name='one.pmtiles'):MapTileFile {return {url:`${prefix}/roads/${name}`,sha256:sha(bytes),byte_length:bytes.byteLength};}
const tick=async()=>{await new Promise(resolve=>setTimeout(resolve,0));};
function catalog():MapCatalog2D {return {schema_version:1,release_id:release,source_release_id:'source',bounds:[124.5,33,132,38.7],sources:[],reference_dates:{sgis:'2025-06-30'},attribution:'original',topics:[{id:'roads',source_layer:'roads',minzoom:6,maxzoom:13,feature_count:1,bounds:[126,36,128,38],description:'test',geometry_precision:'MVT display',chunks:[{...ref(new Uint8Array([1])),first_tile_id:10,last_tile_id:12,tile_count:2}],details:[{...ref(new Uint8Array([2]),'one.json.gz'),first_id:'1'.repeat(64),last_id:'1'.repeat(64),record_count:1}]}]};}

describe('immutable 2D catalog contract',()=>{
  it('validates bounded ordered TileID and source-selection ranges',()=>{
    const c=validateMapCatalog2D(catalog()),t=c.topics[0];expect(findTileChunk(t,11)).toBe(t.chunks[0]);expect(findTileChunk(t,13)).toBeUndefined();expect(findDetailChunk(t,'1'.repeat(64))).toBe(t.details[0]);
    const overlap=catalog();overlap.topics[0].chunks.push({...overlap.topics[0].chunks[0],url:`${prefix}/roads/two.pmtiles`});expect(()=>validateMapCatalog2D(overlap)).toThrow('ranges');
    const missing=catalog();missing.topics[0].feature_count=2;expect(()=>validateMapCatalog2D(missing)).toThrow('every source');
  });
  it('rejects external/escaped/release-swapped paths and oversized files',()=>{
    for(const url of ['https://evil.test/a.pmtiles',`${prefix}/../a.pmtiles`,'/data/map-tiles/map2d-'+'b'.repeat(20)+'/x.pmtiles']){const c=catalog();c.topics[0].chunks[0].url=url;expect(()=>validateMapCatalog2D(c)).toThrow('path');}
    const c=catalog();c.topics[0].chunks[0].byte_length=1024*1024+1;expect(()=>validateMapCatalog2D(c)).toThrow('size');
  });
});

describe('verified whole-archive shared LRU',()=>{
  it('uses GET without Range, verifies complete body before exposing slices, and source holds no body',async()=>{
    const bytes=new Uint8Array([1,2,3,4]),file=ref(bytes),fetcher=vi.fn<typeof fetch>(async()=>new Response(bytes));const store=new VerifiedMapTileStore(origin,fetcher),source=new VerifiedArchiveSource(file,store);
    expect(new Uint8Array((await source.getBytes(1,2)).data)).toEqual(new Uint8Array([2,3]));await source.getBytes(0,16384);
    expect(fetcher).toHaveBeenCalledTimes(1);expect(fetcher.mock.calls[0][1]).toMatchObject({redirect:'error',credentials:'omit'});expect(fetcher.mock.calls[0][1]).not.toHaveProperty('headers');
    expect(Object.values(source).some(value=>value instanceof ArrayBuffer)).toBe(false);expect(store.snapshot().cachedBytes).toBe(4);store.dispose();expect(store.snapshot().cachedBytes).toBe(0);
  });
  it('rejects hash/length/status mismatch and does not cache unverified bytes',async()=>{
    const bytes=new Uint8Array([1,2,3]);
    for(const response of [new Response(new Uint8Array([1,2,4])),new Response(new Uint8Array([1,2])),new Response(new Uint8Array([1,2,3,4])),new Response(null,{status:206})]){
      const store=new VerifiedMapTileStore(origin,vi.fn(async()=>response));await expect(store.get(ref(bytes))).rejects.toThrow();expect(store.snapshot().cachedBytes).toBe(0);store.dispose();
    }
  });
  it('one cancelled subscriber cannot abort a shared archive needed by another',async()=>{
    const bytes=new Uint8Array([1,2,3]);let finish!:(r:Response)=>void;
    const fetcher=vi.fn(()=>new Promise<Response>(resolve=>{finish=resolve;}));const store=new VerifiedMapTileStore(origin,fetcher),a=new AbortController(),b=new AbortController();
    const first=store.get(ref(bytes),a.signal),second=store.get(ref(bytes),b.signal);a.abort();await expect(first).rejects.toMatchObject({name:'AbortError'});finish(new Response(bytes));expect(new Uint8Array(await second)).toEqual(bytes);expect(fetcher).toHaveBeenCalledTimes(1);store.dispose();
  });
  it('enforces four body-lifetime slots and cancels queued/active work on dispose',async()=>{
    const starts:Array<(r:Response)=>void>=[];const bytes=new Uint8Array([1]);const fetcher=vi.fn(()=>new Promise<Response>(resolve=>starts.push(resolve)));const store=new VerifiedMapTileStore(origin,fetcher);
    const promises=Array.from({length:8},(_,i)=>store.get(ref(bytes,`${i}.pmtiles`)).catch(e=>e));expect(starts).toHaveLength(4);expect(store.snapshot().peakActive).toBe(4);
    starts[0](new Response(bytes));await vi.waitFor(()=>expect(starts).toHaveLength(5),{timeout:2000,interval:10});store.dispose();const settled=await Promise.all(promises);expect(settled.filter(v=>v instanceof DOMException).length).toBe(7);
    for(const end of starts.slice(1))end(new Response(bytes));await tick();expect(store.snapshot().cachedBytes).toBe(0);
  });
  it('evicts archive bodies at the shared byte budget and reloads through a key-only source',async()=>{
    const first=new Uint8Array(700000).fill(1),second=new Uint8Array(700000).fill(2);const a=ref(first,'a.pmtiles'),b=ref(second,'b.pmtiles');
    const fetcher=vi.fn(async(url:RequestInfo|URL)=>new Response(String(url).endsWith('a.pmtiles')?first:second));const store=new VerifiedMapTileStore(origin,fetcher,1024*1024),source=new VerifiedArchiveSource(a,store);
    await source.getBytes(0,127);await store.get(b);expect(store.snapshot().cachedBytes).toBe(700000);await source.getBytes(0,127);expect(fetcher).toHaveBeenCalledTimes(3);expect(store.snapshot().cachedBytes).toBeLessThanOrEqual(1024*1024);store.dispose();
  });
});

describe('bounded selection and lifetime',()=>{
  it('shares original building details with bounded overview partitions and rejects wrong membership',async()=>{
    const id='1'.repeat(64),row={stable_id:id,source_record_id:'building/1',source_id:'overture',version:'v',properties:{name:'원본 건물'},geometry_sha256:'a'.repeat(64),source_asset_id:'source'};
    const bytes=gzipSync(JSON.stringify({schema_version:1,records:[row]})),c=catalog();
    const original={...c.topics[0],id:'buildings',source_layer:'buildings',minzoom:14,maxzoom:14,display_id_hex_length:16 as const,
      details:[{...ref(bytes,'buildings.json.gz'),first_id:id,last_id:id,record_count:1}]};
    c.topics=[original,{...original,id:'buildings-overview-1',source_layer:'buildings-overview-1',minzoom:12,maxzoom:13,details:[],chunks:[],detail_topic_id:'buildings'}];
    const fetcher=vi.fn(async()=>new Response(bytes)),api=createMapTilesProtocol({catalog:c,origin,allowedOrigins:[origin],fetcher});
    expect((await api.pick('buildings-overview-1',id.slice(0,16)))?.sourceId).toBe('building/1');
    expect((await api.pick('buildings',id))?.sourceId).toBe('building/1');
    expect(await api.pick('buildings-overview-1','2'.repeat(16))).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);api.dispose();
    c.topics[1].feature_count=0;expect(()=>validateMapCatalog2D(c)).toThrow('partition coverage');
    c.topics[1].feature_count=1;c.topics[1].detail_topic_id='bad' as 'buildings';expect(()=>validateMapCatalog2D(c)).toThrow('detail alias');
  });
  it('resolves collision-audited compact display keys across full-hash shard boundaries',async()=>{
    const full='1234567890abcdef'+'f'.repeat(48),row={stable_id:full,source_record_id:'원본/123',source_id:'osm',version:'v',properties:{name:'원래 도로'},geometry_sha256:'a'.repeat(64),source_asset_id:'source'},bytes=gzipSync(JSON.stringify({schema_version:1,records:[row]}));
    const c=catalog();c.topics[0].display_id_hex_length=16;c.topics[0].details=[{...ref(bytes,'compact.json.gz'),first_id:full,last_id:full,record_count:1}];
    expect(findDetailChunk(c.topics[0],full.slice(0,16))).toBe(c.topics[0].details[0]);expect(findDetailChunk(c.topics[0],full)).toBe(c.topics[0].details[0]);
    const api=createMapTilesProtocol({catalog:c,origin,allowedOrigins:[origin],fetcher:vi.fn(async()=>new Response(bytes))});expect((await api.pick('roads',full.slice(0,16)))?.sourceId).toBe('원본/123');api.dispose();
    delete c.topics[0].display_id_hex_length;expect(findDetailChunk(c.topics[0],full.slice(0,16))).toBeUndefined();
  });
  it('restores complete quality/provenance attributes without confusing IDs or caching decoded records',async()=>{
    const id='1'.repeat(64),properties={name:'높이 검토 건물',render_eligible:false,raw_height:.01,render_height:null,quality_flags:['height_conflict'],nested:{a:[null,2]}},bytes=gzipSync(JSON.stringify({schema_version:1,records:[{stable_id:id,source_record_id:'원본/0001',source_id:'overture',version:'original',properties,geometry_sha256:'f'.repeat(64),source_asset_id:'source'}]}));
    const c=catalog();c.topics[0].details=[{...ref(bytes,'selection.json.gz'),first_id:id,last_id:id,record_count:1}];const api=createMapTilesProtocol({catalog:c,origin,allowedOrigins:[origin],fetcher:vi.fn(async()=>new Response(bytes))});
    const selected=await api.pick('roads',id);expect(selected?.properties).toEqual(properties);expect(selected?.sourceId).toBe('원본/0001');expect(selected?.height).toBeUndefined();expect(await api.pick('roads','2'.repeat(64))).toBeNull();api.dispose();await expect(api.pick('roads',id)).rejects.toMatchObject({name:'AbortError'});
  });
  it('caps decompressed bodies and validates URL/zoom before any fetch',async()=>{
    await expect(decompressMapTile(new Uint8Array(gzipSync(new Uint8Array(100000))).buffer,Compression.Gzip,100)).rejects.toThrow('budget');
    const fetcher=vi.fn();const api=createMapTilesProtocol({catalog:catalog(),origin,allowedOrigins:[origin],fetcher});for(const url of ['krtile://wrong/roads/6/1/1',`krtile://${release}/roads/30/1/1`,`krtile://${release}/roads/6/999/1`])await expect(api.protocol({url},new AbortController())).rejects.toThrow();expect(fetcher).not.toHaveBeenCalled();api.dispose();
  });
  it('reads a real PMTiles archive from the independent Python writer through the verified custom Source',async()=>{
    const bytes=new Uint8Array(Buffer.from('UE1UaWxlcwN/AAAAAAAAAB0AAAAAAAAAnAAAAAAAAABaAAAAAAAAAPYAAAAAAAAAAAAAAAAAAAD2AAAAAAAAAMMAAAAAAAAAAQAAAAAAAAABAAAAAAAAAAEAAAAAAAAAAQICAQwMABMaSwAqdRUAQEtMAFemFgyAqbJLgMANFh+LCAAAAAAAAv9jnLmni5PxMCMjAIeRGtEJAAAAH4sIAAAAAAAC/6tWKktNLskvis9JrEwtKlayUoiuVspMAdJKRfmJKcVKOgpKaZmpOSkgqWql4pLEpJzUeIiC4JKizLx0kIq8xNxUJJHa2thaALN6woxZAAAAH4sIAAAAAAAC/w3KOw6CMAAA0IKfBIySMJm6mE6OtFA+G/Eipj8TEgUFTIxLHbiEm+5usslZvIAcQt/84NOwR2XBZOUu3DEAhmGa8wFyLPTRELx1pwHotAOHOdsraFU14zu1ySSclepQqkrlNauzIkeOPe1vTX9vlt9X2z9atLbTmOAk8UNJCfVJKCJfSiw8hcnW4yH1g4jzkFAVYBFEgsWcMCYkDSj+Z4wpQRPbOp5YXmcXJVfX9Gz+AAiWLzGvAAAA','base64'));
    const c=catalog();c.topics[0].chunks=[{...ref(bytes),first_tile_id:19045913,last_tile_id:19045913,tile_count:1}];
    const fetcher=vi.fn(async()=>new Response(bytes));const api=createMapTilesProtocol({catalog:c,origin,allowedOrigins:[origin],fetcher});
    const tile=await api.protocol({url:`krtile://${release}/roads/12/3493/1583`},new AbortController());expect(tile.data.byteLength).toBe(175);
    const expected=new Uint8Array(tile.data.slice(0));structuredClone(tile.data,{transfer:[tile.data]});
    const revisit=await api.protocol({url:`krtile://${release}/roads/12/3493/1583`},new AbortController());
    expect(new Uint8Array(revisit.data)).toEqual(expected);expect(api.snapshot().decodedTileHits).toBe(1);
    expect(api.snapshot().limitBytes+api.snapshot().decodedTileLimitBytes).toBe(64*1024*1024);
    const cancelled=new AbortController();cancelled.abort();await expect(api.protocol({url:`krtile://${release}/roads/12/3493/1583`},cancelled)).rejects.toMatchObject({name:'AbortError'});expect(fetcher).toHaveBeenCalledTimes(1);expect(api.snapshot().directoryEntries).toBeGreaterThan(0);api.dispose();expect(api.snapshot().directoryEntries).toBe(0);
  });
});
