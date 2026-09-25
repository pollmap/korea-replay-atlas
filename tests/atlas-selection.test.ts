import {expect,it,vi} from 'vitest';
import {observeAtlasSelection,type AtlasSelection} from '../src/atlas-selection';

const current='property-1111111111111111',old='property-eea48453a819f517',unknown='property-2222222222222222';
const content={property:{release_id:current},verified:true};
type Content=typeof content;
function setup(initial=`https://korea-replay.pages.dev/#propertyRelease=${current}`,validateDeployment?:(url:URL)=>void){
  let href=initial,resolve!:(value:Content|null)=>void;
  const events=new EventTarget(),states:AtlasSelection<Content>[]=[],replace=vi.fn();
  const load=vi.fn<(signal:AbortSignal)=>Promise<Content|null>>(()=>new Promise<Content|null>(done=>{resolve=done;}));
  const stop=observeAtlasSelection({load,readUrl:()=>href,replaceUrl:replace,events,publish:state=>states.push(state),validateDeployment});
  const navigate=(url:string,event='hashchange')=>{href=url;events.dispatchEvent(new Event(event));};
  return {load,states,replace,stop,navigate,resolve:(value:Content|null)=>resolve(value)};
}
const settle=()=>new Promise<void>(resolve=>queueMicrotask(resolve));

it('revalidates same-tab hash changes and restores a known archive with current region state',async()=>{
  const app=setup();app.resolve(content);await settle();expect(app.states.at(-1)?.state).toBe('ready');
  const hash=`#regionCode=11410&month=202607&propertyRelease=${old}&complex=molit-apt%3A11410%3Atest`;
  app.navigate(`https://korea-replay.pages.dev/${hash}`);
  expect(app.states.at(-1)).toEqual({state:'loading',content:null,error:''});
  expect(new URL(app.replace.mock.calls[0][0]).hash).toBe(hash);
  expect(new URL(app.replace.mock.calls[0][0]).origin).toBe('https://ba3762eb.korea-replay.pages.dev');
  app.navigate(`https://korea-replay.pages.dev/${hash}`,'popstate');
  expect(app.replace).toHaveBeenCalledTimes(1);expect(app.load).toHaveBeenCalledTimes(1);app.stop();
});
it('hides current data for an unknown pin and restores the cached atlas on back/forward or pin removal',async()=>{
  const app=setup();app.resolve(content);await settle();
  app.navigate(`https://korea-replay.pages.dev/#propertyRelease=${unknown}`,'popstate');
  expect(app.states.at(-1)).toMatchObject({state:'error',content:null});expect(app.replace).not.toHaveBeenCalled();
  app.navigate(`https://korea-replay.pages.dev/#propertyRelease=${current}`,'popstate');
  expect(app.states.at(-1)?.content).toBe(content);
  app.navigate(`https://korea-replay.pages.dev/#propertyRelease=${unknown}`);
  app.navigate('https://korea-replay.pages.dev/#regionCode=11710');
  expect(app.states.at(-1)?.content).toBe(content);expect(app.load).toHaveBeenCalledTimes(1);app.stop();
});
it('ignores region/month changes for atlas state and performs no new loading',async()=>{
  const app=setup();app.resolve(content);await settle();const count=app.states.length;
  app.navigate(`https://korea-replay.pages.dev/#propertyRelease=${current}&regionCode=11710&month=202607`);
  app.navigate(`https://korea-replay.pages.dev/#month=202608&propertyRelease=${current}&regionCode=11410`,'popstate');
  expect(app.states).toHaveLength(count);expect(app.load).toHaveBeenCalledTimes(1);app.stop();
});
it('uses the latest URL when loading finishes instead of publishing a stale captured selection',async()=>{
  const app=setup();
  app.navigate(`https://korea-replay.pages.dev/#propertyRelease=${unknown}`);
  app.resolve(content);await settle();
  expect(app.states.some(state=>state.state==='ready')).toBe(false);
  expect(app.states.at(-1)).toMatchObject({state:'error',content:null});
  app.navigate(`https://korea-replay.pages.dev/#propertyRelease=${current}`);
  expect(app.states.at(-1)?.content).toBe(content);app.stop();
});
it('aborts stale loading and unsubscribes when the runtime effect is replaced',async()=>{
  const app=setup();const signal=app.load.mock.calls[0][0];
  app.stop();expect(signal.aborted).toBe(true);
  app.resolve(content);await settle();
  app.navigate(`https://korea-replay.pages.dev/#propertyRelease=${old}`);
  expect(app.states).toEqual([{state:'loading',content:null,error:''}]);expect(app.replace).not.toHaveBeenCalled();
});
it('does not bypass deployment pins or restore unknown/duplicate/empty pins to latest data',async()=>{
  const app=setup();app.resolve(content);await settle();
  for(const url of [
    `https://korea-replay.pages.dev/?deployment=#propertyRelease=${old}`,
    `https://korea-replay.pages.dev/#propertyRelease=${current}&propertyRelease=${unknown}`,
    'https://korea-replay.pages.dev/#propertyRelease=',
    `http://localhost:5173/#propertyRelease=${old}`,
  ]){app.navigate(url);expect(app.states.at(-1)).toMatchObject({state:'error',content:null});}
  expect(app.replace).not.toHaveBeenCalled();app.stop();
});
it('rechecks a changed deployment query even when the property release itself still matches',async()=>{
  const app=setup(undefined,url=>{if(url.searchParams.has('deployment'))throw new Error('deployment mismatch');});
  app.resolve(content);await settle();
  app.navigate(`https://korea-replay.pages.dev/?deployment=other#propertyRelease=${current}`,'popstate');
  expect(app.states.at(-1)).toEqual({state:'error',content:null,error:'deployment mismatch'});
  app.navigate(`https://korea-replay.pages.dev/#propertyRelease=${current}`,'popstate');
  expect(app.states.at(-1)?.content).toBe(content);expect(app.load).toHaveBeenCalledTimes(1);app.stop();
});
