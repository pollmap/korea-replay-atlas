import {describe,it,expect,vi} from 'vitest';
import {handleRequest,type Env} from '../worker/index';
import {EMPTY_CATALOG} from '../shared/sources';
import type {Asset,Catalog,CatalogV2,LayerId} from '../shared/contracts';
import {buildSearchFiles} from '../scripts/build-search-v2.mjs';
import {searchPlaces,type SearchEntry} from '../shared/search';

const RELEASE='pub-0123456789abcdef';
const VERSION='9d3f5082-7819-4efc-92e8-e3f5af8c2321';
const asset=(id:string,changes:Partial<Asset>={}):Asset=>({id,layer:'infrastructure',format:'geojson',url:`/data/${id}.geojson`,bbox:[126.9,37.4,127.2,37.7],source_id:'fixture',version:'test',count:1,sha256:'fixture',...changes});
const detailed=[
  asset('seoul-a',{count:111}),asset('seoul-b',{count:222}),asset('daejeon',{bbox:[127.3,36.2,127.5,36.5],count:333}),
  asset('buildings',{layer:'buildings',format:'3d-tiles',url:'/data/buildings/tileset.json',count:500}),
  asset('rail-current',{layer:'rail',format:'replay',from:'2026-09-16T00:00:00Z',to:'2026-09-16T01:00:00Z'}),
  asset('rail-old',{layer:'rail',format:'replay',from:'2026-09-15T00:00:00Z',to:'2026-09-15T01:00:00Z'}),
];
const legacy:Catalog={...EMPTY_CATALOG,release_id:RELEASE,assets:detailed};
const compact:CatalogV2={...legacy,schema_version:2,assets:detailed.filter(item=>item.format!=='geojson'),
  indexes:[asset('regional',{format:'asset-index',bbox:[126.9,36.2,127.5,37.7],url:'/data/indexes/region.json',count:666})],
  legacy_url:`/data/releases/${RELEASE}.v1.json`,coverage_url:'/data/indexes/coverage-fixture.json'};
const compactCoverage={schema_version:1,assets:detailed.map(({layer,format,bbox,from,to})=>({layer,format,bbox,...(from?{from,to}:{})}))};
type Coverage={release_id:string;layers:{id:LayerId;matching_assets:number;in_view:boolean}[]};

function fixture(overrides:Partial<Env>={}){
  const seen:string[]=[],bodies=new Map<string,unknown>([
    ['/data/catalog.json',compact],[`/data/releases/${RELEASE}.json`,compact],
    [compact.coverage_url!,compactCoverage],[compact.legacy_url,legacy],
  ]);
  const storage={fetch:vi.fn(async(request:Request)=>{
    const pathname=new URL(request.url).pathname;seen.push(pathname);
    return bodies.has(pathname)?Response.json(bodies.get(pathname)):new Response(null,{status:404});
  })};
  const env={ASSETS:storage,DATA_STORAGE:'static',STATIC_RELEASE_ID:RELEASE,CF_VERSION_METADATA:{id:VERSION},...overrides} as unknown as Env;
  const call=(path:string,origin='https://korea-replay.example.workers.dev')=>handleRequest(new Request(origin+path),env);
  return {env,storage,seen,bodies,call};
}

