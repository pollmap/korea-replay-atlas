import {createHash} from 'node:crypto';
import {afterEach,expect,it,vi} from 'vitest';
import {fetchPinnedJson,fetchPinnedPoiJson,fetchPinnedMapPriceJson,atlasNetworkStats} from '../src/atlas-client';
afterEach(()=>vi.unstubAllGlobals());
it('pins current map prices and rejects changed bytes, foreign URLs and oversize metadata before use',async()=>{
  vi.stubGlobal('location',{origin:'https://example.com'});
  const body='{"case":"map-prices"}',sha256=createHash('sha256').update(body).digest('hex'),ref={sha256,bytes:Buffer.byteLength(body)};
  const url='/assets/property-map-price-ceeff63959643461-11710-abcdefgh.json',fetcher=vi.fn(async()=>new Response(body));vi.stubGlobal('fetch',fetcher);
  expect(await fetchPinnedMapPriceJson(url,ref,new AbortController().signal)).toEqual({case:'map-prices'});
  expect(await fetchPinnedMapPriceJson(url,ref,new AbortController().signal)).toEqual({case:'map-prices'});expect(fetcher).toHaveBeenCalledTimes(1);
  await expect(fetchPinnedMapPriceJson(url,{...ref,bytes:ref.bytes+1},new AbortController().signal)).rejects.toThrow('크기');
  for(const invalid of ['https://other.example'+url,url+'?x=1','/assets/11710-abcdefgh.json'])await expect(fetchPinnedMapPriceJson(invalid,ref,new AbortController().signal)).rejects.toThrow('참조');
  await expect(fetchPinnedMapPriceJson(url,{...ref,bytes:1024*1024+1},new AbortController().signal)).rejects.toThrow('참조');
  const controller=new AbortController();controller.abort();
  await expect(fetchPinnedMapPriceJson(url,ref,controller.signal)).rejects.toMatchObject({name:'AbortError'});
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(body+' ')));
  await expect(fetchPinnedMapPriceJson(url.replace('abcdefgh','ijklmnop'),ref,new AbortController().signal)).rejects.toThrow('내용');
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(body.replace('prices','priceX'))));
  await expect(fetchPinnedMapPriceJson(url.replace('abcdefgh','qrstuvwx'),ref,new AbortController().signal)).rejects.toThrow('내용');
});
it('shares hash-verified facility assets without widening the data URL contract',async()=>{
  vi.stubGlobal('location',{origin:'https://example.com'});
  const body='{"case":"facilities"}',sha256=createHash('sha256').update(body).digest('hex'),ref={sha256,bytes:Buffer.byteLength(body)};
  const url=`/assets/poi-${sha256}-abcdefgh.json`,fetcher=vi.fn(async()=>new Response(body));vi.stubGlobal('fetch',fetcher);
  expect(await fetchPinnedPoiJson(url,ref,new AbortController().signal)).toEqual({case:'facilities'});
  expect(await fetchPinnedPoiJson(url,ref,new AbortController().signal)).toEqual({case:'facilities'});expect(fetcher).toHaveBeenCalledTimes(1);
  await expect(fetchPinnedPoiJson(url,{...ref,bytes:ref.bytes+1},new AbortController().signal)).rejects.toThrow('크기');
  for(const invalid of ['https://other.example'+url,url+'?x=1','/assets/unlisted.json'])await expect(fetchPinnedPoiJson(invalid,ref,new AbortController().signal)).rejects.toThrow('참조');
  await expect(fetchPinnedJson({...ref,url},'https://example.com',new AbortController().signal)).rejects.toThrow('참조');
});
const reference=(body:string,path:string)=>({url:`/data/${path}.json`,sha256:createHash('sha256').update(body).digest('hex'),bytes:Buffer.byteLength(body)});
it('shares one verified response and preserves the request when one subscriber leaves',async()=>{
  const body='{"case":"shared"}',ref=reference(body,'shared');let finish!:(value:Response)=>void;
  const fetcher=vi.fn(()=>new Promise<Response>(resolve=>{finish=resolve;}));vi.stubGlobal('fetch',fetcher);
  const first=new AbortController(),second=new AbortController();
  const one=fetchPinnedJson(ref,'https://example.com',first.signal),two=fetchPinnedJson(ref,'https://example.com',second.signal);
  await vi.waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(1));
  const cancelled=expect(one).rejects.toMatchObject({name:'AbortError'});first.abort();finish(new Response(body));
  await cancelled;expect(await two).toEqual({case:'shared'});expect(fetcher).toHaveBeenCalledTimes(1);expect(atlasNetworkStats().pendingJsonRequests).toBe(0);
});
it('checks size for every subscriber even when their response is shared',async()=>{
  const body='{"case":"sizes"}',ref=reference(body,'sizes');vi.stubGlobal('fetch',vi.fn(async()=>new Response(body)));
  const good=fetchPinnedJson(ref,'https://example.com',new AbortController().signal);
  const bad=fetchPinnedJson({...ref,bytes:ref.bytes+1},'https://example.com',new AbortController().signal);
  await expect(bad).rejects.toThrow('크기');expect(await good).toEqual({case:'sizes'});
});
it('cancels the shared transfer when all consumers leave and does not cache failure',async()=>{
  const body='{"case":"cancelled"}',ref=reference(body,'cancelled');let cancelled=false;
  const fetcher=vi.fn((_input,init)=>new Promise((_resolve,reject)=>{init.signal.addEventListener('abort',()=>{cancelled=true;reject(new DOMException('Aborted','AbortError'));});}));vi.stubGlobal('fetch',fetcher);
  const controller=new AbortController(),value=fetchPinnedJson(ref,'https://example.com',controller.signal);
  await vi.waitFor(()=>expect(fetcher).toHaveBeenCalledTimes(1));
  const check=expect(value).rejects.toMatchObject({name:'AbortError'});controller.abort();await check;expect(cancelled).toBe(true);
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(body)));expect(await fetchPinnedJson(ref,'https://example.com',new AbortController().signal)).toEqual({case:'cancelled'});
});
