import {expect,it,vi} from 'vitest';
// @ts-expect-error audited JS deployment utility
import {assertExpectedPagesRuntime,guardPagesProduction} from '../scripts/pages-runtime-guard.mjs';
const expected={artifact_sha256:'a'.repeat(64),release_id:'pub-test',data:{origin:'https://01234567.korea-replay-data.pages.dev',manifest_path:'/data/atlas/test/manifest.json',manifest_sha256:'b'.repeat(64)}};
const current={...expected,schema_version:2,project:'korea-replay',platform:'cloudflare-pages'};
it('accepts only the exact current app and data pin',async()=>{
 await expect(assertExpectedPagesRuntime(expected,{fetcher:async()=>new Response(JSON.stringify(current))})).resolves.toEqual(current);
});
it('fails closed on a concurrent collection, changed data, errors and oversized response',async()=>{
 for(const body of [{...current,artifact_sha256:'c'.repeat(64)},{...current,data:{...current.data,manifest_sha256:'d'.repeat(64)}},{...current,project:'other'}])await expect(assertExpectedPagesRuntime(expected,{fetcher:async()=>new Response(JSON.stringify(body))})).rejects.toThrow();
 await expect(assertExpectedPagesRuntime(expected,{fetcher:async()=>new Response('',{status:503})})).rejects.toThrow();
 await expect(assertExpectedPagesRuntime(expected,{fetcher:async()=>new Response(' '.repeat(65537))})).rejects.toThrow();
});
it('checks at the final production POST and does not retry an unknown outcome',async()=>{
 const deploy=vi.fn(async()=>{throw new Error('unknown outcome');}),fetcher=vi.fn(async()=>new Response(JSON.stringify(current)));
 const api=guardPagesProduction({deploy},expected,{fetcher}),form=new FormData();form.set('branch','main');
 await expect(api.deploy('korea-replay',form)).rejects.toThrow('unknown outcome');expect(fetcher).toHaveBeenCalledTimes(1);expect(deploy).toHaveBeenCalledTimes(1);
 const conflict=guardPagesProduction({deploy},expected,{fetcher:async()=>new Response(JSON.stringify({...current,artifact_sha256:'c'.repeat(64)}))});
 await expect(conflict.deploy('korea-replay',form)).rejects.toThrow('changed');expect(deploy).toHaveBeenCalledTimes(1);
});
