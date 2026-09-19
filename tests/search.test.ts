import {afterEach,describe,it,expect,vi} from 'vitest';
import {preparePlaceSearch,searchPlaces,searchPreparedPlaces,type SearchEntry} from '../shared/search';
import {handleRequest,type Env} from '../worker/index';
import {EMPTY_CATALOG,PLACES} from '../shared/sources';
import type {Place} from '../shared/contracts';
import {createSearchRunner} from '../src/search.worker';
import {fetchPublicCatalog} from '../src/catalog';
const entries:SearchEntry[]=[{id:'airport-1',name:'청주국제공항',alt_names:['Cheongju Airport'],region:'공항',lon:127.49,lat:36.72,range:9000},{id:'invalid',name:'청주 오류',region:'',lon:0,lat:0,range:100}];
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});

function reference(entries:SearchEntry[],query:string):Place[]{
  const normalize=(s:string)=>s.normalize('NFKC').toLocaleLowerCase().replace(/\s/g,'');
  const q=normalize(query);if(!q)return PLACES;
  const candidates:SearchEntry[]=[...PLACES,...entries];
  return candidates.filter(p=>Number.isFinite(p.lon)&&Number.isFinite(p.lat)&&p.lon>=124&&p.lon<=132.5&&p.lat>=32&&p.lat<=39.5)
    .map(p=>({p,score:normalize(p.name)===q?0:normalize(p.name).startsWith(q)?1:normalize(`${p.name} ${p.region??''} ${p.alt_names?.join(' ')??''}`).includes(q)?2:3}))
    .filter(x=>x.score<3).sort((a,b)=>a.score-b.score||a.p.name.localeCompare(b.p.name,'ko')).filter((x,i,all)=>all.findIndex(y=>y.p.id===x.p.id)===i).slice(0,20)
    .map(({p})=>({id:p.id,name:p.name,region:p.region||p.kind_label||'공개 지도 시설',lon:p.lon,lat:p.lat,range:Number.isFinite(p.range)?Math.min(2500000,Math.max(500,p.range)):5000}));
}

