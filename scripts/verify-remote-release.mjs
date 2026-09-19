import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
import process from 'node:process';
import {pathToFileURL} from 'node:url';

const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const assert=(condition,message)=>{if(!condition)throw new Error(message);};
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const UUID=/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/;
const HOST=/^(?:([a-f0-9]{8})-)?korea-replay\.([a-z0-9-]+)\.workers\.dev$/;

function bundleFile(root,relative){
  assert(typeof relative==='string'&&!relative.includes('\\')&&!relative.split('/').some(p=>!p||p==='.'||p==='..'),'Unsafe staged file path');
  const file=path.resolve(root,relative);
  assert(file.startsWith(path.resolve(root)+path.sep),'Staged file escapes the bundle');return file;
}

/** Read receipts and the staged Worker, without a full-country hash scan.
 * Search files are opened only when a local reference query needs them. */
export async function loadVerificationInput(receiptPath){
  const receipt=JSON.parse(await readFile(receiptPath,'utf8'));
  assert(receipt.complete&&receipt.mode==='static'&&receipt.audit?.passed&&receipt.audit.release_id===receipt.release_id,'Staging was not completed');
  const manifestBytes=await readFile(path.join(receipt.bundle,'asset-manifest.json'));
  assert(sha(manifestBytes)===receipt.manifest_hash,'Staged manifest changed');
  const manifest=JSON.parse(manifestBytes.toString('utf8'));
  assert(Array.isArray(manifest)&&manifest.length===receipt.count&&new Set(manifest.map(e=>e.target)).size===manifest.length,'Invalid staged file manifest');
  const entries=new Map(manifest.map(e=>['/'+e.target,e]));
  const client=path.join(receipt.bundle,'client');
  const readAsset=async pathname=>{
    const entry=entries.get(pathname);assert(entry,'File is absent from the staged manifest: '+pathname);
    const body=await readFile(bundleFile(client,pathname.slice(1)));
    assert(body.length===entry.bytes&&sha(body)===entry.sha256,'Staged file changed: '+pathname);return body;
  };
  const catalogBytes=await readAsset('/data/catalog.json');
  assert(sha(catalogBytes)===receipt.catalog_hash,'Staged catalog changed');
  const catalog=JSON.parse(catalogBytes.toString('utf8'));
  assert(catalog.schema_version===2&&catalog.release_id===receipt.release_id&&catalog.legacy_url===`/data/releases/${receipt.release_id}.v1.json`,'Invalid staged catalog');
  const legacy=JSON.parse((await readAsset(catalog.legacy_url)).toString('utf8'));
  const configPath=path.join(receipt.bundle,'wrangler.json');
  assert(sha(await readFile(configPath))===receipt.config_hash,'Staged configuration changed');
  assert(receipt.worker_files?.length,'Staged Worker is missing');
  for(const entry of receipt.worker_files){
    assert(entry.target.startsWith('worker/'),'Unexpected Worker file');
    assert(sha(await readFile(bundleFile(receipt.bundle,entry.target)))===entry.sha256,'Staged Worker changed');
  }
  assert(receipt.worker_files.some(e=>e.target==='worker/index.js'),'Worker entrypoint is not audited');
  const module=await import(pathToFileURL(path.join(receipt.bundle,'worker/index.js')).href);
  const env={DATA_STORAGE:'static',STATIC_RELEASE_ID:receipt.release_id,COLLECTORS_ENABLED:'false',ASSETS:{async fetch(request){
    const pathname=new URL(request.url).pathname;
    if(!entries.has(pathname))return new Response(null,{status:404});
    return new Response(await readAsset(pathname),{headers:{'Content-Type':pathname.endsWith('.json')?'application/json':'application/octet-stream'}});
  }}};
  const searchReference=async(query,release)=>{
    const params=new URLSearchParams({q:query});if(release!==null)params.set('release',release);
    const response=await module.default.fetch(new Request('https://staged.invalid/api/v1/search?'+params),env);
    assert(response.ok,'Staged search reference failed: HTTP '+response.status);return response.json();
  };
  return {receipt,manifest,catalog,legacy,searchReference};
}

