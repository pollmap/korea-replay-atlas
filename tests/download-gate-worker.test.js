import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {afterEach,describe,expect,it,vi} from 'vitest';

const origin='https://korea-replay.example.workers.dev';
const script=readFileSync(new URL('../src/download-gate.worker.js',import.meta.url),'utf8').replaceAll('__DOWNLOAD_GATE_VERSION__','1234567890abcdef');
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function harness(fetcher){
  const handlers=new Map(),claim=vi.fn(async()=>{});
  const scope={location:{origin},crypto:{randomUUID:()=> 'test-worker-instance'},clients:{claim},fetch:fetcher,addEventListener:(name,handler)=>handlers.set(name,handler)};
  runInNewContext(script,{self:scope,URL,Request,Response,Headers,AbortController,DOMException,TypeError,Uint8Array,ArrayBuffer,setTimeout,clearTimeout,Date});
  const request=(path,options)=>{
    let response;
    handlers.get('fetch')({request:new Request(new URL(path,origin),options),respondWith:value=>{response=value;}});
    return response;
  };
  const status=()=>{
    let value;
    handlers.get('message')({data:{type:'korea-download-gate-status'},ports:[{postMessage:message=>{value=message;}}]});
    return value;
  };
  return {request,status,claim,handlers};
}
function heldDownloads(){
  const records=[];
  const fetcher=vi.fn(async(request,{signal})=>{
    const record={path:new URL(request.url).pathname,signal,controller:null,cancelled:0};
    records.push(record);
    return new Response(new ReadableStream({start(controller){record.controller=controller;},cancel(){record.cancelled++;}}));
  });
  const finish=record=>{if(!record.signal.aborted)record.controller.close();};
  return {fetcher,records,finish};
}
afterEach(()=>vi.useRealTimers());
describe('the real same-origin map download service worker',()=>{
  it('holds the four shared slots through full bodies, including worker, image and tile paths',async()=>{
    const transport=heldDownloads(),gate=harness(transport.fetcher);
    const paths=['/data/terrain/0.terrain','/data/buildings/1.glb','/data/geometry/roads.geojson','/data/weather/frame.png','/data/index/city.json','/data/rail/2.geojson','/data/buildings/3.glb','/data/buildings/4.glb','/data/weather/5.png'];
    const responses=paths.map(path=>gate.request(path));
    await flush();expect(transport.records).toHaveLength(4);expect(gate.status()).toMatchObject({active:4,queued:5,peak:4,completed:0});
    transport.records[0].controller.enqueue(new Uint8Array([1,2,3]));
    await flush();expect(transport.records).toHaveLength(4);
    for(let i=0;i<paths.length;i++){transport.finish(transport.records[i]);await flush();expect(gate.status().active).toBeLessThanOrEqual(4);}
    expect(await Promise.all(responses)).toHaveLength(paths.length);
    expect(gate.status()).toMatchObject({active:0,queued:0,peak:4,completed:9,bytes:3});
  });
  it('prioritizes waiting catalog and small JSON before large geometry',async()=>{
    const transport=heldDownloads(),gate=harness(transport.fetcher);
    const pending=[0,1,2,3].map(i=>gate.request(`/data/${i}.glb`));
    pending.push(gate.request('/data/next.glb'),gate.request('/data/city-index.json'),gate.request('/data/catalog.json'),gate.request('/data/tileset.json'));
    await flush();
    for(let i=0;i<8;i++){transport.finish(transport.records[i]);await flush();}
    await Promise.all(pending);
    expect(transport.records.slice(4).map(record=>record.path)).toEqual(['/data/catalog.json','/data/city-index.json','/data/tileset.json','/data/next.glb']);
  });
  it('removes cancelled queued requests and aborts an active body before giving its slot away',async()=>{
    const transport=heldDownloads(),gate=harness(transport.fetcher);
    const activeController=new AbortController(),queuedController=new AbortController();
    const active=gate.request('/data/active.glb',{signal:activeController.signal}).catch(error=>error);
    const other=[1,2,3].map(i=>gate.request(`/data/${i}.glb`));
    const queued=gate.request('/data/cancelled.glb',{signal:queuedController.signal}).catch(error=>error);
    const next=gate.request('/data/next.glb');
    await flush();queuedController.abort();expect((await queued).name).toBe('AbortError');
    activeController.abort();expect((await active).name).toBe('AbortError');await flush();
    expect(transport.records[0].signal.aborted).toBe(true);expect(transport.records[0].cancelled).toBe(1);
    expect(transport.records.map(record=>record.path)).not.toContain('/data/cancelled.glb');
    expect(transport.records[4].path).toBe('/data/next.glb');
    for(const record of transport.records.slice(1))transport.finish(record);
    await Promise.all([...other,next]);expect(gate.status()).toMatchObject({active:0,queued:0,cancelled:2,peak:4});
  });
  it('does not begin an already-aborted request',async()=>{
    const fetcher=vi.fn(),gate=harness(fetcher),controller=new AbortController();controller.abort();
    await expect(gate.request('/data/not-needed.glb',{signal:controller.signal})).rejects.toMatchObject({name:'AbortError'});
    expect(fetcher).not.toHaveBeenCalled();expect(gate.status().cancelled).toBe(1);
  });
  it('releases on fetch and body failures and passes original HTTP errors through',async()=>{
    const fetcher=vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(new Response(new ReadableStream({start(controller){controller.error(new Error('broken body'));}}))).mockResolvedValueOnce(new Response('missing',{status:404})).mockResolvedValueOnce(new Response('ok'));
    const gate=harness(fetcher);
    await expect(gate.request('/data/a.glb')).rejects.toThrow('offline');
    await expect(gate.request('/data/b.glb')).rejects.toThrow('broken body');
    const missing=await gate.request('/data/missing.glb');expect(missing.status).toBe(404);expect(await missing.text()).toBe('missing');
    expect(await (await gate.request('/data/ok.glb')).text()).toBe('ok');
    expect(gate.status()).toMatchObject({active:0,queued:0,failed:2,completed:2});
  });
  it('stops oversized decoded bodies during reading and releases the slot',async()=>{
    const cancelled=vi.fn();
    const fetcher=vi.fn().mockResolvedValueOnce(new Response(new ReadableStream({start(controller){controller.enqueue(new Uint8Array(24*1024*1024+1));},cancel:cancelled}))).mockResolvedValueOnce(new Response('ok'));
    const gate=harness(fetcher);
    await expect(gate.request('/data/oversize.glb')).rejects.toThrow('24 MiB');expect(cancelled).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledOnce();expect(gate.status()).toMatchObject({retries:0,bytes:0,lastFailure:{phase:'body',name:'RangeError'}});
    expect(await (await gate.request('/data/ok.glb')).text()).toBe('ok');expect(gate.status().active).toBe(0);
  });
  it('preserves HTTP cache/conditional/range semantics while correcting decoded body headers',async()=>{
    const fetcher=vi.fn(async request=>{
      expect(request.cache).toBe('no-cache');expect(request.headers.get('If-None-Match')).toBe('source-hash');
      return new Response('한글',{headers:{'Content-Type':'application/json','Content-Encoding':'br','Content-Length':'3','ETag':'source-hash','Cache-Control':'public, max-age=31536000, immutable'}});
    });
    const gate=harness(fetcher),response=await gate.request('/data/metadata.json',{cache:'no-cache',headers:{'If-None-Match':'source-hash'}});
    expect(response.headers.has('Content-Encoding')).toBe(false);expect(response.headers.get('Content-Length')).toBe('6');
    expect(response.headers.get('ETag')).toBe('source-hash');expect(response.headers.get('Cache-Control')).toContain('immutable');expect(await response.text()).toBe('한글');
    const conditional=harness(vi.fn(async()=>new Response(null,{status:304,headers:{ETag:'same'}})));
    const cached=await conditional.request('/data/catalog.json');expect(cached.status).toBe(304);expect(await cached.text()).toBe('');expect(cached.headers.get('ETag')).toBe('same');
    const head=harness(vi.fn(async()=>new Response(null,{headers:{'Content-Encoding':'gzip','Content-Length':'120'}})));
    const headed=await head.request('/data/file.glb',{method:'HEAD'});expect(headed.headers.has('Content-Length')).toBe(false);expect(headed.body).toBeNull();
    const range=harness(vi.fn(async request=>{expect(request.headers.get('Range')).toBe('bytes=0-2');return new Response('abc',{status:206,headers:{'Content-Range':'bytes 0-2/6'}});}));
    const partial=await range.request('/data/file.glb',{headers:{Range:'bytes=0-2'}});expect(partial.status).toBe(206);expect(partial.headers.get('Content-Range')).toBe('bytes 0-2/6');
  });
  it('times out hung active transfers without permanently consuming a slot',async()=>{
    vi.useFakeTimers();
    const fetcher=vi.fn((request,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true})));
    const gate=harness(fetcher),response=gate.request('/data/hung.glb').catch(error=>error);
    await vi.advanceTimersByTimeAsync(60001);expect((await response).name).toBe('TimeoutError');expect(gate.status()).toMatchObject({active:0,queued:0,failed:1});
  });
  it('bounds queue wait time when earlier transfers keep failing slowly',async()=>{
    vi.useFakeTimers();
    const fetcher=vi.fn((request,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true})));
    const gate=harness(fetcher),responses=Array.from({length:13},(_,i)=>gate.request(`/data/${i}.glb`).catch(error=>error));
    await vi.advanceTimersByTimeAsync(120001);await Promise.all(responses);
    expect(fetcher.mock.calls.length).toBeLessThan(13);expect(gate.status()).toMatchObject({active:0,queued:0,failed:13});
  });
  it('intercepts only same-origin data and uses normal activation without forced takeover or a cache store',async()=>{
    const fetcher=vi.fn(async()=>new Response('ok')),gate=harness(fetcher);
    for(const url of ['/api/v1/runtime','/assets/main.js','/download-gate.js','https://external.example/data/tile.glb'])expect(gate.request(url)).toBeUndefined();
    let activation;gate.handlers.get('activate')({waitUntil:value=>{activation=value;}});await activation;expect(gate.claim).toHaveBeenCalledOnce();
    expect(gate.handlers.has('install')).toBe(false);expect(fetcher).not.toHaveBeenCalled();
  });
  it('marks every gated network request, including no-cors images, without stripping conditional headers',async()=>{
    const marker='1234567890abcdef;test-worker-instance';
    const fetcher=vi.fn(async(request,options)=>{
      const outgoing=new Request(request,options);
      expect(outgoing.mode).toBe('same-origin');expect(outgoing.headers.get('X-Korea-Download-Gate')).toBe(marker);
      if(outgoing.url.endsWith('tile.glb'))expect(outgoing.headers.get('If-None-Match')).toBe('original');
      return new Response('body');
    });
    const gate=harness(fetcher);
    const image=await gate.request('/data/weather.png',{mode:'no-cors'});expect(image.headers.get('X-Korea-Download-Gate')).toBe(marker);
    await gate.request('/data/tile.glb',{headers:{'If-None-Match':'original'}});expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it.each(['GET','HEAD'])('recovers a transient %s fetch failure without counting it as a failed job or exposing its query/message',async method=>{
    vi.useFakeTimers();
    const fetcher=vi.fn().mockRejectedValueOnce(new TypeError('private transport details')).mockResolvedValueOnce(new Response(method==='HEAD'?null:'recovered'));
    const gate=harness(fetcher),pending=gate.request('/data/terrain/example.terrain?private=hidden',{method});
    await flush();expect(fetcher).toHaveBeenCalledOnce();expect(gate.status()).toMatchObject({active:1,retries:0,failed:0});
    await vi.advanceTimersByTimeAsync(99);expect(fetcher).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(1);
    expect(await (await pending).text()).toBe(method==='HEAD'?'':'recovered');
    const status=gate.status();
    expect(status).toMatchObject({active:0,completed:1,failed:0,retries:1,bytes:method==='HEAD'?0:9,
      lastFailure:{pathname:'/data/terrain/example.terrain',phase:'fetch',name:'TypeError'}});
    expect(Object.keys(status.lastFailure).sort()).toEqual(['name','pathname','phase']);
    expect(JSON.stringify(status)).not.toMatch(/private|hidden|transport details/);
  });
  it('stops after two retries with 100/300 ms backoff and one terminal failure',async()=>{
    vi.useFakeTimers();
    const fetcher=vi.fn().mockRejectedValue(new TypeError('network failed')),gate=harness(fetcher);
    const pending=gate.request('/data/terrain/failing.terrain').catch(error=>error);
    await flush();await vi.advanceTimersByTimeAsync(100);expect(fetcher).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(299);expect(fetcher).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);expect((await pending).name).toBe('TypeError');
    expect(gate.status()).toMatchObject({active:0,completed:0,failed:1,retries:2,bytes:0});
    await vi.advanceTimersByTimeAsync(120000);expect(fetcher).toHaveBeenCalledTimes(3);
  });
  it('awaits failed body cleanup before retry and counts only the final successful body',async()=>{
    vi.useFakeTimers();
    let cleaned;
    const cleanup=new Promise(resolve=>{cleaned=resolve;});
    const reader={read:vi.fn().mockResolvedValueOnce({done:false,value:new Uint8Array([1,2,3,4,5])}).mockRejectedValueOnce(new TypeError('stream disconnected')),
      cancel:vi.fn(()=>cleanup),releaseLock:vi.fn()};
    const response={headers:new Headers(),status:200,statusText:'OK',ok:true,redirected:false,body:{getReader:()=>reader}};
    const fetcher=vi.fn().mockResolvedValueOnce(response).mockResolvedValueOnce(new Response('ok')),gate=harness(fetcher);
    const pending=gate.request('/data/terrain/body.terrain');
    await flush();expect(reader.cancel).toHaveBeenCalledOnce();expect(reader.releaseLock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(300);expect(fetcher).toHaveBeenCalledOnce();expect(gate.status()).toMatchObject({active:1,bytes:0});
    cleaned();await flush();expect(reader.releaseLock).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(100);expect(await (await pending).text()).toBe('ok');
    expect(gate.status()).toMatchObject({active:0,retries:1,completed:1,failed:0,bytes:2,lastFailure:{phase:'body',name:'TypeError'}});
  });
  it('keeps all four slots through backoff and retried bodies, leaving the fifth job queued',async()=>{
    vi.useFakeTimers();
    const transport=heldDownloads(),attempts=new Map();
    const fetcher=vi.fn((request,options)=>{
      const pathname=new URL(request.url).pathname,count=(attempts.get(pathname)??0)+1;attempts.set(pathname,count);
      if(count===1&&pathname!=='/data/4.terrain')return Promise.reject(new TypeError('transient'));
      return transport.fetcher(request,options);
    });
    const gate=harness(fetcher),pending=Array.from({length:5},(_,i)=>gate.request(`/data/${i}.terrain`));
    await flush();expect(fetcher).toHaveBeenCalledTimes(4);expect(gate.status()).toMatchObject({active:4,queued:1,peak:4});
    await vi.advanceTimersByTimeAsync(99);expect(fetcher).toHaveBeenCalledTimes(4);
    await vi.advanceTimersByTimeAsync(1);expect(fetcher).toHaveBeenCalledTimes(8);expect(transport.records).toHaveLength(4);
    expect(attempts.has('/data/4.terrain')).toBe(false);expect(gate.status()).toMatchObject({active:4,queued:1,peak:4,retries:4});
    transport.finish(transport.records[0]);await flush();expect(transport.records[4].path).toBe('/data/4.terrain');
    for(const record of transport.records.slice(1))transport.finish(record);
    await Promise.all(pending);expect(gate.status()).toMatchObject({active:0,queued:0,peak:4,completed:5,retries:4,failed:0});
  });
  it('cancels promptly during backoff and never starts the retry',async()=>{
    vi.useFakeTimers();
    const fetcher=vi.fn().mockRejectedValue(new TypeError('transient')),gate=harness(fetcher),controller=new AbortController();
    const pending=gate.request('/data/cancel.terrain',{signal:controller.signal}).catch(error=>error);
    await flush();controller.abort();expect((await pending).name).toBe('AbortError');
    expect(gate.status()).toMatchObject({active:0,retries:0,failed:0,cancelled:1});
    await vi.advanceTimersByTimeAsync(1000);expect(fetcher).toHaveBeenCalledOnce();
  });
  it('retains an aborted body slot until its one cancellation finishes',async()=>{
    vi.useFakeTimers();
    let cleaned;
    const cleanup=new Promise(resolve=>{cleaned=resolve;}),cancel=vi.fn(()=>cleanup),transport=heldDownloads();
    const fetcher=vi.fn().mockResolvedValueOnce(new Response(new ReadableStream({cancel}))).mockImplementation(transport.fetcher);
    const gate=harness(fetcher),controller=new AbortController();
    const aborted=gate.request('/data/cancel-body.terrain',{signal:controller.signal}).catch(error=>error);
    const pending=Array.from({length:4},(_,i)=>gate.request(`/data/next-${i}.terrain`));
    await flush();controller.abort();await flush();expect(cancel).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledTimes(4);expect(gate.status()).toMatchObject({active:4,queued:1});
    cleaned();expect((await aborted).name).toBe('AbortError');await flush();expect(fetcher).toHaveBeenCalledTimes(5);
    for(const record of transport.records)transport.finish(record);
    await Promise.all(pending);expect(gate.status()).toMatchObject({active:0,retries:0,cancelled:1,failed:0});
  });
  it.each([404,302])('does not retry HTTP %s, even when reading its error body throws TypeError',async status=>{
    vi.useFakeTimers();
    const fetcher=vi.fn().mockResolvedValueOnce(new Response('status',{status})).mockResolvedValueOnce(new Response(new ReadableStream({start(controller){controller.error(new TypeError('broken HTTP error body'));}}),{status}));
    const gate=harness(fetcher);
    expect((await gate.request('/data/status.terrain')).status).toBe(status);
    await expect(gate.request('/data/broken-status.terrain')).rejects.toMatchObject({name:'TypeError'});
    expect(gate.status()).toMatchObject({active:0,retries:0,completed:1,failed:1});
    await vi.advanceTimersByTimeAsync(1000);expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it.each(['AbortError','TimeoutError'])('does not retry %s from fetch',async name=>{
    const fetcher=vi.fn().mockRejectedValue(new DOMException('request stopped',name)),gate=harness(fetcher);
    await expect(gate.request('/data/stopped.terrain')).rejects.toMatchObject({name});
    expect(fetcher).toHaveBeenCalledOnce();expect(gate.status()).toMatchObject({active:0,retries:0,failed:1});
  });
  it('does not retry non-idempotent methods or response construction errors',async()=>{
    const postFetcher=vi.fn().mockRejectedValue(new TypeError('failed')),post=harness(postFetcher);
    await expect(post.request('/data/file',{method:'POST'})).rejects.toMatchObject({name:'TypeError'});
    expect(postFetcher).toHaveBeenCalledOnce();expect(post.status().retries).toBe(0);
    const response={headers:new Headers(),status:101,statusText:'',body:null};
    const invalidFetcher=vi.fn().mockResolvedValue(response),invalid=harness(invalidFetcher);
    await expect(invalid.request('/data/invalid.terrain')).rejects.toThrow();
    expect(invalidFetcher).toHaveBeenCalledOnce();expect(invalid.status()).toMatchObject({retries:0,lastFailure:{phase:'response'}});
  });
  it('keeps one 60 second deadline through attempts and aborts backoff when it expires',async()=>{
    vi.useFakeTimers();
    const fetcher=vi.fn(()=>new Promise((resolve,reject)=>setTimeout(()=>reject(new TypeError('late disconnect')),59950)));
    const gate=harness(fetcher),pending=gate.request('/data/slow.terrain').catch(error=>error);
    await vi.advanceTimersByTimeAsync(59950);expect(gate.status()).toMatchObject({active:1,retries:0});
    await vi.advanceTimersByTimeAsync(50);expect((await pending).name).toBe('TimeoutError');
    expect(gate.status()).toMatchObject({active:0,retries:0,failed:1,lastFailure:{phase:'backoff',name:'TimeoutError'}});
    await vi.advanceTimersByTimeAsync(1000);expect(fetcher).toHaveBeenCalledOnce();
  });
});