function fixture(host:string,values:SearchEntry[]=entries){
  let version=1,indexFailures=0;
  const seen:string[]=[];
  const env={ASSETS:{fetch:async(request:Request)=>{
    const path=new URL(request.url).pathname;seen.push(path);
    if(path.includes('/index-')){
      if(indexFailures-->0)return new Response(null,{status:503});
      return Response.json({entries:values,entry_count:values.length,candidate_count:values.length+5,omitted_count:5});
    }
    return Response.json({...EMPTY_CATALOG,release_id:`pub-${String(version).repeat(16)}`,assets:[{id:'search',format:'search-index',url:`/data/index-${version}.json`,sha256:`source-${version}`}]});
  }}} as unknown as Env;
  return {env,seen,url:`http://${host}/api/v1/search?q=airport`,next:()=>{version++;},failIndex:()=>{indexFailures=1;}};
}
describe('published infrastructure search',()=>{
  it('matches Korean spacing and English aliases while rejecting invalid coordinates',()=>{
    expect(searchPlaces(entries,'청주 국제 공항')[0].id).toBe('airport-1');
    expect(searchPlaces(entries,'CHEONGJU AIRPORT')[0].id).toBe('airport-1');
    expect(searchPlaces(entries,'오류')).toHaveLength(0);
  });
  it('reads the selected release index through the public path',async()=>{
    const c={...EMPTY_CATALOG,release_id:'pub-1111111111111111',assets:[{id:'search',format:'search-index',url:'/data/search.json',sha256:'test-search-index'}]};
    const seen:string[]=[];
    const env={ASSETS:{fetch:async(request:Request)=>{seen.push(new URL(request.url).pathname);return Response.json(request.url.endsWith('search.json')?{entries}:c);}}} as unknown as Env;
    const response=await handleRequest(new Request('http://search-test/api/v1/search?q=airport&release=pub-1111111111111111'),env);
    expect(response.status).toBe(200);expect((await response.json() as {places:SearchEntry[]}).places[0].id).toBe('airport-1');
    expect(seen).toEqual(['/data/releases/pub-1111111111111111.json','/data/search.json']);
  });
  it('preserves exact/prefix/alias ranking and duplicate winners against a full-sort oracle',()=>{
    const generated:SearchEntry[]=Array.from({length:420},(_,i)=>({id:`id-${i%73}`,name:`${['가역','청주','서울','Ａ항','a항','마을','x청주','z'][i%8]} ${i%39}`,alt_names:[`Airport ${i%13}`],region:i%2?'지역':'',lon:127,lat:36,range:3000}));
    generated.push({...entries[0],id:'id-1',name:'청주'},...entries);
    const prepared=preparePlaceSearch(generated);
    for(const query of ['가','청주','서울','역','항','ａ','Ａ항','Ａ Ｉ Ｒ Ｐ Ｏ Ｒ Ｔ','Airport 1','마을','지역','없음','３','']){
      expect(searchPreparedPlaces(prepared,query),query).toEqual(reference(generated,query));
    }
  });
  it('reuses prepared immutable entries without exposing mutable cached results',()=>{
    const local=entries.slice();const prepared=preparePlaceSearch(local);
    expect(preparePlaceSearch(local)).toBe(prepared);
    const first=searchPreparedPlaces(prepared,'airport');first[0].name='changed';
    expect(searchPreparedPlaces(prepared,'airport')[0].name).toBe('청주국제공항');
    expect(searchPlaces([{...entries[0],range:9000000}],'airport')[0].range).toBe(2500000);
  });
  it('coalesces concurrent catalog/index reads and exposes the published coverage limit',async()=>{
    const f=fixture('search-coalesce');
    const responses=await Promise.all(Array.from({length:8},()=>handleRequest(new Request(f.url),f.env)));
    expect(f.seen).toEqual(['/data/catalog.json','/data/index-1.json']);
    const body=await responses[0].json() as Record<string,unknown>;
    expect(body).toMatchObject({indexed_count:2,candidate_count:7,omitted_count:5,index_limit:50000,coverage:'published-index'});
    await handleRequest(new Request(f.url),f.env);
    expect(f.seen).toHaveLength(2);
  });
  it('refreshes latest descriptors after 30 seconds but retains immutable release descriptors',async()=>{
    const time=vi.spyOn(Date,'now').mockReturnValue(1000);const f=fixture('search-refresh');
    await handleRequest(new Request(f.url),f.env);
    f.next();time.mockReturnValue(30999);await handleRequest(new Request(f.url),f.env);
    expect(f.seen).toHaveLength(2);
    time.mockReturnValue(31001);await handleRequest(new Request(f.url),f.env);
    expect(f.seen).toEqual(['/data/catalog.json','/data/index-1.json','/data/catalog.json','/data/index-2.json']);
    const pinned=fixture('search-pinned');const url=pinned.url+'&release=pub-1111111111111111';
    await handleRequest(new Request(url),pinned.env);time.mockReturnValue(9999999);
    await handleRequest(new Request(url),pinned.env);
    expect(pinned.seen).toHaveLength(2);
  });
  it('retries a failed index load and isolates storage bindings at the same origin/hash',async()=>{
    const a=fixture('same-search-host'),b=fixture('same-search-host',[{...entries[0],id:'other-source'}]);
    a.failIndex();expect((await handleRequest(new Request(a.url),a.env)).status).toBe(503);
    expect((await handleRequest(new Request(a.url),a.env)).status).toBe(200);
    const response=await handleRequest(new Request(b.url),b.env);
    expect((await response.json() as {places:Place[]}).places[0].id).toBe('other-source');
    expect(a.seen.filter(path=>path.includes('/index-'))).toHaveLength(2);
    expect(b.seen).toHaveLength(2);
  });
  it('rejects an oversized published index and invalid release without caching them',async()=>{
    const f=fixture('search-cap',Array.from({length:50001},()=>entries[0]));
    const invalid=await handleRequest(new Request(f.url+'&release=invalid'),f.env);
    expect(invalid.status).toBe(400);expect(f.seen).toHaveLength(0);
    expect((await handleRequest(new Request(f.url),f.env)).status).toBe(503);
  });
});