/** All external reads are injected for small, offline regression fixtures. */
export async function verifyRelease({origin,version=null,receipt,manifest,catalog,legacy,searchReference,fetcher=fetch,log=message=>process.stdout.write(message+'\n')}){
  const url=new URL(origin),local=url.hostname==='127.0.0.1',host=url.hostname.match(HOST);
  assert(!url.username&&!url.password&&url.pathname==='/'&&!url.search&&!url.hash,'Expected an origin without credentials or a path');
  assert(local?url.protocol==='http:':url.protocol==='https:'&&!url.port&&host,'Expected this application origin');
  origin=url.origin;
  assert(local||UUID.test(version??''),'Expected a remote version UUID');
  assert(local||!host[1]||host[1]===version.slice(0,8),'Origin is pinned to a different Worker version');
  assert(typeof searchReference==='function','A staged search reference is required');
  const preview=local?null:`https://${version.slice(0,8)}-korea-replay.${host[2]}.workers.dev`;
  const entries=new Map(manifest.map(entry=>['/'+entry.target,entry])),checks=[];
  async function request(route,{base=origin,...init}={}){
    const start=performance.now(),target=new URL(route,base);
    assert(target.origin===base,'Request leaves the selected deployment');
    let response=await fetcher(target.href,{...init,redirect:'manual',signal:AbortSignal.timeout(60000)});
    // Static Assets may canonicalize /index.html to /. API and data requests
    // cannot redirect to a latest deployment or a generic fallback page.
    if([301,302,303,307,308].includes(response.status)){
      const location=response.headers.get('location');
      assert(target.pathname==='/index.html'&&location&&new URL(location,target).href===base+'/','Unexpected redirect: '+route);
      await response.body?.cancel();response=await fetcher(base+'/',{...init,redirect:'manual',signal:AbortSignal.timeout(60000)});
    }
    assert(![301,302,303,307,308].includes(response.status),'Unresolved redirect: '+route);
    if(response.url)assert(new URL(response.url).origin===base,'Response came from a different deployment');
    const body=new Uint8Array(await response.arrayBuffer());
    return {response,body,elapsed_ms:Math.round(performance.now()-start)};
  }
  async function check(label,run){
    try{const details=await run();checks.push({label,passed:true,...details});}
    catch(error){checks.push({label,passed:false,error:String(error)});}
    log(`${checks.at(-1).passed?'PASS':'FAIL'} ${label}`);
  }
  async function jsonRequest(route,init){
    const result=await request(route,init);
    assert(result.response.ok,`HTTP ${result.response.status}: ${new TextDecoder().decode(result.body).slice(0,180)}`);
    return {...result,value:JSON.parse(new TextDecoder().decode(result.body))};
  }
  function runtimeMatches(runtime){
    assert(runtime.release_id===receipt.release_id,'Wrong data release');
    if(!local){assert(runtime.version_id===version,'Wrong Worker version');assert(runtime.preview_origin===preview,'Wrong immutable preview origin');}
  }
  function stagedBytes(route,body){
    const expected=entries.get(route);assert(expected,'Asset absent from stage manifest');
    assert(body.length===expected.bytes&&sha(body)===expected.sha256,'Remote bytes differ from staged data: '+route);
  }
  await check('runtime and free static configuration',async()=>{
    const {value:runtime}=await jsonRequest('/api/v1/runtime');
    const {value:health}=await jsonRequest('/api/v1/health');
    runtimeMatches(runtime);runtimeMatches(health);
    assert(health.storage==='static-assets'&&!health.database&&!health.collectors,'Unexpected runtime storage or collector');
    if(!local)assert(health.environment==='production','Wrong runtime environment');
    return {runtime,health};
  });
  await check('catalog v2 hash, latest cache and v1 compatibility',async()=>{
    const {response,body,value}=await jsonRequest('/data/catalog.json');
    assert(sha(body)===receipt.catalog_hash&&value.schema_version===2&&value.release_id===receipt.release_id,'Catalog hash/schema mismatch');
    if(!local)assert(response.headers.get('cache-control')?.includes('no-cache'),'Latest catalog is not revalidated');
    const etag=response.headers.get('etag');if(!local)assert(etag,'Latest catalog ETag is missing');
    if(etag){const conditional=await request('/data/catalog.json',{headers:{'If-None-Match':etag}});assert(conditional.response.status===304&&conditional.body.length===0,'Conditional GET failed');}
    for(const pinned of [false,true]){
      const compatible=await jsonRequest('/api/v1/catalog'+(pinned?'?release='+receipt.release_id:''));
      assert(compatible.value.schema_version===1&&same(compatible.value,legacy),'v1 content changed');
      stagedBytes(catalog.legacy_url,compatible.body);
      if(!local)assert(compatible.response.headers.get('cache-control')?.includes(pinned?'immutable':'no-cache'),'v1 cache policy changed');
    }
    const pinned=await request(`/data/releases/${receipt.release_id}.json`);
    assert(pinned.response.ok&&sha(pinned.body)===receipt.catalog_hash,'Pinned catalog differs from the selected release');
    return {catalog_bytes:body.length,etag};
  });
  await check('immutable preview origin and pinned share target',async()=>{
    if(local)return {skipped:'Local runtime has no immutable Cloudflare preview'};
    const {value:runtime}=await jsonRequest('/api/v1/runtime',{base:preview});runtimeMatches(runtime);
    const share=new URL(preview);share.searchParams.set('deployment',version);share.hash=new URLSearchParams({release:receipt.release_id,place:'daejeon',time:'2026-09-16T03:00:00Z'}).toString();
    const page=await request(share.pathname+share.search,{base:preview});
    assert(page.response.ok,'Pinned share page is unavailable');stagedBytes('/index.html',page.body);
    const pinned=await request(`/data/releases/${receipt.release_id}.json`,{base:preview});
    assert(pinned.response.ok&&sha(pinned.body)===receipt.catalog_hash,'Preview does not serve the pinned data release');
    return {share_url:share.href,scope:'HTTP target, version metadata and pinned source; browser state restoration is checked separately'};
  });
  await check('missing assets and missing pinned releases',async()=>{
    const routes=['/data/missing-verification.glb','/data/releases/pub-0000000000000000.json',
      ...['catalog','coverage','replay','search'].map(endpoint=>`/api/v1/${endpoint}?release=pub-0000000000000000&q=서울`),
      '/api/v1/search?release=pub-0000000000000000','/api/v1/search?release=pub-0000000000000000&q=%20'];
    for(const route of routes)assert((await request(route)).response.status===404,`Missing resource did not return 404: ${route}`);
    assert((await request('/api/v1/search?release=latest')).response.status===400,'Empty search skipped invalid-release validation');
  });
  await check('exact latest and pinned spatial coverage compatibility',async()=>{
    const boxes=[[127.35,36.25,127.5,36.4],[126.9,37.5,127.05,37.62],[131.8,37.2,131.95,37.3]];
    for(const bbox of boxes){
      for(const pinned of [false,true]){
        const {value}=await jsonRequest('/api/v1/coverage?bbox='+bbox.join(',')+(pinned?'&release='+receipt.release_id:''));
        assert(value.release_id===receipt.release_id,'Coverage release mismatch');
        assert(same(value.layers.map(l=>l.id).sort(),legacy.layers.map(l=>l.id).sort()),'Coverage omitted or duplicated layers');
        for(const layer of value.layers){
          const count=legacy.assets.filter(a=>a.layer===layer.id&&a.bbox[0]<=bbox[2]&&a.bbox[2]>=bbox[0]&&a.bbox[1]<=bbox[3]&&a.bbox[3]>=bbox[1]).length;
          assert(layer.matching_assets===count&&layer.in_view===(layer.id==='sun'||count>0),`Coverage differs: ${layer.id}`);
        }
      }
    }
    return {boxes};
  });
  await check('latest and pinned replay compatibility',async()=>{
    const expected={release_id:receipt.release_id,assets:legacy.assets.filter(a=>a.format==='replay'||a.format==='imagery')};
    for(const suffix of ['', '?release='+receipt.release_id])assert(same((await jsonRequest('/api/v1/replay'+suffix)).value,expected),'Replay content differs from the staged release');
  });
  // Record initial/repeated requests without assuming a cold isolate or
  // interpreting network wall time as Cloudflare CPU time.
  for(const query of ['서울','청주','산업단지','Incheon','Dongcheon','zz-no-match-84941']){
    await check('search '+query,async()=>{
      const samples=[];let expected;
      for(const pinned of [false,true]){
        expected=await searchReference(query,pinned?receipt.release_id:null);
        for(let i=0;i<2;i++){
          const {value,elapsed_ms}=await jsonRequest('/api/v1/search?q='+encodeURIComponent(query)+(pinned?'&release='+receipt.release_id:''));
          assert(same(value,expected),`${pinned?'Pinned':'Latest'} search differs from staged results`);
          samples.push({pinned,wall_ms:elapsed_ms});
        }
      }
      if(preview&&preview!==origin){
        const {value}=await jsonRequest('/api/v1/search?q='+encodeURIComponent(query)+'&release='+receipt.release_id,{base:preview});
        assert(same(value,expected),'Share preview search differs from staged results');
      }
      return {samples,result_ids:expected.places.map(place=>place.id)};
    });
  }
  const samples=new Set(['/index.html','/download-gate.js','/data/catalog.json',catalog.legacy_url,...catalog.assets.map(a=>a.url)]);
  if(catalog.coverage_url)samples.add(catalog.coverage_url);
  for(const suffix of ['.glb','.terrain','.geojson','.png','.js','.wasm']){
    const candidates=manifest.filter(entry=>entry.target.endsWith(suffix));
    for(const ratio of [0,.25,.5,.75,1])if(candidates.length)samples.add('/'+candidates[Math.floor((candidates.length-1)*ratio)].target);
  }
  for(const ratio of [0,.5,1])if(catalog.indexes.length)samples.add(catalog.indexes[Math.floor((catalog.indexes.length-1)*ratio)].url);
  const remaining=[...samples];
  await Promise.all(Array.from({length:4},async()=>{
    while(remaining.length){const route=remaining.shift();await check('asset '+route,async()=>{
      const {response,body,elapsed_ms}=await request(route);assert(response.ok,`HTTP ${response.status}`);stagedBytes(route,body);
      if(!local&&route.startsWith('/data/')&&route!=='/data/catalog.json')assert(response.headers.get('cache-control')?.includes('immutable'),'Immutable cache missing');
      if(!local&&route==='/download-gate.js')assert(response.headers.get('cache-control')?.includes('no-cache'),'Download gate updates are not revalidated');
      if(!local&&route.endsWith('.terrain'))assert(response.headers.get('content-type')?.includes('application/vnd.quantized-mesh'),'Terrain content type mismatch');
      return {bytes:body.length,elapsed_ms,content_type:response.headers.get('content-type')};
    });}
  }));
  return {created_at:new Date().toISOString(),origin,version,release_id:receipt.release_id,bundle_id:receipt.bundle_id,
    catalog_hash:receipt.catalog_hash,manifest_hash:receipt.manifest_hash,config_hash:receipt.config_hash,verification_schema:2,
    passed:checks.every(c=>c.passed),scope:'Latest/pinned API and staged search equality, immutable share HTTP target, cache and sampled SHA-256; full dependency audit and browser restoration are separate',checks};
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  const argument=name=>process.argv.find(value=>value.startsWith(`--${name}=`))?.slice(name.length+3);
  try{
    const input=await loadVerificationInput(argument('receipt')??'.local/deploy/static-stage.json');
    const result=await verifyRelease({...input,origin:argument('url')??'http://127.0.0.1:4174',version:argument('version')??null});
    const output=argument('output')??`.local/deploy/verify-${result.version??'local'}.json`;
    await mkdir(path.dirname(output),{recursive:true});await writeFile(output,JSON.stringify(result,null,2)+'\n');
    process.stdout.write(JSON.stringify({passed:result.passed,checks:result.checks.length,output})+'\n');
    if(!result.passed)process.exitCode=1;
  }catch(error){process.stderr.write(String(error)+'\n');process.exitCode=1;}
}
