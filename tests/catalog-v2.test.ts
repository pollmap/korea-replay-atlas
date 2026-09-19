import {afterEach,expect,it,vi} from 'vitest';
import {EMPTY_CATALOG} from '../shared/sources';
import type {Asset} from '../shared/contracts';
import {fetchPublicCatalog,normalizePublicCatalog,resolveSceneAssets} from '../src/catalog';

const release='pub-0123456789abcdef';
const asset:Asset={id:'detail',layer:'infrastructure',format:'geojson',url:'/data/detail.geojson',bbox:[127,36,128,37],count:1,source_id:'test',version:'1',sha256:'fixture'};
const index:Asset={...asset,id:'index',format:'asset-index',url:'/data/index.json',max_camera_height:10000};
const published={...EMPTY_CATALOG,schema_version:2,release_id:release,assets:[],indexes:[index],legacy_url:'/data/legacy.json'};
afterEach(()=>vi.unstubAllGlobals());

it('revalidates the compact catalog and reuses the parsed matching ETag',async()=>{
  const fetcher=vi.fn().mockResolvedValueOnce(Response.json(published,{headers:{ETag:'"v2"'}})).mockResolvedValueOnce(new Response(null,{status:304}));
  vi.stubGlobal('fetch',fetcher);
  const first=await fetchPublicCatalog(null),second=await fetchPublicCatalog(null);
  expect(second).toBe(first);expect(second.assets).toEqual([index]);
  expect(fetcher.mock.calls[1][1].headers).toEqual({'If-None-Match':'"v2"'});
});
it('fetches only regional manifests in view and retains a coarse fallback nearby',async()=>{
  const overview={...index,id:'overview',url:'/data/overview.json',detail_level:'overview',min_camera_height:10000,max_camera_height:undefined};
  const catalog=normalizePublicCatalog({...published,indexes:[index,overview,{...index,id:'far',bbox:[130,34,131,35]}]},null);
  const fetcher=vi.fn(async(url)=>Response.json({schema_version:1,assets:[{...asset,id:String(url)}]}));
  const result=await resolveSceneAssets(catalog,[127.3,36.3,127.4,36.4],2000,undefined,fetcher);
  expect(fetcher.mock.calls.map(x=>x[0])).toEqual(['/data/index.json','/data/overview.json']);
  expect(result).toHaveLength(2);
});
it('reuses in-flight manifests when a camera move still requires them',async()=>{
  let done!:(r:Response)=>void;const response=new Promise<Response>(resolve=>{done=resolve;});
  const fetcher=vi.fn(()=>response),catalog=normalizePublicCatalog(published,null);
  const abort=new AbortController();
  const a=resolveSceneAssets(catalog,asset.bbox,1000,abort.signal,fetcher);
  const b=resolveSceneAssets(catalog,asset.bbox,1000,undefined,fetcher);
  abort.abort();done(Response.json({schema_version:1,assets:[asset]}));
  await expect(a).rejects.toMatchObject({name:'AbortError'});expect(await b).toEqual([asset]);expect(fetcher).toHaveBeenCalledTimes(1);
});
it('reuses a nationwide view with hundreds of small manifests on a second visit',async()=>{
  const indexes=Array.from({length:180},(_,i)=>({...index,id:`region-${i}`,url:`/data/region-${i}.json`,byte_length:800}));
  const catalog=normalizePublicCatalog({...published,indexes},null);
  const fetcher=vi.fn(async(url)=>Response.json({schema_version:1,assets:[{...asset,id:String(url)}]}));
  const first=await resolveSceneAssets(catalog,asset.bbox,1000,undefined,fetcher);
  const second=await resolveSceneAssets(catalog,asset.bbox,1000,undefined,fetcher);
  expect(first).toEqual(second);expect(first).toHaveLength(180);expect(fetcher).toHaveBeenCalledTimes(180);
});
it('rejects altered immutable manifests and retries instead of caching a failure',async()=>{
  const catalog=normalizePublicCatalog({...published,indexes:[{...index,sha256:'a'.repeat(64)}]},null);
  const fetcher=vi.fn(async()=>Response.json({schema_version:1,assets:[asset]}));
  await expect(resolveSceneAssets(catalog,asset.bbox,1000,undefined,fetcher)).rejects.toThrow('해시');
  await expect(resolveSceneAssets(catalog,asset.bbox,1000,undefined,fetcher)).rejects.toThrow('해시');
  expect(fetcher).toHaveBeenCalledTimes(2);
});
it('does not silently replace a missing or mismatching shared release',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(null,{status:404})));
  await expect(fetchPublicCatalog(release)).rejects.toThrow('공유한');
  expect(()=>normalizePublicCatalog(published,'pub-1111111111111111')).toThrow();
});
it('shares a global four-request budget across concurrent camera queries',async()=>{
  const indexes=Array.from({length:12},(_,i)=>({...index,id:String(i),url:`/data/${i}.json`}));
  const catalog=normalizePublicCatalog({...published,indexes},null);let active=0,max=0;
  const fetcher=vi.fn(async()=>{active++;max=Math.max(max,active);await new Promise(resolve=>setTimeout(resolve,2));active--;return Response.json({schema_version:1,assets:[asset]});});
  await Promise.all([resolveSceneAssets(catalog,asset.bbox,1000,undefined,fetcher),resolveSceneAssets(catalog,asset.bbox,1000,undefined,fetcher)]);
  expect(max).toBeLessThanOrEqual(4);expect(fetcher).toHaveBeenCalledTimes(12);
});
it('publishes the core immediately and completed regions before a slow region finishes',async()=>{
  const core={...asset,id:'base',source_id:'natural-earth'},updates:Asset[][]=[];
  let finish!:(response:Response)=>void;
  const slow=new Promise<Response>(resolve=>{finish=resolve;});
  const fetcher=vi.fn(async(url)=>String(url).endsWith('slow.json')?slow:Response.json({schema_version:1,assets:[asset]}));
  const catalog=normalizePublicCatalog({...published,assets:[core],indexes:[index,{...index,id:'slow',url:'/data/slow.json'}]},null);
  const pending=resolveSceneAssets(catalog,asset.bbox,1000,undefined,fetcher,rows=>updates.push(rows));
  expect(updates).toEqual([[core]]);
  await vi.waitFor(()=>expect(updates.some(rows=>rows.some(row=>row.id==='detail'))).toBe(true));
  finish(Response.json({schema_version:1,assets:[{...asset,id:'late'}]}));
  expect((await pending).map(row=>row.id)).toEqual(['base','detail','late']);
});
it('cancels abandoned region reads promptly and frees slots for the next view',async()=>{
  let cancelled=0;
  const fetcher=vi.fn<typeof fetch>((url,init)=>new Promise((resolve,reject)=>{
    if(String(url).endsWith('next.json')){resolve(Response.json({schema_version:1,assets:[asset]}));return;}
    init!.signal!.addEventListener('abort',()=>{cancelled++;reject(new DOMException('Aborted','AbortError'));},{once:true});
  }));
  const before=normalizePublicCatalog({...published,indexes:Array.from({length:12},(_,i)=>({...index,id:`r${i}`,url:`/data/old-${i}.json`}))},null);
  const controller=new AbortController(),pending=resolveSceneAssets(before,asset.bbox,1000,controller.signal,fetcher);
  const rejected=expect(pending).rejects.toMatchObject({name:'AbortError'});
  await vi.waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(4));controller.abort();await rejected;
  const next=normalizePublicCatalog({...published,indexes:[{...index,url:'/data/next.json'}]},null);
  expect(await resolveSceneAssets(next,asset.bbox,1000,undefined,fetcher)).toEqual([asset]);
  await vi.waitFor(()=>expect(cancelled).toBe(4));expect(fetcher).toHaveBeenCalledTimes(5);
});
it('does not request disabled layers or an overview above its maximum height',async()=>{
  const catalog=normalizePublicCatalog({...published,indexes:[{...index,layer:'buildings'},
    {...index,id:'coarse',detail_level:'overview',max_camera_height:5000,url:'/data/coarse.json'}]},null);
  const fetcher=vi.fn();
  expect(await resolveSceneAssets(catalog,asset.bbox,6000,undefined,fetcher,undefined,{terrain:true,buildings:false,infrastructure:true,rail:false,bus:false,depth:false,radar:false,satellite:false,sun:true})).toEqual([]);
  expect(fetcher).not.toHaveBeenCalled();
});
