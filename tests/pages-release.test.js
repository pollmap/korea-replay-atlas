import {afterEach,describe,expect,it} from 'vitest';
import {mkdtemp,mkdir,readFile,writeFile,rm,stat,symlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {stagePagesApp,stagePagesData,verifyPagesStage,copyImmutable,pagesHeaders} from '../scripts/pages-release.mjs';
import {deployPagesStage,verifyPagesRemote} from '../scripts/pages-api.mjs';
const roots=[],sha=value=>createHash('sha256').update(value).digest('hex'),json=value=>JSON.stringify(value);
const release='pub-0123456789abcdef',data={origin:'https://1234abcd.korea-replay-data.pages.dev',manifest_path:'/data/atlas/atlas-fixture/manifest.json',manifest_sha256:'a'.repeat(64)};
async function put(file,value){await mkdir(path.dirname(file),{recursive:true});await writeFile(file,value);}
afterEach(async()=>{for(const root of roots.splice(0)){if(path.dirname(root)!==path.resolve(tmpdir())||!path.basename(root).startsWith('korea-pages-'))throw new Error('Unsafe fixture cleanup');await rm(root,{recursive:true,force:true});}});
async function fixture(headers='/download-gate.js\n  Cache-Control: no-cache\n'){
  const projectRoot=await mkdtemp(path.join(tmpdir(),'korea-pages-'));roots.push(projectRoot);
  const bundle=path.join(projectRoot,'.local/deploy/bundles/fixture'),client=path.join(bundle,'client'),receiptPath=path.join(bundle,'receipt.json');
  const bodies={'index.html':'<!doctype html><title>Public fixture</title>','404.html':'missing','_headers':headers,
    'data/catalog.json':json({release_id:release,assets:[]}), 'data/sample.geojson':json({type:'FeatureCollection',features:[]})};
  const manifest=[];for(const [target,body] of Object.entries(bodies)){await put(path.join(client,target),body);manifest.push({target,sha256:sha(body),bytes:Buffer.byteLength(body)});}
  const worker="export default {fetch(){return new Response('fixture')}};";await put(path.join(bundle,'worker/index.js'),worker);
  const config={name:'korea-replay',main:'./worker/index.js',no_bundle:true,compatibility_date:'2026-09-16',compatibility_flags:['nodejs_compat'],
    vars:{ENVIRONMENT:'production',DATA_STORAGE:'static',COLLECTORS_ENABLED:'false',STATIC_RELEASE_ID:release},
    assets:{directory:'./client',binding:'ASSETS',not_found_handling:'none',run_worker_first:['/api/*']},version_metadata:{binding:'CF_VERSION_METADATA'},workers_dev:true,preview_urls:true};
  await put(path.join(bundle,'wrangler.json'),json(config));await put(path.join(bundle,'asset-manifest.json'),json(manifest));
  await put(receiptPath,json({schema_version:1,mode:'static',complete:true,bundle,config:path.join(bundle,'wrangler.json'),config_hash:sha(json(config)),
    manifest_hash:sha(json(manifest)),catalog_hash:sha(bodies['data/catalog.json']),release_id:release,audit:{passed:true,release_id:release},count:manifest.length,
    worker_files:[{target:'worker/index.js',sha256:sha(worker)}]}));
  await put(path.join(projectRoot,'worker/pages-adapter.ts'),'export function createPagesAdapter(application:unknown, policy:unknown){return {application,policy};}');
  return {projectRoot,bundle,client,receiptPath,bodies};
}
async function dataFixture(root,kind){
  const id=kind==='map_catalog'?'map-fixture':'property-fixture',prefix=kind==='map_catalog'?'map-tiles':'property',base=path.join(root,'.local',id),target=`data/${prefix}/${id}/manifest.json`,body=json({schema_version:1,release_id:id});
  await put(path.join(base,target),body);const publication=path.join(base,'publication.json');
  await put(publication,json({schema_version:1,[kind]:{path:target,sha256:sha(body),release_id:id},files:[{path:target,sha256:sha(body),byte_length:Buffer.byteLength(body)}]}));return publication;
}
describe('independent Pages release stages',()=>{
  it('lets Pages negotiate raw GLB compression while preserving the immutable Worker bundle and other header policies',async()=>{
    const source='/data/*.glb\n  Content-Encoding: gzip\n  Vary: Accept-Encoding\n/data/already-compressed.gz\n  Content-Encoding: gzip\n/data/catalog.json\n  Cache-Control: no-cache\n';
    const f=await fixture(source),result=await stagePagesApp({...f,data}),checked=await verifyPagesStage(result.receiptPath,{projectRoot:f.projectRoot});
    expect(await readFile(path.join(f.client,'_headers'),'utf8')).toBe(source);
    const output=await readFile(path.join(checked.client,'_headers'),'utf8');
    expect(output).not.toContain('/data/*.glb\n  Content-Encoding: gzip');
    expect(output).toContain('/data/already-compressed.gz\n  Content-Encoding: gzip');
    expect(output).toContain('/data/catalog.json\n  Cache-Control: no-cache');
    expect((await stat(path.join(f.client,'_headers'))).ino).not.toBe((await stat(path.join(checked.client,'_headers'))).ino);
    expect(pagesHeaders(output)).toBe(output);
  });
  it('hardlinks audited immutable assets, privately copies the existing API and keeps the original bundle byte-identical',async()=>{
    const f=await fixture(),before=await readFile(f.receiptPath),result=await stagePagesApp({...f,data});
    const checked=await verifyPagesStage(result.receiptPath,{projectRoot:f.projectRoot});
    expect(checked.receipt.linked).toBe(6);expect(await readFile(f.receiptPath)).toEqual(before);
    expect((await stat(path.join(f.client,'index.html'))).ino).toBe((await stat(path.join(checked.client,'index.html'))).ino);
    expect(checked.configuration.services).toEqual([{binding:'KOREA_API',service:'korea-replay'}]);
    expect(JSON.parse(await readFile(path.join(checked.client,'_routes.json'),'utf8'))).toEqual({version:1,include:['/api/*'],exclude:[]});
    await expect(stagePagesApp({...f,data})).rejects.toThrow(/exist/i);
  });
  it('supports exclusive copy fallback without ever replacing an existing file',async()=>{
    const f=await fixture(),destination=path.join(f.projectRoot,'.local/copied.html');
    expect(await copyImmutable(path.join(f.client,'index.html'),destination,{copyOnly:true})).toBe('copied');
    await expect(copyImmutable(path.join(f.client,'index.html'),destination,{copyOnly:true})).rejects.toThrow();
    expect(await readFile(destination,'utf8')).toBe(f.bodies['index.html']);
  });
  it('allows only the share destination to differ between candidate and production',async()=>{
    const f=await fixture(),candidate=await stagePagesApp({...f,data});
    await expect(stagePagesApp({...f,data,snapshotOrigin:'https://abcd1234.korea-replay.pages.dev',candidateReceiptPath:candidate.receiptPath})).rejects.toThrow();
    await put(path.join(candidate.directory,'verified-preview-abcd1234.json'),json({passed:true,origin:'https://abcd1234.korea-replay.pages.dev',artifact_sha256:candidate.receipt.artifact_sha256,project:'korea-replay'}));
    const production=await stagePagesApp({...f,data,snapshotOrigin:'https://abcd1234.korea-replay.pages.dev',candidateReceiptPath:candidate.receiptPath});
    expect(production.receipt.artifact_sha256).toBe(candidate.receipt.artifact_sha256);
    await expect(stagePagesApp({...f,data:{...data,manifest_sha256:'b'.repeat(64)},snapshotOrigin:'https://abcd1234.korea-replay.pages.dev',candidateReceiptPath:candidate.receiptPath})).rejects.toThrow('differ');
    await expect(stagePagesApp({...f,data,snapshotOrigin:'https://main.korea-replay.pages.dev',candidateReceiptPath:candidate.receiptPath})).rejects.toThrow('immutable');
  // Real esbuild + filesystem stages run several times here. Allow bounded I/O
  // latency so fixture cleanup cannot race still-running stages on slow disks.
  },30_000);
  it('rejects source mutation before linking and rejects later output additions',async()=>{
    const f=await fixture();await put(path.join(f.client,'data/sample.geojson'),'changed');await expect(stagePagesApp({...f,data})).rejects.toThrow();
    const g=await fixture(),staged=await stagePagesApp({...g,data});await put(path.join(staged.directory,'client','unexpected.json'),'{}');
    await expect(verifyPagesStage(staged.receiptPath,{projectRoot:g.projectRoot})).rejects.toThrow('additional');
  });
  it('rejects a modified receipt policy, paid binding, mutable data origin and path escape',async()=>{
    const f=await fixture();await expect(stagePagesApp({...f,data:{...data,origin:'https://korea-replay-data.pages.dev'}})).rejects.toThrow('immutable');
    await expect(stagePagesApp({...f,receiptPath:path.join(f.projectRoot,'receipt.json'),data})).rejects.toThrow('escapes');
    const staged=await stagePagesApp({...f,data}),receipt=JSON.parse(await readFile(staged.receiptPath,'utf8'));
    receipt.policy.release_id='pub-ffffffffffffffff';await put(staged.receiptPath,json(receipt));await expect(verifyPagesStage(staged.receiptPath,{projectRoot:f.projectRoot})).rejects.toThrow('policy');
    const configuration=JSON.parse(await readFile(path.join(staged.directory,'wrangler.json'),'utf8'));configuration.r2_buckets=[];await put(path.join(staged.directory,'wrangler.json'),json(configuration));
    await expect(verifyPagesStage(staged.receiptPath,{projectRoot:f.projectRoot})).rejects.toThrow('allowlist');
  });
  it('rejects a junction in the source tree',async()=>{
    const f=await fixture(),outside=path.join(f.projectRoot,'.local/outside');await mkdir(outside);await symlink(outside,path.join(f.client,'linked'),'junction');
    await expect(stagePagesApp({...f,data})).rejects.toThrow(/심볼릭|junction/);
  });
  it('creates an independent static CORS data site with both content-addressed release entries',async()=>{
    const f=await fixture(),mapPublication=await dataFixture(f.projectRoot,'map_catalog'),propertyPublication=await dataFixture(f.projectRoot,'property_release');
    const staged=await stagePagesData({projectRoot:f.projectRoot,mapPublication,propertyPublication}),checked=await verifyPagesStage(staged.receiptPath,{projectRoot:f.projectRoot});
    const manifest=JSON.parse(await readFile(path.join(checked.client,staged.receipt.atlas_manifest.path.slice(1)),'utf8'));
    expect(manifest.map_catalog.release_id).toBe('map-fixture');expect(manifest.property_release.release_id).toBe('property-fixture');
    expect(await readFile(path.join(checked.client,'_headers'),'utf8')).toContain('Access-Control-Allow-Origin: *');expect(checked.configuration.services).toBeUndefined();
    expect(checked.entries.some(entry=>entry.target.startsWith('_worker.js'))).toBe(false);
  });
  it('refuses an undeclared publication file or a different entry release',async()=>{
    const f=await fixture(),mapPublication=await dataFixture(f.projectRoot,'map_catalog'),propertyPublication=await dataFixture(f.projectRoot,'property_release');
    await put(path.join(path.dirname(mapPublication),'data/extra.json'),'{}');
    await expect(stagePagesData({projectRoot:f.projectRoot,mapPublication,propertyPublication})).rejects.toThrow('closure');
  });
  it.each(['asset-size','archive-size','count'])('rejects the free data-publication limit before upload: %s',async type=>{
    const f=await fixture(),mapPublication=await dataFixture(f.projectRoot,'map_catalog'),propertyPublication=await dataFixture(f.projectRoot,'property_release'),publication=JSON.parse(await readFile(mapPublication,'utf8'));
    if(type==='count')publication.files=Array.from({length:20001},(_,i)=>({...publication.files[0],path:`data/map-tiles/file-${i}.json`}));
    else if(type==='archive-size')publication.files[0]={...publication.files[0],path:'data/map-tiles/oversize.pmtiles',byte_length:1024*1024+1};
    else publication.files[0].byte_length=25*1024*1024+1;
    await put(mapPublication,json(publication));await expect(stagePagesData({projectRoot:f.projectRoot,mapPublication,propertyPublication})).rejects.toThrow(/limit|1 MiB/);
  });
  it('uploads a preview through the bounded REST protocol with Worker files excluded from public assets',async()=>{
    const f=await fixture(),staged=await stagePagesApp({...f,data}),origin='https://1234abcd.korea-replay.pages.dev';let posted=0;
    const api={inspect:async()=>({name:'korea-replay',subdomain:'korea-replay.pages.dev',production_branch:'main'}),uploadToken:async()=>'fixture-upload-token',
      missing:async hashes=>hashes,upload:async items=>{for(const item of items){expect(item.key).toMatch(/^[a-f0-9]{32}$/);expect(item.base64).toBe(true);}},retain:async()=>{},
      deploy:async(project,form)=>{posted++;expect(project).toBe('korea-replay');const manifest=JSON.parse(form.get('manifest'));expect(Object.keys(manifest).some(name=>name.includes('_worker.js')||name==='/_headers')).toBe(false);
        expect(form.get('branch')).toMatch(/^candidate-/);const worker=await new Response(form.get('_worker.bundle')).formData();expect(JSON.parse(worker.get('metadata')).bindings).toEqual([{type:'service',name:'KOREA_API',service:'korea-replay'}]);
        return {id:'1234abcd-0000-0000-0000-000000000000',url:origin,environment:'preview'};}};
    const result=await deployPagesStage({receiptPath:staged.receiptPath,projectRoot:f.projectRoot,api});expect(result.url).toBe(origin);expect(posted).toBe(1);
    const fetcher=async input=>{
      const pathname=new URL(input).pathname;
      if(pathname==='/api/v2/runtime')return Response.json({schema_version:2,platform:'cloudflare-pages',project:'korea-replay',release_id:release,artifact_sha256:staged.receipt.artifact_sha256,snapshot:{origin,hash:'1234abcd'},data});
      if(pathname.includes('__pages-verification-missing__'))return new Response(null,{status:404});
      if(pathname==='/index.html')return new Response(null,{status:308,headers:{Location:'/'}});
      return new Response(await readFile(path.join(staged.directory,'client',pathname==='/'?'index.html':pathname.slice(1))));
    };
    expect((await verifyPagesRemote({receiptPath:staged.receiptPath,projectRoot:f.projectRoot,origin,fetcher})).passed).toBe(true);
    expect((await stagePagesApp({...f,data,snapshotOrigin:origin,candidateReceiptPath:staged.receiptPath})).receipt.artifact_sha256).toBe(staged.receipt.artifact_sha256);
  });
  it('does not accept a forged remote artifact or a successful SPA response for missing data',async()=>{
    const f=await fixture(),staged=await stagePagesApp({...f,data});
    await expect(verifyPagesRemote({receiptPath:staged.receiptPath,projectRoot:f.projectRoot,origin:'https://1234abcd.korea-replay.pages.dev',fetcher:async()=>Response.json({schema_version:2,artifact_sha256:'0'.repeat(64)})})).rejects.toThrow('Remote runtime');
  });
});
