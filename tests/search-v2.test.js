import {describe,it,expect,vi} from 'vitest';
import {createHash} from 'node:crypto';
import {buildSearchFiles} from '../scripts/build-search-v2.mjs';
import {createPublishedPlaceSearch,searchBucket} from '../shared/search-v2';
import {searchPlaces} from '../shared/search';
import {createSearchRunner} from '../src/search.worker';

const entries=Array.from({length:900},(_,i)=>({id:`id-${i%170}`,name:`${['청주','서울','가역','Ａ항','a항','마을','x청주','Z'][i%8]} ${i%59}`,alt_names:[`Airport ${i%19}`,'별칭'],region:i%2?'지역':'',lon:127,lat:36,range:3000}));
entries.push({id:'id-1',name:'청주',region:'정확',lon:127,lat:36,range:3000},{id:'alias',name:'별도 장소',alt_names:['Cheongju Airport','단어 공간 경계'],region:'기타',lon:127.49,lat:36.72,range:9000},{id:'invalid',name:'잘못된좌표',region:'',lon:0,lat:0,range:100});
entries.push({id:'punctuation',name:'따옴표"곳: [연구소]',alt_names:['quote,"q','줄\n바꿈'],region:'기타',lon:127,lat:36,range:3000},{id:'emoji',name:'😀역',region:'기타',lon:127,lat:36,range:3000},{id:'prototype',name:'__proto__',region:'기타',lon:127,lat:36,range:3000});
const built=buildSearchFiles(entries,{candidate_count:entries.length+123,source_sha256:'a'.repeat(64)});
function fixture(result=built){
  const seen=[];
  const fetcher=vi.fn(async(url)=>{seen.push(url);const data=result.files.get(url);return data?new Response(new Uint8Array(data)):new Response(null,{status:404});});
  return {fetcher,seen,search:createPublishedPlaceSearch(fetcher),descriptor:result.descriptor};
}
describe('sharded immutable search v2',()=>{
  it('preserves source preparation, exact/prefix/alias order and duplicate winners',async()=>{
    const f=fixture();
    for(const q of ['가','청주','서울','역','항','ａ','Ａ항','Ａ Ｉ Ｒ Ｐ Ｏ Ｒ Ｔ','Airport 1','마을','지역','없음','３','별칭','어공간경','잘못된좌표','따옴표"','곳:','[연구소]','quote,"q','😀','줄 바꿈','__proto__','q','']){
      expect((await f.search(f.descriptor,q)).places,q).toEqual(searchPlaces(entries,q));
    }
    expect((await f.search(f.descriptor,'청주')).metadata).toEqual({indexed_count:entries.length,candidate_count:entries.length+123,omitted_count:123,index_limit:50000});
  });
  it('builds deterministic immutable pages and limits total asset count',()=>{
    const again=buildSearchFiles(entries,{candidate_count:entries.length+123,source_sha256:'a'.repeat(64)});
    expect(again.descriptor).toEqual(built.descriptor);
    expect([...again.files.keys()]).toEqual([...built.files.keys()]);
    for(const [url,content] of built.files)expect(again.files.get(url).equals(content)).toBe(true);
    expect(built.files.size).toBe(128+Math.ceil(built.manifest.row_count/512)+1);
    expect(built.files.size).toBeLessThanOrEqual(266);
  });
  it('rejects a negative query using bucket lookups without fetching record pages',async()=>{
    const f=fixture();expect((await f.search(f.descriptor,'없는음절쀍쀍')).places).toEqual([]);
    expect(f.seen).toHaveLength(3);
    expect(f.seen.some(url=>url.includes('/page-'))).toBe(false);
    const count=f.seen.length;await f.search(f.descriptor,'없는음절쀍쀍');expect(f.seen).toHaveLength(count);
  });
  it('coalesces simultaneous manifest/shard reads and returns independent result objects',async()=>{
    const f=fixture();const [a,b]=await Promise.all([f.search(f.descriptor,'청주'),f.search(f.descriptor,'청주')]);
    expect(new Set(f.seen).size).toBe(f.seen.length);expect(a.places).toEqual(b.places);
    a.places[0].name='변경';expect((await f.search(f.descriptor,'청주')).places[0].name).not.toBe('변경');
  });
  it('reports missing and integrity failures distinctly, then retries the missing shard',async()=>{
    const f=fixture(),target=built.manifest.buckets[searchBucket('청주')].url;let fail=true;
    const runner=createPublishedPlaceSearch(async(url)=>url===target&&fail?new Response(null,{status:404}):f.fetcher(url));
    await expect(runner(f.descriptor,'청주')).rejects.toMatchObject({code:'missing'});
    fail=false;expect((await runner(f.descriptor,'청주')).places.length).toBeGreaterThan(0);
    const corrupt=createPublishedPlaceSearch(async(url)=>url===target?new Response('{}'):f.fetcher(url));
    await expect(corrupt(f.descriptor,'청주')).rejects.toMatchObject({code:'integrity'});
  });
  it('exposes budget exhaustion instead of claiming an incomplete successful search',async()=>{
    const f=fixture(),limited=createPublishedPlaceSearch(f.fetcher,{maxRequests:1});
    await expect(limited(f.descriptor,'청주')).rejects.toMatchObject({code:'budget'});
    expect(f.seen).toHaveLength(1);
  });
  it('stops obsolete queries while reusing an in-flight immutable manifest',async()=>{
    let release;const gate=new Promise(resolve=>{release=resolve;});const f=fixture();
    const search=createPublishedPlaceSearch(async(url)=>{if(url===f.descriptor.url)await gate;return f.fetcher(url);});
    const controller=new AbortController(),old=search(f.descriptor,'청주',{signal:controller.signal});
    const assertion=expect(old).rejects.toMatchObject({name:'AbortError'});
    controller.abort();const current=search(f.descriptor,'서울');release();
    await assertion;expect((await current).places).toEqual(searchPlaces(entries,'서울'));
    expect(f.seen.filter(url=>url===f.descriptor.url)).toHaveLength(1);
  });
  it('bounds reusable shard cache and prevents hidden version fallback',async()=>{
    const f=fixture(),search=createPublishedPlaceSearch(f.fetcher,{cacheEntries:1,cacheBytes:256*1024});
    await search(f.descriptor,'없는음절쀍쀍');const count=f.seen.length;
    await search(f.descriptor,'없는음절쀍쀍');expect(f.seen.length).toBeGreaterThan(count);
    await expect(search({url:'/data/search-v2/absent.json',sha256:'b'.repeat(64)},'청주')).rejects.toMatchObject({code:'missing'});
  });
  it('rejects tampered manifests, remote shard paths, and inconsistent coverage',async()=>{
    const value={...built.manifest,pages:[{...built.manifest.pages[0],url:'https://example.com/private.json'},...built.manifest.pages.slice(1)]};
    const body=JSON.stringify(value),sha256=createHash('sha256').update(body).digest('hex');
    const search=createPublishedPlaceSearch(async()=>new Response(body));
    await expect(search({url:'/data/manifest.json',sha256},'청주')).rejects.toMatchObject({code:'invalid-data'});
    await expect(search({url:'/data/%2e%2e/manifest.json',sha256},'청주')).rejects.toMatchObject({code:'invalid-path'});
    const invalid=createPublishedPlaceSearch(async()=>Response.json({...built.manifest,omitted_count:0}));
    await expect(invalid({url:'/data/manifest.json',sha256:'test'},'청주')).rejects.toMatchObject({code:'invalid-data'});
  });
  it('validates byte offsets before decoding a selected UTF-8 record',async()=>{
    const current=structuredClone(built.manifest),files=new Map(built.files),first=current.pages[0];
    const original=files.get(first.url),newline=original.indexOf(10),header=JSON.parse(original.subarray(0,newline).toString('utf8'));
    const records=original.subarray(newline+1),firstRow=JSON.parse(records.subarray(header.offsets[0],header.offsets[1]-1).toString('utf8'));
    header.offsets[0]=-1;
    const damaged=Buffer.concat([Buffer.from(JSON.stringify(header)+'\n'),records]);
    files.set(first.url,damaged);Object.assign(first,{sha256:createHash('sha256').update(damaged).digest('hex'),byte_length:damaged.length});
    const manifest=Buffer.from(JSON.stringify(current)),descriptor={url:'/data/offset-fixture.json',sha256:createHash('sha256').update(manifest).digest('hex')};
    files.set(descriptor.url,manifest);const f=fixture({files,descriptor});
    await expect(f.search(descriptor,firstRow.place.name)).rejects.toMatchObject({code:'invalid-data'});
  });
  it('integrates with the browser worker and keeps legacy v1 support',async()=>{
    const f=fixture(),run=createSearchRunner(f.fetcher);
    const result=await run({id:123,query:'Airport',index:f.descriptor});
    expect(result).toMatchObject({id:123,indexed_count:entries.length,index_limit:50000});
    expect(result.places).toEqual(searchPlaces(entries,'Airport'));
    const legacy=createPublishedPlaceSearch(async()=>Response.json({entries,entry_count:entries.length}));
    expect((await legacy({url:'/data/legacy.json',sha256:'legacy'},'청주')).places).toEqual(searchPlaces(entries,'청주'));
  });
});