describe('CatalogV2 static API compatibility',()=>{
  it('counts the exact published assets from compact coverage without opening regional files',async()=>{
    const f=fixture();const response=await f.call('/api/v1/coverage?bbox=126.9,37.4,127.2,37.7');
    expect(response.status).toBe(200);const body=await response.json() as Coverage;
    expect(body.release_id).toBe(RELEASE);
    expect(body.layers.find(layer=>layer.id==='infrastructure')).toMatchObject({matching_assets:2,in_view:true});
    expect(body.layers.find(layer=>layer.id==='buildings')).toMatchObject({matching_assets:1,in_view:true});
    expect(body.layers.find(layer=>layer.id==='rail')).toMatchObject({matching_assets:2,in_view:true});
    expect(f.seen).toEqual(['/data/catalog.json',compact.coverage_url]);
    expect(f.seen).not.toContain('/data/indexes/region.json');
  });
  it('retains spatial and time filtering from the v1 contract',async()=>{
    const v2=fixture(),v1=fixture({DATA_STORAGE:undefined,STATIC_RELEASE_ID:undefined});v1.bodies.set('/data/catalog.json',legacy);
    for(const params of ['', '?bbox=127.3,36.2,127.5,36.5','?bbox=130.9,37.2,131.2,37.5','?bbox=126.9,37.4,127.2,37.7&from=2026-09-16T09%3A00%3A00%2B09%3A00&to=2026-09-16T10%3A00%3A00%2B09%3A00']){
      const [a,b]=await Promise.all([v2.call('/api/v1/coverage'+params),v1.call('/api/v1/coverage'+params)]);
      expect(await a.json()).toEqual(await b.json());
    }
    const replay=await v2.call('/api/v1/replay?from=2026-09-16T00:00:00Z&to=2026-09-16T01:00:00Z');
    expect((await replay.json() as {assets:Asset[]}).assets.map(item=>item.id)).toEqual(['rail-current']);
  });
  it('streams a prebuilt v1 catalog without invoking its JSON decoder',async()=>{
    const f=fixture(),raw=JSON.stringify(legacy),jsonDecoder=vi.fn(()=>{throw new Error('v1 must not be parsed in the edge worker');});
    f.storage.fetch.mockImplementation(async(request:Request)=>{
      f.seen.push(new URL(request.url).pathname);
      const response=new Response(raw,{headers:{'Content-Type':'application/json','ETag':'"fixture"'}});
      response.json=jsonDecoder;return response;
    });
    const latest=await f.call('/api/v1/catalog');
    expect(latest.status).toBe(200);expect(latest.headers.get('cache-control')).toBe('no-cache');
    expect(latest.headers.get('etag')).toBe('"fixture"');expect(await latest.text()).toBe(raw);
    const pinned=await f.call('/api/v1/catalog?release='+RELEASE);
    expect(await pinned.text()).toBe(raw);expect(pinned.headers.get('cache-control')).toContain('immutable');
    expect(jsonDecoder).not.toHaveBeenCalled();
    expect(f.seen).toEqual([compact.legacy_url,compact.legacy_url]);
  });
  it('returns missing pinned releases as 404 without falling back to latest',async()=>{
    const f=fixture();
    for(const endpoint of ['catalog','coverage','replay','search?q=청주&']){
      const separator=endpoint.includes('?')?'':'?';
      const response=await f.call(`/api/v1/${endpoint}${separator}release=pub-aaaaaaaaaaaaaaaa`);
      expect(response.status,endpoint).toBe(404);
      expect((await response.json() as {error:string}).error).toContain('공유한');
    }
    expect(f.seen).not.toContain('/data/catalog.json');
  });
  it('keeps the local v1 catalog flat after its public pointer upgrades to v2',async()=>{
    const f=fixture({DATA_STORAGE:undefined,STATIC_RELEASE_ID:undefined});
    for(const query of ['', '?release='+RELEASE]){
      const response=await f.call('/api/v1/catalog'+query);
      expect(response.status).toBe(200);expect(await response.json()).toEqual(legacy);
    }
    expect(f.seen).not.toContain('/data/indexes/region.json');
    f.bodies.delete(compact.legacy_url);
    expect((await f.call('/api/v1/catalog')).status).toBe(404);
  });
  it('rejects a local compatibility pointer to a different release or external host',async()=>{
    for(const legacy_url of ['https://example.org/catalog.json','/data/releases/pub-aaaaaaaaaaaaaaaa.v1.json']){
      const f=fixture({DATA_STORAGE:undefined,STATIC_RELEASE_ID:undefined});
      f.bodies.set('/data/catalog.json',{...compact,legacy_url});
      expect((await f.call('/api/v1/catalog')).status).toBe(503);
      expect(f.seen).toEqual(['/data/catalog.json']);
    }
  });
  it('validates a pinned release even when search is empty',async()=>{
    const f=fixture();
    for(const query of ['', '&q=', '&q=%20%20']){
      expect((await f.call('/api/v1/search?release=pub-aaaaaaaaaaaaaaaa'+query)).status).toBe(404);
    }
    for(const release of ['', 'latest', '../catalog']){
      expect((await f.call('/api/v1/search?release='+encodeURIComponent(release))).status).toBe(400);
    }
    expect(f.seen).not.toContain('/data/catalog.json');
    const pinned=await f.call('/api/v1/search?release='+RELEASE);
    expect(pinned.status).toBe(200);
    expect(await pinned.json()).toEqual(await (await f.call('/api/v1/search')).json());
  });
  it('rejects remote or missing coverage documents rather than returning zero coverage',async()=>{
    const remote=fixture();remote.bodies.set('/data/catalog.json',{...compact,coverage_url:'https://example.org/coverage.json'});
    expect((await remote.call('/api/v1/coverage')).status).toBe(503);expect(remote.seen).toHaveLength(1);
    const missing=fixture();missing.bodies.delete(compact.coverage_url!);
    expect((await missing.call('/api/v1/coverage')).status).toBe(503);
  });
});