describe('browser search and direct catalog delivery',()=>{
  it('shares browser index loading and returns only the bounded results with request IDs',async()=>{
    const fetcher=vi.fn(async()=>Response.json({entries,entry_count:entries.length}));
    const run=createSearchRunner(fetcher);
    const index={url:'/data/search.json',sha256:'one'};
    const results=await Promise.all([run({id:1,query:'airport',index}),run({id:2,query:'청주',index})]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(results[0]).toMatchObject({id:1,indexed_count:2,index_limit:50000,places:[{id:'airport-1'}]});
    expect(results[1].id).toBe(2);
    expect(results.every(result=>result.places.length<=20)).toBe(true);
    await run({id:3,query:'airport',index});expect(fetcher).toHaveBeenCalledTimes(1);
    await run({id:4,query:'airport',index:{...index,sha256:'two'}});expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('rejects oversized or remote index data, and retries a failed browser load',async()=>{
    const fetcher=vi.fn().mockResolvedValueOnce(new Response(null,{status:503})).mockImplementation(async()=>Response.json({entries}));
    const run=createSearchRunner(fetcher),index={url:'/data/search.json',sha256:'one'};
    expect((await run({id:1,query:'airport',index})).error).toBeDefined();
    expect((await run({id:2,query:'airport',index})).places[0].id).toBe('airport-1');
    expect((await run({id:3,query:'airport',index:{...index,url:'https://example.com/search.json'}})).error).toBeDefined();
    expect(fetcher).toHaveBeenCalledTimes(2);
    const oversized=createSearchRunner(async()=>Response.json({entries:Array.from({length:50001},()=>entries[0])}));
    expect((await oversized({id:4,query:'airport',index})).error).toBeDefined();
    expect((await run({id:5,query:'서울'})).places.some(place=>place.id==='seoul')).toBe(true);
  });
  it('parses catalog on the client and treats only a missing latest catalog as empty',async()=>{
    const fetcher=vi.fn(async()=>new Response(null,{status:404}));vi.stubGlobal('fetch',fetcher);
    expect(await fetchPublicCatalog(null)).toBe(EMPTY_CATALOG);
    expect(fetcher).toHaveBeenLastCalledWith('/data/catalog.json',{signal:undefined});
    await expect(fetchPublicCatalog('pub-1111111111111111')).rejects.toThrow('공유한 공개 버전');
    expect(fetcher).toHaveBeenLastCalledWith('/data/releases/pub-1111111111111111.json',{signal:undefined});
    const calls=fetcher.mock.calls.length;
    await expect(fetchPublicCatalog('../bad')).rejects.toThrow('형식');expect(fetcher).toHaveBeenCalledTimes(calls);
  });
  it('rejects mismatched pinned catalogs and malformed static content',async()=>{
    const fetcher=vi.fn().mockResolvedValueOnce(Response.json({...EMPTY_CATALOG,release_id:'pub-2222222222222222'})).mockResolvedValueOnce(Response.json({})).mockResolvedValueOnce(Response.json(EMPTY_CATALOG));vi.stubGlobal('fetch',fetcher);
    await expect(fetchPublicCatalog('pub-1111111111111111')).rejects.toThrow('형식');
    await expect(fetchPublicCatalog(null)).rejects.toThrow('형식');
    expect(await fetchPublicCatalog(null)).toEqual(EMPTY_CATALOG);
  });
});
