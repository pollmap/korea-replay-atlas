import {expect,it,vi} from 'vitest';
const fixture=vi.hoisted(()=>({calls:[] as string[],siblingAborted:false}));
vi.mock('../src/atlas-client',()=>({fetchPinnedJson:vi.fn(async(ref:{url?:string;path?:string},_origin:string,signal:AbortSignal)=>{
  const name=ref.url??ref.path??'';fixture.calls.push(name);
  const prefix='/data/property-summary/summary-1111111111111111/';
  const reference=(url:string)=>({url,sha256:'a'.repeat(64),bytes:100});
  if(name.endsWith('/manifest.json'))return {schema_version:1,kind:'property-complex-summary-release',property_release_id:'property-8879dff1b31ac5f0',regions:[{lawd_code:'11710',...reference(prefix+'regions/11710.json')}]};
  if(name.includes('/regions/'))return {kind:'property-complex-summary-region',property_release_id:'property-8879dff1b31ac5f0',lawd_code:'11710',partitions:[],summaries:[0,1,2].map(n=>({...reference(prefix+`rows/11710/202609-00${n}.json`),deal_month:'202609'}))};
  if(name.endsWith('001.json'))return new Promise((_resolve,reject)=>{signal.addEventListener('abort',()=>{fixture.siblingAborted=true;reject(new DOMException('Aborted','AbortError'));},{once:true});});
  if(name.endsWith('000.json')){await Promise.resolve();throw Error('broken asset hash');}
  throw Error('The query must stop before fetching a third chunk.');
})}));
import {loadComplexPriceSummaries} from '../src/property-summary-client';
it('aborts the sibling download and stops queued work when a hash-checked chunk fails',async()=>{
  await expect(loadComplexPriceSummaries('property-8879dff1b31ac5f0','11710',['202609'],new AbortController().signal)).rejects.toThrow('broken asset hash');
  expect(fixture.siblingAborted).toBe(true);
  expect(fixture.calls.some(name=>name.endsWith('002.json'))).toBe(false);
});
