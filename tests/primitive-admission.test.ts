import {describe,expect,it} from 'vitest';
import {PrimitiveAdmission} from '../shared/primitive-admission';
const signal=()=>new AbortController().signal;
const primitive=()=>({ready:false,isDestroyed:()=>false});

describe('scene-wide Cesium preparation admission',()=>{
  it('keeps concurrent producers within the shared limit until actual readiness',async()=>{
    const queue=new PrimitiveAdmission(2),a=primitive(),b=primitive();
    (await queue.acquire(signal())).track(a);(await queue.acquire(signal())).track(b);
    let third=false;const next=queue.acquire(signal()).then(lease=>{third=true;return lease;});
    queue.poll();await Promise.resolve();expect(third).toBe(false);
    expect(queue.pending).toBe(2);expect(queue.queued).toBe(1);
    a.ready=true;queue.poll();const lease=await next;expect(third).toBe(true);
    expect(queue.pending).toBe(2);expect(queue.peakPending).toBe(2);
    lease.release();lease.release();expect(queue.pending).toBe(1);
    b.ready=true;queue.poll();expect(queue.pending).toBe(0);
  });
  it('cancels a queued camera request without consuming or leaking a slot',async()=>{
    const queue=new PrimitiveAdmission(1),held=await queue.acquire(signal()),abort=new AbortController();
    const cancelled=queue.acquire(abort.signal);const check=expect(cancelled).rejects.toMatchObject({name:'AbortError'});
    abort.abort();await check;expect(queue.queued).toBe(0);expect(queue.pending).toBe(1);
    held.release();expect(queue.pending).toBe(0);
  });
  it('releases aborted preparations and destroyed primitives without double admission',async()=>{
    const queue=new PrimitiveAdmission(1),abort=new AbortController(),old=await queue.acquire(abort.signal);
    old.track(primitive());const next=queue.acquire(signal());abort.abort();
    const active=await next;old.release();expect(queue.pending).toBe(1);
    active.track({ready:false,isDestroyed:()=>true});queue.poll();expect(queue.pending).toBe(0);
  });
  it('reserves slots before creation and aborts all waiters on scene teardown',async()=>{
    const queue=new PrimitiveAdmission(1),held=await queue.acquire(signal());
    queue.poll();expect(queue.pending).toBe(1);
    const waiting=queue.acquire(signal()),check=expect(waiting).rejects.toMatchObject({name:'AbortError'});
    queue.dispose();await check;held.release();queue.dispose();
    expect(queue.pending).toBe(0);expect(queue.queued).toBe(0);
    await expect(queue.acquire(signal())).rejects.toMatchObject({name:'AbortError'});
    expect(()=>held.track(primitive())).toThrow('Aborted');
  });
});
