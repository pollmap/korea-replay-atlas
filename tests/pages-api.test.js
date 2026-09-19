import {afterEach,describe,expect,it,vi} from 'vitest';
import {mkdtemp,readFile,readdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createPagesApi,createPagesAssetSession,pagesAssetHash,workerBundle,verifyPagesRemote,uploadPagesBuckets} from '../scripts/pages-api.mjs';
import {verifyPagesStage} from '../scripts/pages-release.mjs';
vi.mock('../scripts/pages-release.mjs',async importOriginal=>{
  const actual=await importOriginal();return {...actual,verifyPagesStage:vi.fn(actual.verifyPagesStage)};
});
const credentials={accountId:'a'.repeat(32),token:'fixture-not-a-real-credential'};
const ok=result=>Response.json({success:true,result});
const remoteRoots=[],previewOrigin='https://1234abcd.korea-replay.pages.dev',publicOrigin='https://korea-replay.pages.dev';
const fixtureData={origin:'https://abcd1234.korea-replay-data.pages.dev',manifest_path:'/data/atlas/test/manifest.json',manifest_sha256:'b'.repeat(64)};
describe('interrupted Pages uploads',()=>{
  const buckets=['a','b','c','d'].map(key=>[{key}]);
  it('registers each accepted bucket before counting it and permits server-side reuse',async()=>{
    const retained=new Set(),events=[],progress=[];
    const assets={upload:async items=>{events.push('upload:'+items[0].key);},retain:async hashes=>{events.push('retain:'+hashes[0]);hashes.forEach(hash=>retained.add(hash));}};
    const uploaded=await uploadPagesBuckets({buckets,assets,loadItems:async entries=>entries,onProgress:value=>{expect(retained.size).toBeGreaterThanOrEqual(value.files);progress.push(value);}});
    expect(uploaded).toBe(4);expect(progress.at(-1).files).toBe(4);
    for(const key of retained)expect(events.indexOf('upload:'+key)).toBeLessThan(events.indexOf('retain:'+key));
    expect(buckets.flat().filter(entry=>!retained.has(entry.key))).toEqual([]);
  });
  it('settles the other accepted bucket and stops new work after a rejected upload',async()=>{
    let release;const waiting=new Promise(resolve=>{release=resolve;}),retained=[],started=[],progress=[];
    const assets={upload:async items=>{const key=items[0].key;started.push(key);if(key==='a')throw new Error('upload rejected');await waiting;},retain:async hashes=>{retained.push(...hashes);}};
    let settled=false;
    const result=uploadPagesBuckets({buckets,assets,loadItems:async entries=>entries,onProgress:value=>progress.push(value)}).then(()=>null,error=>error).finally(()=>{settled=true;});
    await new Promise(resolve=>setTimeout(resolve,0));expect(settled).toBe(false);release();
    expect((await result).message).toBe('upload rejected');expect(started).toEqual(['a','b']);expect(retained).toEqual(['b']);expect(progress.at(-1).files).toBe(1);
  });
  it('does not count an ambiguous registration as confirmed or retry it locally',async()=>{
    const progress=[],upload=vi.fn(async()=>{}),retain=vi.fn(async()=>{throw new Error('registration uncertain');});
    await expect(uploadPagesBuckets({buckets:buckets.slice(0,1),assets:{upload,retain},loadItems:async entries=>entries,onProgress:value=>progress.push(value)})).rejects.toThrow('registration uncertain');
    expect(progress).toEqual([]);expect(upload).toHaveBeenCalledTimes(1);expect(retain).toHaveBeenCalledTimes(1);
  });
});
afterEach(async()=>{
  vi.restoreAllMocks();
  vi.mocked(verifyPagesStage).mockReset();
  for(const root of remoteRoots.splice(0)){
    if(path.dirname(path.resolve(root))!==path.resolve(tmpdir())||!path.basename(root).startsWith('korea-pages-remote-'))throw new Error('Unsafe fixture cleanup');
    await rm(root,{recursive:true,force:true});
  }
});
function fixtureJwt(claims){return [Buffer.from('{"alg":"fixture"}').toString('base64url'),Buffer.from(JSON.stringify(claims)).toString('base64url'),'not-a-real-signature'].join('.');}
async function remoteFixture({snapshotOrigin=null,project='korea-replay'}={}){
  const directory=await mkdtemp(path.join(tmpdir(),'korea-pages-remote-'));remoteRoots.push(directory);
  const bodies=project==='korea-replay'?{'index.html':'fixture HTML','data/catalog.json':'{"release_id":"pub-test"}','assets/app.js':'export default 1;','assets/app.css':'body{color:black}'}:{'data/atlas/test/manifest.json':'{"schema_version":1}'};
  const entries=Object.entries(bodies).map(([target,body])=>({target,bytes:Buffer.byteLength(body),sha256:createHash('sha256').update(body).digest('hex')}));
  const receipt={project,artifact_sha256:'a'.repeat(64),release_id:'pub-test',...(project==='korea-replay'?{policy:{release_id:'pub-test',data:fixtureData,snapshot_origin:snapshotOrigin}}:{atlas_manifest:{path:'/data/atlas/test/manifest.json'}})};
  const checked={directory,client:path.join(directory,'client'),receipt,entries};
  vi.mocked(verifyPagesStage).mockResolvedValue(checked);
  return {...checked,bodies,receiptPath:path.join(directory,'receipt.json')};
}
function remoteResponses(fixture,origin,runtimeChanges={}){
  return vi.fn(async input=>{
    const url=new URL(input);expect(url.origin).toBe(origin);
    if(url.pathname==='/api/v2/runtime'){
      const snapshotOrigin=origin===publicOrigin?fixture.receipt.policy.snapshot_origin:origin;
      return Response.json({schema_version:2,platform:'cloudflare-pages',project:'korea-replay',release_id:'pub-test',artifact_sha256:fixture.receipt.artifact_sha256,
        snapshot:snapshotOrigin?{origin:snapshotOrigin,hash:new URL(snapshotOrigin).hostname.slice(0,8)}:null,data:fixtureData,...runtimeChanges});
    }
    if(url.pathname==='/data/__pages-verification-missing__.json')return new Response(null,{status:404});
    const body=fixture.bodies[url.pathname==='/'?'index.html':url.pathname.slice(1)];
    if(body===undefined)throw new Error('Unexpected fixture request');
    return new Response(body,{headers:fixture.receipt.project==='korea-replay-data'?{'Access-Control-Allow-Origin':'*'}:{}});
  });
}
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
  it('refreshes once before concurrent asset operations at the sixty-second expiry boundary',async()=>{
    let now=Date.UTC(2026,8,20),tokens=0;vi.spyOn(Date,'now').mockImplementation(()=>now);
    const first=fixtureJwt({exp:now/1000+3600,fixture:1}),second=fixtureJwt({exp:now/1000+7200,fixture:2}),used=[];
    const fetcher=vi.fn(async(url,init)=>{
      if(url.endsWith('/upload-token'))return ok({jwt:++tokens===1?first:second});
      used.push(init.headers.get('Authorization'));return ok(null);
    });
    const assets=createPagesAssetSession(createPagesApi({...credentials,fetcher}),'korea-replay');
    await assets.missing([]);now+=3539999;await assets.upload([]);expect(tokens).toBe(1);
    now+=1;await Promise.all([assets.upload([]),assets.missing([]),assets.retain([])]);
    expect(tokens).toBe(2);expect(used).toEqual([`Bearer ${first}`,`Bearer ${first}`,`Bearer ${second}`,`Bearer ${second}`,`Bearer ${second}`]);
  });
  it('reuses proactive renewal when a request using the older token fails later',async()=>{
    let now=Date.UTC(2026,8,20),tokens=0,release,entered;vi.spyOn(Date,'now').mockImplementation(()=>now);
    const first=fixtureJwt({exp:now/1000+3600}),second=fixtureJwt({exp:now/1000+7200});
    const held=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
    const fetcher=vi.fn(async(url,init)=>{
      if(url.endsWith('/upload-token'))return ok({jwt:++tokens===1?first:second});
      if(init.headers.get('Authorization')===`Bearer ${first}`){entered();await held;return new Response('fixture',{status:401});}
      return ok(null);
    });
    const assets=createPagesAssetSession(createPagesApi({...credentials,fetcher}),'korea-replay');
    const old=assets.upload([]);await started;now+=3540000;await assets.upload([]);release();await old;
    expect(tokens).toBe(2);
  });
  it.each([{}, {exp:'not-a-numeric-expiry'}])('preserves existing reactive behavior when expiry metadata is unavailable: %j',async claims=>{
    let now=Date.UTC(2026,8,20),tokens=0;vi.spyOn(Date,'now').mockImplementation(()=>now);
    const fetcher=vi.fn(async url=>url.endsWith('/upload-token')?(tokens++,ok({jwt:fixtureJwt(claims)})):ok(null));
    const assets=createPagesAssetSession(createPagesApi({...credentials,fetcher}),'korea-replay');
    await assets.upload([]);now+=24*60*60*1000;await assets.retain([]);expect(tokens).toBe(1);
  });
  it('rejects an already expired replacement without an asset call or a refresh loop',async()=>{
    const now=Date.UTC(2026,8,20);vi.spyOn(Date,'now').mockReturnValue(now);
    const fetcher=vi.fn(async()=>ok({jwt:fixtureJwt({exp:now/1000-1})}));
    const assets=createPagesAssetSession(createPagesApi({...credentials,fetcher}),'korea-replay');
    await expect(assets.upload([])).rejects.toThrow('already expired upload credential');expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('still refuses to refresh or retry a non-JSON HTTP 403 asset response',async()=>{
    let tokens=0,uploads=0;
    const fetcher=vi.fn(async url=>{
      if(url.endsWith('/upload-token')){tokens++;return ok({jwt:fixtureJwt({exp:Date.now()/1000+3600})});}
      uploads++;return new Response('<html>fixture forbidden</html>',{status:403});
    });
    const assets=createPagesAssetSession(createPagesApi({...credentials,fetcher}),'korea-replay');
    await expect(assets.upload([])).rejects.toThrow('Pages API invalid response (403)');expect(tokens).toBe(1);expect(uploads).toBe(1);
  });
});

