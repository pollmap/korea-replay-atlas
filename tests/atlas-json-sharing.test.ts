import {createHash} from 'node:crypto';
import {afterEach,expect,it,vi} from 'vitest';
import {fetchPinnedJson,atlasNetworkStats} from '../src/atlas-client';
afterEach(()=>vi.unstubAllGlobals());
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
