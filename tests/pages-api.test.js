import {describe,expect,it,vi} from 'vitest';
import {createPagesApi,createPagesAssetSession,pagesAssetHash,workerBundle} from '../scripts/pages-api.mjs';
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
  it('refreshes one shared token for concurrent unauthorized asset buckets',async()=>{
    let tokens=0,uploads=0;
    const fetcher=vi.fn(async(url,init)=>{
      if(url.endsWith('/upload-token'))return ok({jwt:++tokens===1?'fixture-old-token':'fixture-renewed-token'});
      expect(url.endsWith('/pages/assets/upload')).toBe(true);uploads++;
      if(init.headers.get('Authorization')==='Bearer fixture-old-token')return Response.json({success:false,errors:[{code:8000013,message:'never expose fixture token'}]},{status:403});
      return ok(null);
    });
    const assets=createPagesAssetSession(createPagesApi({...credentials,fetcher}),'korea-replay');
    await Promise.all([assets.upload([{key:'one'}]),assets.upload([{key:'two'}])]);
    expect(tokens).toBe(2);expect(uploads).toBe(4);
  });
  it('reuses the replacement when an old-token failure arrives late',async()=>{
    let tokens=0,lateFailure;
    const late=new Promise(resolve=>{lateFailure=resolve;});
    const fetcher=vi.fn(async(url,init)=>{
      if(url.endsWith('/upload-token'))return ok({jwt:'fixture-token-'+(++tokens)});
      if(init.headers.get('Authorization')==='Bearer fixture-token-1'){
        if(JSON.parse(init.body)[0].key==='late')await late;
        return new Response('not JSON',{status:401});
      }
      return ok(null);
    });
    const assets=createPagesAssetSession(createPagesApi({...credentials,fetcher}),'korea-replay');
    const fast=assets.upload([{key:'fast'}]),slow=assets.upload([{key:'late'}]);
    await fast;lateFailure();await slow;expect(tokens).toBe(2);
  });
  it.each(['missing','upload','retain'])('limits %s to one retry even after another unauthorized response',async operation=>{
    let tokens=0,calls=0;
    const fetcher=vi.fn(async url=>{
      if(url.endsWith('/upload-token'))return ok({jwt:'fixture-token-'+(++tokens)});
      calls++;return Response.json({success:false,errors:[{code:8000013}]},{status:401});
    });
    const assets=createPagesAssetSession(createPagesApi({...credentials,fetcher}),'korea-replay-data');
    await expect(assets[operation]([])).rejects.toThrow('401; codes 8000013');
    expect(tokens).toBe(2);expect(calls).toBe(2);
  });
  it.each([403,429,503])('does not refresh or retry a non-token HTTP %s asset error',async status=>{
    let tokens=0,calls=0;
    const fetcher=vi.fn(async url=>{
      if(url.endsWith('/upload-token')){tokens++;return ok({jwt:'fixture-token'});}
      calls++;return Response.json({success:false,errors:[{code:1000,message:credentials.token}]},{status});
    });
    const assets=createPagesAssetSession(createPagesApi({...credentials,fetcher}),'korea-replay');
    await expect(assets.upload([])).rejects.toThrow(`${status}; codes 1000`);
    expect(tokens).toBe(1);expect(calls).toBe(1);
  });
  it('never repeats a deployment POST when its response is unauthorized',async()=>{
    const fetcher=vi.fn(async()=>Response.json({success:false,errors:[{code:8000013}]},{status:401}));
    await expect(createPagesApi({...credentials,fetcher}).deploy('korea-replay',new FormData())).rejects.toThrow('401; codes 8000013');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