describe('Pages remote origin and immutable sharing verification',()=>{
  it.each([null,'https://87654321.korea-replay.pages.dev'])('verifies an immutable host against itself with staged share policy %s',async snapshotOrigin=>{
    const fixture=await remoteFixture({snapshotOrigin}),fetcher=remoteResponses(fixture,previewOrigin);
    const report=await verifyPagesRemote({receiptPath:fixture.receiptPath,origin:previewOrigin,fetcher});
    expect(report).toMatchObject({passed:true,origin:previewOrigin,origin_kind:'immutable',snapshot_origin:previewOrigin});
    expect(await readdir(fixture.directory)).toEqual(['verified-preview-1234abcd.json']);
    expect(fetcher.mock.calls.some(([url])=>url===previewOrigin+'/')).toBe(true);
  });
  it('verifies the production host against its already verified candidate and writes a separate report',async()=>{
    const fixture=await remoteFixture({snapshotOrigin:previewOrigin}),fetcher=remoteResponses(fixture,publicOrigin);
    const report=await verifyPagesRemote({receiptPath:fixture.receiptPath,origin:publicOrigin,fetcher});
    expect(report).toMatchObject({passed:true,origin:publicOrigin,origin_kind:'production',snapshot_origin:previewOrigin});
    expect(JSON.parse(await readFile(path.join(fixture.directory,'verified-production.json'),'utf8'))).toEqual(report);
    expect(await readdir(fixture.directory)).toEqual(['verified-production.json']);
  });
  it('rejects an immutable host claiming another preview as its own share target',async()=>{
    const fixture=await remoteFixture({snapshotOrigin:'https://87654321.korea-replay.pages.dev'});
    await expect(verifyPagesRemote({receiptPath:fixture.receiptPath,origin:previewOrigin,fetcher:remoteResponses(fixture,previewOrigin,{snapshot:{origin:fixture.receipt.policy.snapshot_origin,hash:'87654321'}})})).rejects.toThrow('Remote runtime');
  });
  it('rejects production verification without a staged immutable share target before any HTTP request',async()=>{
    const fixture=await remoteFixture(),fetcher=vi.fn();
    await expect(verifyPagesRemote({receiptPath:fixture.receiptPath,origin:publicOrigin,fetcher})).rejects.toThrow('staged verified immutable share target');
    expect(fetcher).not.toHaveBeenCalled();expect(await readdir(fixture.directory)).toEqual([]);
  });
  it.each([
    null,
    {origin:'https://87654321.korea-replay.pages.dev',hash:'87654321'},
    {origin:previewOrigin,hash:'87654321'},
    {origin:publicOrigin,hash:'korea-re'},
  ])('rejects a production runtime with the wrong share pin: %j',async snapshot=>{
    const fixture=await remoteFixture({snapshotOrigin:previewOrigin});
    await expect(verifyPagesRemote({receiptPath:fixture.receiptPath,origin:publicOrigin,fetcher:remoteResponses(fixture,publicOrigin,{snapshot})})).rejects.toThrow('Remote runtime');
    expect(await readdir(fixture.directory)).toEqual([]);
  });
  it.each([{artifact_sha256:'c'.repeat(64)},{data:{...fixtureData,manifest_sha256:'c'.repeat(64)}}])('keeps the exact artifact and data pin checks on production: %j',async changes=>{
    const fixture=await remoteFixture({snapshotOrigin:previewOrigin});
    await expect(verifyPagesRemote({receiptPath:fixture.receiptPath,origin:publicOrigin,fetcher:remoteResponses(fixture,publicOrigin,changes)})).rejects.toThrow('Remote runtime');
  });
  it('does not admit branch aliases, other projects, HTTP, credentials or URL paths',async()=>{
    const fixture=await remoteFixture({snapshotOrigin:previewOrigin}),fetcher=vi.fn();
    for(const origin of ['https://main.korea-replay.pages.dev','https://korea-replay-data.pages.dev','http://korea-replay.pages.dev',publicOrigin+'/path','https://fixture@korea-replay.pages.dev']){
      await expect(verifyPagesRemote({receiptPath:fixture.receiptPath,origin,fetcher})).rejects.toThrow('origin is required');
    }
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('verifies the static data production site with hash, public CORS and 404 checks',async()=>{
    const fixture=await remoteFixture({project:'korea-replay-data'}),origin='https://korea-replay-data.pages.dev',fetcher=remoteResponses(fixture,origin);
    const report=await verifyPagesRemote({receiptPath:fixture.receiptPath,origin,fetcher});
    expect(report).toMatchObject({passed:true,origin_kind:'production',snapshot_origin:null});
    expect(fetcher.mock.calls.some(([url])=>url.includes('/api/'))).toBe(false);expect(report.samples).toHaveLength(2);
  });
});
