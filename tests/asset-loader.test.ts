import {describe,it,expect,vi} from 'vitest';
import {AssetLoadQueue,frameBatch,tilesetBudget,setTilesetScreenSpaceError} from '../shared/asset-loader';

function deferred<T>(){
  let resolve!:(value:T)=>void;
  const promise=new Promise<T>(done=>{resolve=done;});
  return {promise,resolve};
}
async function flush(){for(let i=0;i<12;i++)await Promise.resolve();}

describe('asset loading across effect generations',()=>{
  it('limits live work to four and does not start cancelled queued tasks',async()=>{
    const queue=new AssetLoadQueue<number>(4),effect=new AbortController();
    const gates=Array.from({length:9},()=>deferred<number>()),started:number[]=[];
    const jobs=gates.map((gate,i)=>queue.enqueue(`asset:${i}`,async()=>{started.push(i);return gate.promise;},effect.signal));
    await flush();expect(started).toEqual([0,1,2,3]);
    effect.abort();await flush();expect(started).toEqual([0,1,2,3]);
    for(let i=0;i<4;i++)gates[i].resolve(i);
    expect(await Promise.all(jobs)).toEqual([0,1,2,3,undefined,undefined,undefined,undefined,undefined]);
  });
  it('shares an existing non-cancelled ID/hash request',async()=>{
    const queue=new AssetLoadQueue<number>(4),effect=new AbortController(),gate=deferred<number>();
    const run=vi.fn(async()=>gate.promise);
    const a=queue.enqueue('id:hash',run,effect.signal),b=queue.enqueue('id:hash',run,effect.signal);
    expect(a).toBe(b);await flush();expect(run).toHaveBeenCalledTimes(1);
    gate.resolve(7);expect(await a).toBe(7);
  });
  it('waits for cancelled non-abortable work, disposes it, and then starts the new effect generation',async()=>{
    const queue=new AssetLoadQueue<string|undefined>(4),oldEffect=new AbortController(),nextEffect=new AbortController();
    const old=deferred<void>(),destroy=vi.fn(),commit=vi.fn();
    const first=queue.enqueue('id:hash',async signal=>{
      await old.promise;
      if(signal.aborted){destroy();return;}
      commit('old');return 'old';
    },oldEffect.signal);
    await flush();oldEffect.abort();
    const runNext=vi.fn(async()=>{commit('new');return 'new';});
    const second=queue.enqueue('id:hash',runNext,nextEffect.signal);
    await flush();expect(runNext).not.toHaveBeenCalled();
    old.resolve();await first;await flush();
    expect(destroy).toHaveBeenCalledTimes(1);expect(commit).toHaveBeenCalledExactlyOnceWith('new');
    expect(await second).toBe('new');
  });
  it('keeps old non-cancellable work within the same global concurrency budget',async()=>{
    const queue=new AssetLoadQueue<number>(1),oldEffect=new AbortController(),nextEffect=new AbortController();
    const gate=deferred<number>();
    const first=queue.enqueue('old-hash',async()=>gate.promise,oldEffect.signal);
    await flush();oldEffect.abort();
    const next=vi.fn(async()=>2),second=queue.enqueue('new-hash',next,nextEffect.signal);
    await flush();expect(next).not.toHaveBeenCalled();gate.resolve(1);
    await first;expect(await second).toBe(2);
  });
  it('survives StrictMode cleanup/re-setup without starting abandoned pending tasks',async()=>{
    const queue=new AssetLoadQueue<number>(4),firstEffect=new AbortController();
    const abandoned=vi.fn(async()=>1),first=queue.enqueue('id:hash',abandoned,firstEffect.signal);
    queue.cancelAll();firstEffect.abort();
    const secondEffect=new AbortController(),run=vi.fn(async()=>2);
    const second=queue.enqueue('id:hash',run,secondEffect.signal);
    expect(await first).toBeUndefined();expect(await second).toBe(2);expect(abandoned).not.toHaveBeenCalled();
  });
  it('frees a slot after an error without poisoning later work',async()=>{
    const queue=new AssetLoadQueue<number>(1),effect=new AbortController();
    const first=queue.enqueue('bad',async()=>{throw new Error('load failed');},effect.signal);
    const rejection=expect(first).rejects.toThrow('load failed');
    const next=queue.enqueue('good',async()=>8,effect.signal);
    await rejection;expect(await next).toBe(8);
  });
});

describe('shared cache budgets and progressive commits',()=>{
  it('preserves Cesium memory-adapted detail when the configured quality has not changed',()=>{
    let configured=40,adapted=65;
    const tileset={get maximumScreenSpaceError(){return configured;},set maximumScreenSpaceError(value:number){configured=value;adapted=value;}};
    setTilesetScreenSpaceError(tileset,40);
    expect(adapted).toBe(65);
    setTilesetScreenSpaceError(tileset,24);
    expect(configured).toBe(24);expect(adapted).toBe(24);
  });
  it.each([1,20,64,128,1000])('keeps %i active tilesets within the total budgets without a per-tileset floor',count=>{
    for(const lightweight of [false,true]){
      const budget=tilesetBudget(count,lightweight);
      expect(budget.cacheBytes*count).toBeLessThanOrEqual((lightweight?128:256)*1024*1024);
      expect(budget.maximumCacheOverflowBytes*count).toBeLessThanOrEqual((lightweight?64:128)*1024*1024);
    }
  });
  it('reassigns the same share to an existing tileset when the visible population expands',()=>{
    const existing={...tilesetBudget(1,false)};
    Object.assign(existing,tilesetBudget(20,false));
    expect(existing.cacheBytes*20).toBeLessThanOrEqual(256*1024*1024);
    expect(existing.maximumCacheOverflowBytes*20).toBeLessThanOrEqual(128*1024*1024);
  });
  it('notifies completed assets on the next frame while other work is pending',()=>{
    const callbacks=new Map<number,()=>void>();let id=0;
    const render=vi.fn(),request=vi.fn((cb:()=>void)=>{callbacks.set(++id,cb);return id;});
    const cancel=vi.fn((key:number)=>{callbacks.delete(key);});
    const batch=frameBatch(render,request,cancel);
    batch.notify();batch.notify();expect(request).toHaveBeenCalledTimes(1);
    callbacks.get(1)!();expect(render).toHaveBeenCalledTimes(1);
    batch.notify();expect(request).toHaveBeenCalledTimes(2);
    batch.cancel();expect(cancel).toHaveBeenCalledWith(2);expect(callbacks.has(2)).toBe(false);
  });
});
