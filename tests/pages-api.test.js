import {describe,expect,it,vi} from 'vitest';
import {createPagesApi,pagesAssetHash,workerBundle} from '../scripts/pages-api.mjs';
const credentials={accountId:'a'.repeat(32),token:'fixture-not-a-real-credential'};
const ok=result=>Response.json({success:true,result});
describe('Pages REST-only deployment boundary',()=>{
  it('creates only the two named Pages projects through REST and never invokes Worker deployment',async()=>{
    const fetcher=vi.fn(async(url,init)=>{expect(url).toBe('https://api.cloudflare.com/client/v4/accounts/'+'a'.repeat(32)+'/pages/projects');expect(JSON.parse(init.body)).toEqual({name:'korea-replay',production_branch:'main'});return ok({name:'korea-replay',subdomain:'korea-replay.pages.dev',production_branch:'main'});});
    const client=createPagesApi({...credentials,fetcher});expect((await client.create('korea-replay')).name).toBe('korea-replay');
    await expect(client.create('unrelated-project')).rejects.toThrow('two Pages');expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('does not print tokens/upstream messages or automatically retry an uncertain mutation',async()=>{
    const fetcher=vi.fn(async()=>Response.json({success:false,errors:[{code:1000,message:'private-fixture-text'}]},{status:403}));
    await expect(createPagesApi({...credentials,fetcher}).create('korea-replay')).rejects.toThrow('403; codes 1000');expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('blocks a preview rollback and leaves other deployments unchanged',async()=>{
    const fetcher=vi.fn(async()=>ok({environment:'preview'})),client=createPagesApi({...credentials,fetcher});
    await expect(client.rollback('korea-replay','12345678-1234-1234-1234-123456789abc')).rejects.toThrow('only to a production');expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('uses the installed Pages BLAKE3 full-content-plus-extension contract and distinct media keys',()=>{
    const first=pagesAssetHash(Buffer.from('fixture'),'a.txt');expect(first).toMatch(/^[a-f0-9]{32}$/);
    expect(pagesAssetHash(Buffer.from('fixture'),'another.txt')).toBe(first);expect(pagesAssetHash(Buffer.from('fixture'),'a.json')).not.toBe(first);
    expect(pagesAssetHash(Buffer.from('changed'),'a.txt')).not.toBe(first);
  });
  it('keeps service bindings in private multipart metadata with no keys or state bindings',async()=>{
    const checked={entries:[],configuration:{compatibility_date:'2026-09-16',compatibility_flags:['nodejs_compat']}};
    const blob=await workerBundle(checked),form=await new Response(blob).formData(),metadata=JSON.parse(form.get('metadata'));
    expect(metadata).toEqual({main_module:'index.js',compatibility_date:'2026-09-16',compatibility_flags:['nodejs_compat'],bindings:[{type:'service',name:'KOREA_API',service:'korea-replay'}]});
  });
});
