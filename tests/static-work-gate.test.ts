import {describe,expect,it} from 'vitest';
import {FrameWorkBudget} from '../shared/map-performance';
import {GatedFrameWorkBudget,StaticWorkGate} from '../src/static-work-gate';

const tick=()=>new Promise<void>(resolve=>setImmediate(resolve));
const open=(gate:StaticWorkGate)=>{const revision=gate.reserveView();expect(gate.commitView(revision)).toBe(true);return revision;};

describe('static geometry work admission',()=>{
  it('resumes only after the newest settled wanted set commits and removes cancelled waiters',async()=>{
    const gate=new StaticWorkGate();gate.startSession(false);const initial=open(gate);
    gate.beginMotion();
    const obsolete=new AbortController(),events:string[]=[];
    const cancelled=gate.wait(obsolete.signal).then(()=>events.push('obsolete'),error=>error.name);
    const retained=gate.wait().then(()=>events.push('retained'));
    expect(gate.commitView(initial)).toBe(false);
    gate.endMotion();const previousView=gate.reserveView();
    // React's previous update can arrive after another flight has already begun.
    gate.beginMotion();expect(gate.commitView(previousView)).toBe(false);
    gate.endMotion();const latestView=gate.reserveView();
    obsolete.abort();events.push('wanted assigned; obsolete aborted');
    await tick();expect(events).toEqual(['wanted assigned; obsolete aborted']);
    expect(gate.snapshot().waiting).toBe(1);
    expect(gate.commitView(previousView)).toBe(false);
    expect(gate.commitView(latestView)).toBe(true);
    await retained;expect(await cancelled).toBe('AbortError');
    expect(events).toEqual(['wanted assigned; obsolete aborted','retained']);
    expect(gate.snapshot()).toMatchObject({blocked:false,waiting:0});gate.dispose();
  });

  it('rechecks movement after a shared frame yield, keeps live work running, and excludes paused wall time',async()=>{
    let now=0;const frames:(()=>void)[]=[],operations:string[]=[];
    const shared=new FrameWorkBudget(()=>now,()=>new Promise<void>(resolve=>frames.push(resolve)),4);
    const gate=new StaticWorkGate();gate.startSession(false);open(gate);
    const geometry=new GatedFrameWorkBudget(shared,gate),finish=geometry.startTask();
    await geometry.run('geometry-first',()=>{operations.push('first');now+=5;});
    const continuation=geometry.run('geometry-next',()=>{operations.push('next');now+=1;return 'source-id';});
    await tick();expect(frames).toHaveLength(1);
    gate.beginMotion();now+=1;frames.shift()!();await tick();
    expect(operations).toEqual(['first']);
    // Only geometry uses the adapter. Replay/live work retains the same base budget.
    await shared.run('live-update',()=>{operations.push('live');now+=1;});
    expect(operations).toEqual(['first','live']);
    now+=60000;gate.endMotion();const latest=gate.reserveView();await tick();
    expect(operations).toEqual(['first','live']);
    gate.commitView(latest);expect(await continuation).toBe('source-id');finish();
    expect(operations).toEqual(['first','live','next']);
    expect(shared.snapshot().chunkMaxMs).toBeLessThan(10);
    expect(geometry.snapshot()).toEqual(shared.snapshot());gate.dispose();
  });

  it('stays paused while hidden, disposes without rAF, and reopens a fresh StrictMode session',async()=>{
    const gate=new StaticWorkGate();gate.startSession(true);open(gate);
    const events:string[]=[];
    const old=gate.wait().then(()=>events.push('old-session'),error=>error.name);
    const controller=new AbortController();
    const aborted=gate.wait(controller.signal).then(()=>events.push('aborted'),error=>error.name);
    controller.abort();await tick();expect(gate.snapshot().waiting).toBe(1);
    gate.dispose();expect(await old).toBe('AbortError');expect(await aborted).toBe('AbortError');
    expect(gate.snapshot().waiting).toBe(0);
    gate.startSession(true);const next=open(gate);
    const current=gate.wait().then(()=>events.push('new-session'));
    await tick();expect(events).toEqual([]);expect(gate.snapshot().blocked).toBe(true);
    gate.setHidden(false);await current;
    expect(events).toEqual(['new-session']);
    expect(gate.snapshot()).toMatchObject({blocked:false,waiting:0,revision:next});
    gate.dispose();expect(gate.snapshot().waiting).toBe(0);
  });

  it('does not resume the CPU clock on visibility changes while preparation still waits',async()=>{
    let now=0,release!:()=>void;
    const shared=new FrameWorkBudget(()=>now,async()=>{now+=16;});
    const gate=new StaticWorkGate();gate.startSession(false);open(gate);
    const geometry=new GatedFrameWorkBudget(shared,gate),finish=geometry.startTask();
    await geometry.run('before-preparation',()=>{now+=2;});
    const waiting=finish.waitFor(new Promise<void>(resolve=>{release=resolve;}));
    now+=1000;gate.setHidden(true);now+=1000;gate.setHidden(false);
    // Becoming visible must not start an idle task's CPU interval.
    now+=1000;release();await waiting;
    await geometry.run('after-preparation',()=>{now+=3;});finish();
    expect(shared.snapshot()).toMatchObject({chunkMaxMs:3,operationMaxMs:3,chunkCount:2});
    gate.dispose();
  });

  it('finishes a disposed preparation waiter without restarting its CPU segment',async()=>{
    let now=0,rejectWait!:(reason:Error)=>void;
    const shared=new FrameWorkBudget(()=>now,async()=>{now+=16;});
    const gate=new StaticWorkGate();gate.startSession(false);open(gate);
    const geometry=new GatedFrameWorkBudget(shared,gate),finish=geometry.startTask();
    await geometry.run('before-disposal',()=>{now+=2;});
    const waiting=finish.waitFor(new Promise<void>((_resolve,reject)=>{rejectWait=reject;}));
    const stopped=expect(waiting).rejects.toMatchObject({name:'AbortError'});
    now+=1000;gate.dispose();finish();rejectWait(new DOMException('Aborted','AbortError'));await stopped;
    now+=1000;gate.startSession(false);open(gate);const next=geometry.startTask();
    await geometry.run('new-session',()=>{now+=1;});next();
    expect(shared.snapshot()).toMatchObject({chunkMaxMs:2,chunkCount:2});gate.dispose();
  });
});