describe('immutable deployment runtime discovery',()=>{
  it('uses Cloudflare version metadata and the app workers.dev hostname only',async()=>{
    const f=fixture();
    for(const origin of ['https://korea-replay.example.workers.dev','https://01234567-korea-replay.example.workers.dev']){
      const response=await f.call('/api/v1/runtime',origin);
      expect(await response.json()).toEqual({version_id:VERSION,release_id:RELEASE,preview_origin:'https://9d3f5082-korea-replay.example.workers.dev'});
    }
    expect(f.seen).toHaveLength(0);
  });
  it('does not manufacture a preview address for invalid UUIDs or foreign hostnames',async()=>{
    for(const origin of ['http://127.0.0.1:5173','https://evil.example','https://korea-replay.example.workers.dev.evil.example','https://evil-korea-replay.example.workers.dev','https://korea-replay.other.app.workers.dev']){
      const f=fixture();expect((await (await f.call('/api/v1/runtime',origin)).json() as {preview_origin:unknown}).preview_origin).toBeNull();
    }
    for(const id of ['latest','9d3f5082/evil','9d3f5082-7819-4efc-92e8-e3f5af8c2321<script>','']){
      const f=fixture({CF_VERSION_METADATA:{id}});expect(await (await f.call('/api/v1/runtime')).json()).toEqual({version_id:null,release_id:RELEASE,preview_origin:null});
    }
  });
});

describe('sharded search through the public Worker API',()=>{
  it('reads a real generated v2 manifest and shards under the selected catalog',async()=>{
    const entries:SearchEntry[]=[
      {id:'airport',name:'청주국제공항',region:'공항',alt_names:['Cheongju Airport'],lon:127.49,lat:36.72,range:9000},
      {id:'station',name:'청주역',region:'철도역',alt_names:['Cheongju Station'],lon:127.39,lat:36.65,range:5000},
    ];
    const built=buildSearchFiles(entries,{candidate_count:15,source_sha256:'c'.repeat(64)}),f=fixture();
    const search=asset('search',{...built.descriptor,format:'search-index'});
    f.bodies.set(`/data/releases/${RELEASE}.json`,{...compact,assets:[...compact.assets,search]});
    f.storage.fetch.mockImplementation(async(request:Request)=>{
      const pathname=new URL(request.url).pathname;f.seen.push(pathname);
      if(built.files.has(pathname))return new Response(new Uint8Array(built.files.get(pathname)!));
      return f.bodies.has(pathname)?Response.json(f.bodies.get(pathname)):new Response(null,{status:404});
    });
    const response=await f.call(`/api/v1/search?q=Cheongju&release=${RELEASE}`);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({places:searchPlaces(entries,'Cheongju'),coverage:'published-index',indexed_count:2,candidate_count:15,omitted_count:13,index_limit:50000});
    expect(f.seen[0]).toBe(`/data/releases/${RELEASE}.json`);expect(f.seen).toContain(built.descriptor.url);
    expect(f.seen.some(url=>url.includes('/bucket-'))).toBe(true);expect(f.seen.some(url=>url.includes('/page-'))).toBe(true);
    const count=f.seen.length;await f.call(`/api/v1/search?q=Cheongju&release=${RELEASE}`);expect(f.seen).toHaveLength(count);
  });
});
