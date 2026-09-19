import {describe,expect,it,vi} from 'vitest';
import {createHash} from 'node:crypto';
import {mkdir,mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {loadVerificationInput,verifyRelease} from '../scripts/verify-remote-release.mjs';
import {buildSearchFiles} from '../scripts/build-search-v2.mjs';
import {handleRequest} from '../worker/index';
import {EMPTY_CATALOG} from '../shared/sources';
import {searchPlaces} from '../shared/search';

const release='pub-0123456789abcdef',version='9d3f5082-7819-4efc-92e8-e3f5af8c2321';
const origin='https://korea-replay.fixture.workers.dev',preview='https://9d3f5082-korea-replay.fixture.workers.dev';
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const encode=value=>new TextEncoder().encode(typeof value==='string'?value:JSON.stringify(value));
const source=[{id:'airport',name:'청주국제공항',region:'공항',alt_names:['Cheongju Airport'],lon:127.49,lat:36.72,range:9000}];
const built=buildSearchFiles(source,{candidate_count:15,source_sha256:'c'.repeat(64)});
const asset=(id,changes={})=>({id,layer:'infrastructure',format:'geojson',url:`/data/${id}.geojson`,bbox:[126.9,37.4,127.2,37.7],source_id:'fixture',version:'test',count:1,sha256:'fixture',...changes});
const detailed=[asset('road'),asset('search',{...built.descriptor,format:'search-index'}),asset('weather',{layer:'radar',format:'imagery',url:'/data/weather.json',from:'2026-09-16T00:00:00Z',to:'2026-09-16T01:00:00Z'})];
const legacy={...EMPTY_CATALOG,release_id:release,assets:detailed};
const catalog={...legacy,schema_version:2,assets:detailed.filter(a=>a.format!=='geojson'),
  indexes:[asset('region',{format:'asset-index',url:'/data/indexes/region.json'})],
  legacy_url:`/data/releases/${release}.v1.json`,coverage_url:'/data/indexes/coverage.json'};
const files=new Map([...built.files,
  ['/index.html',encode('<!doctype html><html><body>Fixture release</body></html>')],['/download-gate.js',encode('/* gate fixture */')],
  ['/data/catalog.json',encode(catalog)],[`/data/releases/${release}.json`,encode(catalog)],[catalog.legacy_url,encode(legacy)],
  [catalog.coverage_url,encode({schema_version:1,assets:detailed})],
  ['/data/indexes/region.json',encode({schema_version:1,assets:[detailed[0]]})],
  ['/data/road.geojson',encode({type:'FeatureCollection',features:[]})],['/data/weather.json',encode({frames:[]})],
]);
const manifest=[...files].map(([url,body])=>({target:url.slice(1),bytes:body.length,sha256:digest(body)}));
const receipt={complete:true,mode:'static',release_id:release,catalog_hash:digest(files.get('/data/catalog.json')),bundle_id:'fixture',manifest_hash:'a'.repeat(64),config_hash:'b'.repeat(64)};

function fixture(change){
  const storage={async fetch(request){
    const url=new URL(request.url),pathname=url.pathname==='/'?'/index.html':url.pathname,body=files.get(pathname);
    if(!body)return new Response(null,{status:404});
    const etag='"'+digest(body)+'"';
    if(request.headers.get('If-None-Match')===etag)return new Response(null,{status:304,headers:{ETag:etag}});
    const headers={'Content-Type':pathname.endsWith('.html')?'text/html':pathname.endsWith('.json')||pathname.endsWith('.geojson')?'application/json':'application/octet-stream',
      'Cache-Control':pathname==='/data/catalog.json'||pathname==='/download-gate.js'?'no-cache':'public, max-age=31536000, immutable',ETag:etag};
    return new Response(new Uint8Array(body),{headers});
  }};
  const env={ASSETS:storage,DATA_STORAGE:'static',STATIC_RELEASE_ID:release,ENVIRONMENT:'production',COLLECTORS_ENABLED:'false',CF_VERSION_METADATA:{id:version}};
  const fetcher=vi.fn(async(url,init)=>{
    const request=new Request(url,init),response=await handleRequest(request,env);
    return change?await change(request,response):response;
  });
  const searchReference=async query=>({places:searchPlaces(source,query),coverage:'published-index',indexed_count:source.length,candidate_count:15,omitted_count:15-source.length,index_limit:50000});
  const run=(options={})=>verifyRelease({origin,version,receipt,manifest,catalog,legacy,searchReference,fetcher,log:()=>{},...options});
  return {run,fetcher};
}

describe('remote release gate using a small offline static fixture',()=>{
  it('compares latest and pinned contracts and reaches the immutable share host',async()=>{
    const f=fixture(),result=await f.run();
    expect(result.checks.filter(c=>!c.passed)).toEqual([]);expect(result.passed).toBe(true);
    expect(result).toMatchObject({release_id:release,version,bundle_id:'fixture',catalog_hash:receipt.catalog_hash,verification_schema:2});
    const urls=f.fetcher.mock.calls.map(([url])=>url);
    expect(urls).toContain(preview+'/api/v1/runtime');
    expect(urls).toContain(preview+'/?deployment='+version);
    expect(urls).toContain(origin+'/api/v1/search?q=Incheon&release='+release);
    expect(f.fetcher.mock.calls.every(([,init])=>init.redirect==='manual')).toBe(true);
    const initial=await fixture().run({origin:preview});
    expect(initial.passed).toBe(true);
  });
  it.each([false,true])('rejects repeatable empty search results (pinned only: %s)',async pinnedOnly=>{
    const f=fixture(async(request,response)=>{
      const url=new URL(request.url);
      if(url.pathname==='/api/v1/search'&&response.ok&&url.searchParams.get('q')==='서울'&&(!pinnedOnly||url.searchParams.get('release')===release)){
        return Response.json({...await response.json(),places:[]});
      }
      return response;
    });
    const result=await f.run();expect(result.passed).toBe(false);
    expect(result.checks.find(c=>c.label==='search 서울')).toMatchObject({passed:false});
  });
  it('rejects an unavailable immutable preview even when the main address works',async()=>{
    const result=await fixture((request,response)=>new URL(request.url).origin===preview?new Response(null,{status:404}):response).run();
    expect(result.checks.find(c=>c.label==='runtime and free static configuration').passed).toBe(true);
    expect(result.checks.find(c=>c.label==='immutable preview origin and pinned share target').passed).toBe(false);
    expect(result.passed).toBe(false);
  });
  it('does not follow a version-host redirect to the latest deployment',async()=>{
    const f=fixture((request,response)=>request.url===preview+'/api/v1/runtime'?new Response(null,{status:302,headers:{Location:origin+'/api/v1/runtime'}}):response);
    const result=await f.run();expect(result.passed).toBe(false);
    expect(result.checks.find(c=>c.label==='immutable preview origin and pinned share target').error).toContain('Unexpected redirect');
  });
  it('allows only the same-origin index canonical redirect',async()=>{
    const result=await fixture((request,response)=>new URL(request.url).pathname==='/index.html'?new Response(null,{status:308,headers:{Location:'/'}}):response).run();
    expect(result.passed).toBe(true);
  });
  it('requires an exact preview origin, not only its version prefix',async()=>{
    const result=await fixture(async(request,response)=>{
      if(new URL(request.url).pathname==='/api/v1/runtime')return Response.json({...await response.json(),preview_origin:preview+'.evil.example'});
      return response;
    }).run();
    expect(result.checks.find(c=>c.label==='runtime and free static configuration').passed).toBe(false);
  });
  it('detects empty coverage layers and absent catalog validators',async()=>{
    const result=await fixture(async(request,response)=>{
      const url=new URL(request.url);
      if(url.pathname==='/api/v1/coverage'&&response.ok)return Response.json({...await response.json(),layers:[]});
      if(url.pathname==='/data/catalog.json')response.headers.delete('ETag');
      return response;
    }).run();
    expect(result.checks.find(c=>c.label==='exact latest and pinned spatial coverage compatibility').passed).toBe(false);
    expect(result.checks.find(c=>c.label==='catalog v2 hash, latest cache and v1 compatibility').passed).toBe(false);
  });
  it('detects the former empty-search missing-release fallback',async()=>{
    const result=await fixture((request,response)=>{
      const url=new URL(request.url);
      return url.pathname==='/api/v1/search'&&url.searchParams.get('release')==='pub-0000000000000000'&&!url.searchParams.get('q')?Response.json({places:[]}):response;
    }).run();
    expect(result.checks.find(c=>c.label==='missing assets and missing pinned releases').passed).toBe(false);
  });
  it('checks the small staged reference and hashes an asset only when the reference reads it',async()=>{
    const tempRoot=path.resolve(tmpdir()),bundle=await mkdtemp(path.join(tempRoot,'korea-replay-verifier-'));
    if(!bundle.startsWith(tempRoot+path.sep)||!path.basename(bundle).startsWith('korea-replay-verifier-'))throw new Error('Unsafe fixture directory');
    try{
      await Promise.all([...files].map(async([url,bytes])=>{
        const file=path.join(bundle,'client',url.slice(1));await mkdir(path.dirname(file),{recursive:true});await writeFile(file,bytes);
      }));
      const worker=encode("export default {async fetch(request,env){const response=await env.ASSETS.fetch(new Request('https://fixture.invalid/data/road.geojson'));return Response.json({places:[],road:(await response.json()).type});}};\n");
      const config=encode({fixture:true}),manifestBytes=encode(manifest),workerPath=path.join(bundle,'worker/index.js');
      await mkdir(path.dirname(workerPath),{recursive:true});await writeFile(workerPath,worker);
      await writeFile(path.join(bundle,'package.json'),'{"type":"module"}');
      await writeFile(path.join(bundle,'wrangler.json'),config);await writeFile(path.join(bundle,'asset-manifest.json'),manifestBytes);
      const staged={...receipt,bundle,count:manifest.length,audit:{passed:true,release_id:release},config_hash:digest(config),manifest_hash:digest(manifestBytes),worker_files:[{target:'worker/index.js',sha256:digest(worker)}]};
      const receiptPath=path.join(bundle,'receipt.json');await writeFile(receiptPath,JSON.stringify(staged));
      const loaded=await loadVerificationInput(receiptPath);
      expect(loaded.catalog).toEqual(catalog);expect(loaded.legacy).toEqual(legacy);
      expect(await loaded.searchReference('서울',release)).toEqual({places:[],road:'FeatureCollection'});
      await writeFile(path.join(bundle,'client/data/road.geojson'),'{}');
      await expect(loaded.searchReference('서울',release)).rejects.toThrow('Staged file changed');
      await writeFile(workerPath,'export default {};');
      await expect(loadVerificationInput(receiptPath)).rejects.toThrow('Staged Worker changed');
    }finally{
      // Only remove the exact temporary directory made by this test.
      await rm(bundle,{recursive:true,force:true});
    }
  });
});
