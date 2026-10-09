import {afterEach,describe,expect,it,vi} from 'vitest';
import {retainDetailAnchor} from '../src/property-detail-anchor';

function fixture(){
  let notify=()=>{};
  const observed:Element[]=[],disconnect=vi.fn();
  const tasks=new Map<number,FrameRequestCallback>();let next=0;
  vi.stubGlobal('ResizeObserver',class{
    constructor(callback:ResizeObserverCallback){notify=()=>callback([],this as unknown as ResizeObserver);}
    observe(element:Element){observed.push(element);}
    disconnect=disconnect;
  });
  vi.stubGlobal('requestAnimationFrame',(callback:FrameRequestCallback)=>{tasks.set(++next,callback);return next;});
  vi.stubGlobal('cancelAnimationFrame',(id:number)=>tasks.delete(id));
  let targetTop=900,attached=true;
  const body=Object.assign(new EventTarget(),{scrollTop:100,
    getBoundingClientRect:()=>({top:240}),
    contains:()=>attached,
    querySelectorAll:()=>[previous,target],
    scrollTo:vi.fn((options:ScrollToOptions)=>{body.scrollTop=options.top!;}),
  });
  const previous={} as HTMLElement;
  const target={getBoundingClientRect:()=>({top:targetTop-body.scrollTop+240})} as HTMLElement;
  return {body:body as unknown as HTMLElement,target,previous,observed,disconnect,
    grow:(height:number)=>{targetTop+=height;notify();},
    detach:()=>{attached=false;notify();},
    flush:()=>{const pending=[...tasks.values()];tasks.clear();for(const callback of pending)callback(0);},
    tasks};
}
afterEach(()=>vi.unstubAllGlobals());
describe('detail menu anchor',()=>{
  it('follows a late height change above the selected school without moving the page',()=>{
    const f=fixture(),stop=retainDetailAnchor(f.body,f.target);
    expect(f.body.scrollTop).toBe(900);
    expect(f.observed).toContain(f.previous);
    f.grow(80);f.flush();expect(f.body.scrollTop).toBe(980);
    stop();expect(f.disconnect).toHaveBeenCalledOnce();
  });
  it.each(['wheel','touchmove','pointerdown','keydown'])('leaves user position alone after %s',event=>{
    const f=fixture();retainDetailAnchor(f.body,f.target);
    f.body.dispatchEvent(new Event(event));f.body.scrollTop=640;
    f.grow(80);f.flush();expect(f.body.scrollTop).toBe(640);
    expect(f.disconnect).toHaveBeenCalledOnce();
  });
  it('cancels a queued resize on close or replacement',()=>{
    const f=fixture(),stop=retainDetailAnchor(f.body,f.target);
    f.grow(80);expect(f.tasks.size).toBe(1);stop();stop();f.flush();
    expect(f.body.scrollTop).toBe(900);expect(f.tasks.size).toBe(0);
    expect(f.disconnect).toHaveBeenCalledOnce();
  });
  it('releases detached content instead of scrolling a newly selected complex',()=>{
    const f=fixture();retainDetailAnchor(f.body,f.target);f.detach();f.flush();
    expect(f.disconnect).toHaveBeenCalledOnce();expect(f.body.scrollTop).toBe(900);
  });
  it('keeps trades at the beginning and coalesces repeated resize notifications',()=>{
    const f=fixture(),stop=retainDetailAnchor(f.body,f.target,true);
    expect(f.body.scrollTop).toBe(0);f.grow(80);f.grow(80);
    expect(f.tasks.size).toBe(1);f.flush();expect(f.body.scrollTop).toBe(0);stop();
  });
});

