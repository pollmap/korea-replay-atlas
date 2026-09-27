import {afterEach,expect,it,vi} from 'vitest';
import {createMap2DDiagnostics,writeMap2DDiagnostics} from '../src/map2d-diagnostics';

afterEach(()=>vi.useRealTimers());
const timing={now:()=>Date.now(),schedule:(callback:()=>void,delay:number)=>setTimeout(callback,delay),cancel:(timer:ReturnType<typeof setTimeout>)=>clearTimeout(timer)};

it('aggregates 120 tile-arrival reports into five publications and flushes the latest settled state',()=>{
  vi.useFakeTimers();vi.setSystemTime(0);
  let frames=0;const received:number[]=[];
  const diagnostics=createMap2DDiagnostics(()=>received.push(frames),250,timing);
  for(let i=0;i<120;i++){frames++;diagnostics.request();vi.advanceTimersByTime(8);}
  expect(received).toHaveLength(4);
  diagnostics.flush();expect(received).toHaveLength(5);expect(received.at(-1)).toBe(120);
  vi.advanceTimersByTime(1000);expect(received).toHaveLength(5);
  diagnostics.dispose();
});
it('trailing publication reads current counters instead of a snapshot captured at scheduling time',()=>{
  vi.useFakeTimers();vi.setSystemTime(0);
  let value='loading';const published:string[]=[];
  const diagnostics=createMap2DDiagnostics(()=>published.push(value),250,timing);
  diagnostics.request();vi.advanceTimersByTime(25);diagnostics.request();
  value='ready';vi.advanceTimersByTime(225);
  expect(published).toEqual(['loading','ready']);
  diagnostics.flushPending();diagnostics.flushPending();expect(published).toHaveLength(2);
  diagnostics.dispose();
});
it('flushes tile diagnostics at idle once without making ordinary hover/idle frames publish',()=>{
  vi.useFakeTimers();vi.setSystemTime(0);const publish=vi.fn();
  const diagnostics=createMap2DDiagnostics(publish,250,timing);
  diagnostics.request();vi.advanceTimersByTime(10);diagnostics.request();
  diagnostics.flushPending();expect(publish).toHaveBeenCalledTimes(2);
  for(let i=0;i<120;i++)diagnostics.flushPending();
  vi.advanceTimersByTime(1000);expect(publish).toHaveBeenCalledTimes(2);
  diagnostics.dispose();
});
it('cancels trailing work on map teardown and does not retain an interval while idle',()=>{
  vi.useFakeTimers();vi.setSystemTime(0);const publish=vi.fn();
  const diagnostics=createMap2DDiagnostics(publish,250,timing);
  diagnostics.request();expect(vi.getTimerCount()).toBe(0);
  vi.advanceTimersByTime(10);diagnostics.request();expect(vi.getTimerCount()).toBe(1);
  diagnostics.dispose();expect(vi.getTimerCount()).toBe(0);
  diagnostics.request();diagnostics.flush();diagnostics.flushPending();vi.advanceTimersByTime(1000);
  expect(publish).toHaveBeenCalledTimes(1);
});
it('writes only changed diagnostic attributes while retaining unrelated map state',()=>{
  let writes=0;
  const attributes:Record<string,string|undefined>={mapEngine:'maplibre',map2dFrames:'12',selectedRegionName:'서울특별시'};
  const observed=new Proxy(attributes,{set(target,key,value){writes++;target[String(key)]=value;return true;}});
  writeMap2DDiagnostics(observed,{mapEngine:'maplibre',map2dFrames:'12'});expect(writes).toBe(0);
  writeMap2DDiagnostics(observed,{mapEngine:'maplibre',map2dFrames:'13',map2dErrors:'0'});expect(writes).toBe(2);
  expect(attributes.selectedRegionName).toBe('서울특별시');
});
