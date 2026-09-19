/** Direct Pages REST only. Never invoke Wrangler: its agent delegation can deploy an existing Worker. */
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {verifyPagesStage} from './pages-release.mjs';
const require=createRequire(import.meta.url),wranglerRequire=createRequire(require.resolve('wrangler/package.json'));
const blake3=wranglerRequire('blake3-wasm');
const ORIGIN='https://api.cloudflare.com',PREFIX='/client/v4';
const PROJECTS=new Set(['korea-replay','korea-replay-data']);
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
export function pagesAssetHash(bytes,name){return blake3.hash(bytes.toString('base64')+path.extname(name).slice(1)).toString('hex').slice(0,32);}
function projectName(project){if(!PROJECTS.has(project))throw new Error('Only this application\'s two Pages projects are permitted');return project;}
function mime(name){return ({'.html':'text/html; charset=utf-8','.js':'application/javascript','.css':'text/css','.json':'application/json','.geojson':'application/geo+json','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.gif':'image/gif','.webp':'image/webp','.ico':'image/x-icon','.wasm':'application/wasm','.woff':'font/woff','.woff2':'font/woff2','.glb':'model/gltf-binary','.terrain':'application/vnd.quantized-mesh','.gz':'application/gzip'})[path.extname(name)]??'application/octet-stream';}
class PagesApiError extends Error {
  constructor(status,codes){super(`Pages API rejected request (${status}; codes ${codes.join(',')||'unknown'})`);this.status=status;this.codes=codes;}
}
function uploadTokenExpiresAt(token){
  // Unverified JWT metadata is only a refresh hint; it never grants authorization.
  if(typeof token!=='string'||token.length>32768)return null;
  const parts=token.split('.');if(parts.length!==3||!/^[A-Za-z0-9_-]+$/.test(parts[1]))return null;
  try{const exp=JSON.parse(Buffer.from(parts[1],'base64url').toString('utf8'))?.exp;
    return typeof exp==='number'&&exp>=0&&Number.isFinite(exp*1000)&&exp*1000<=Number.MAX_SAFE_INTEGER?exp*1000:null;
  }catch{return null;}
}
export function createPagesApi({accountId,token,fetcher=fetch}){
  if(!/^[a-f0-9]{32}$/i.test(accountId)||typeof token!=='string'||!token.trim()||/[\r\n]/.test(token))throw new Error('Pages credentials are missing or malformed');
  const account=`/accounts/${accountId}/pages/projects`;
  async function request(route,{method='GET',body,uploadToken}={}){
    // Even an upstream-supplied string cannot select a Worker, subscription or another account.
    if(![account,account+'/korea-replay',account+'/korea-replay-data'].some(allowed=>route===allowed)
      &&!new RegExp(`^${account}/(?:korea-replay|korea-replay-data)/(?:upload-token|deployments(?:/[a-f0-9-]+(?:/rollback)?)?)$`).test(route)
      &&!/^\/pages\/assets\/(?:check-missing|upload|upsert-hashes)$/.test(route))throw new Error('Non-Pages endpoint rejected');
    const headers=new Headers({Authorization:`Bearer ${uploadToken??token}`});
    if(body!==undefined&&!(body instanceof FormData)){headers.set('Content-Type','application/json');body=JSON.stringify(body);}
    let response;try{response=await fetcher(ORIGIN+PREFIX+route,{method,headers,body,redirect:'error',signal:AbortSignal.timeout(120000)});}catch{throw new Error('Pages API transport failed; no automatic deployment retry was made');}
    let payload;try{payload=await response.json();}catch{if(response.status===401)throw new PagesApiError(401,[]);throw new Error(`Pages API invalid response (${response.status})`);}
    if(!response.ok||payload?.success!==true){const codes=(payload?.errors??[]).map(error=>Number(error.code)).filter(Number.isFinite);throw new PagesApiError(response.status,codes);}
    return payload.result;
  }
  return {
    async inspect(project){projectName(project);const result=await request(account+'/'+project);return {name:result.name,subdomain:result.subdomain,production_branch:result.production_branch};},
    async create(project){projectName(project);const result=await request(account,{method:'POST',body:{name:project,production_branch:'main'}});return {name:result.name,subdomain:result.subdomain,production_branch:result.production_branch};},
    async uploadToken(project){projectName(project);const result=await request(`${account}/${project}/upload-token`);if(typeof result?.jwt!=='string')throw new Error('Pages upload credential missing');return result.jwt;},
    missing(hashes,jwt){return request('/pages/assets/check-missing',{method:'POST',body:{hashes},uploadToken:jwt});},
    upload(items,jwt){return request('/pages/assets/upload',{method:'POST',body:items,uploadToken:jwt});},
    retain(hashes,jwt){return request('/pages/assets/upsert-hashes',{method:'POST',body:{hashes},uploadToken:jwt});},
    deploy(project,form){projectName(project);return request(`${account}/${project}/deployments`,{method:'POST',body:form});},
    async deployment(project,id){projectName(project);if(!/^[a-f0-9-]{32,36}$/.test(id))throw new Error('Invalid deployment ID');const value=await request(`${account}/${project}/deployments/${id}`);return {id:value.id,url:value.url,environment:value.environment,latest_stage:value.latest_stage};},
    async rollback(project,id){projectName(project);if(!/^[a-f0-9-]{32,36}$/.test(id))throw new Error('Invalid deployment ID');const current=await request(`${account}/${project}/deployments/${id}`);if(current.environment!=='production')throw new Error('Pages can roll back only to a production deployment');const value=await request(`${account}/${project}/deployments/${id}/rollback`,{method:'POST'});return {id:value.id,url:value.url,environment:value.environment};},
  };
}
/** Only idempotent content-addressed asset APIs may refresh/retry; never deployment POST. */
export function createPagesAssetSession(api,project){
  projectName(project);let current=null,expiresAt=null,pending=null;
  const refresh=failedToken=>{
    if(pending)return pending;
    // A slower failed request may hold the token which another upload already replaced.
    if(current!==null&&current!==failedToken)return Promise.resolve(current);
    pending=Promise.resolve().then(()=>api.uploadToken(project)).then(token=>{
      const expiry=uploadTokenExpiresAt(token);
      if(expiry!==null&&expiry<=Date.now())throw new Error('Pages returned an already expired upload credential');
      current=token;expiresAt=expiry;return token;
    }).finally(()=>{pending=null;});
    return pending;
  };
  // Check every asset operation, including the final retain, without idle refresh timers.
  const token=()=>pending??(current!==null&&(expiresAt===null||expiresAt>Date.now()+60000)?Promise.resolve(current):refresh(current));
  const run=async(operation,value)=>{
    const used=await token();
    try{return await api[operation](value,used);}
    catch(error){
      if(!(error instanceof PagesApiError)||(error.status!==401&&!error.codes.includes(8000013)))throw error;
      const renewed=await refresh(used);
      // Deliberately outside the catch: a second failure terminates this bucket.
      return api[operation](value,renewed);
    }
  };
  return {missing:hashes=>run('missing',hashes),upload:items=>run('upload',items),retain:hashes=>run('retain',hashes)};
}
/** Register only confirmed uploads, so check-missing can reuse them after an interruption. */
export async function uploadPagesBuckets({buckets,assets,loadItems,onProgress=()=>{}}){
  let cursor=0,uploaded=0,failure=null;
  const total=buckets.reduce((count,bucket)=>count+bucket.length,0);
  await Promise.all(Array.from({length:2},async()=>{
    while(!failure&&cursor<buckets.length){
      const current=buckets[cursor++];
      try{
        const items=await loadItems(current);
        if(failure)return;
        await assets.upload(items);
        // A concurrent failure does not invalidate this bucket's confirmed success.
        await assets.retain(current.map(entry=>entry.key));
        uploaded+=current.length;onProgress({phase:'upload',files:uploaded,total});
      }catch(error){failure??=error;}
    }
  }));
  // Settle both active buckets before returning; no background lane keeps uploading.
  if(failure)throw failure;
  return uploaded;
}
export async function workerBundle(checked){
  const modules=checked.entries.filter(entry=>entry.target.startsWith('_worker.js/')),form=new FormData();
  const bindings=[{type:'service',name:'KOREA_API',service:'korea-replay'}];
  form.set('metadata',JSON.stringify({main_module:'index.js',compatibility_date:checked.configuration.compatibility_date,compatibility_flags:checked.configuration.compatibility_flags,bindings}));
  for(const entry of modules){const name=entry.target.slice('_worker.js/'.length);if(!name.endsWith('.js'))throw new Error('Unexpected private Worker module');const bytes=await readFile(path.join(checked.client,entry.target));if(sha(bytes)!==entry.sha256)throw new Error('Worker module changed after audit');form.append(name,new File([bytes],name,{type:'application/javascript+module'}));}
  return new Response(form).blob();
}
export async function deployPagesStage({receiptPath,projectRoot=process.cwd(),api,production=false,onProgress=()=>{}}){
  const checked=await verifyPagesStage(receiptPath,{projectRoot}),project=checked.receipt.project;
  if(production&&project==='korea-replay'&&!checked.receipt.policy?.snapshot_origin)throw new Error('Production app requires an already verified immutable share target');
  if(!production&&checked.receipt.policy?.snapshot_origin)throw new Error('Production policy cannot be uploaded as a preview candidate');
  const details=await api.inspect(project);if(details.name!==project||details.subdomain!==project+'.pages.dev'||details.production_branch!=='main')throw new Error('Pages project identity/configuration mismatch');
  const entries=checked.entries.filter(entry=>!entry.target.startsWith('_worker.js/')&&!['_headers','_redirects','_routes.json'].includes(entry.target));
  const map=new Map();let hashed=0;
  for(const entry of entries){const bytes=await readFile(path.join(checked.client,entry.target));if(sha(bytes)!==entry.sha256)throw new Error('Asset changed after stage audit');map.set(entry.target,{...entry,key:pagesAssetHash(bytes,entry.target)});hashed++;if(hashed%500===0)onProgress({phase:'hash',files:hashed,total:entries.length});}
  const assets=createPagesAssetSession(api,project),hashes=[...new Set([...map.values()].map(entry=>entry.key))],missing=await assets.missing(hashes);
  if(!Array.isArray(missing)||missing.some(hash=>!hashes.includes(hash)))throw new Error('Pages returned an unexpected missing-asset list');
  const missingSet=new Set(missing),unique=new Map();for(const entry of map.values())if(missingSet.has(entry.key))unique.set(entry.key,entry);
  // Two bounded uploads. At most two individual 25 MiB files plus base64/JSON are resident.
  const buckets=[];let bucket=[],bytes=0;for(const entry of unique.values()){if(bucket.length&&(bytes+entry.bytes>16*1024*1024||bucket.length>=200)){buckets.push(bucket);bucket=[];bytes=0;}bucket.push(entry);bytes+=entry.bytes;}if(bucket.length)buckets.push(bucket);
  onProgress({phase:'missing',files:unique.size,total:hashes.length,cached:hashes.length-unique.size});
  const uploaded=await uploadPagesBuckets({buckets,assets,onProgress,loadItems:async current=>{const items=[];
    for(const entry of current){const content=await readFile(path.join(checked.client,entry.target));if(sha(content)!==entry.sha256)throw new Error('Asset changed during upload');items.push({key:entry.key,value:content.toString('base64'),metadata:{contentType:mime(entry.target)},base64:true});}
    return items;
  }});
  await assets.retain(hashes);
  const form=new FormData();form.set('manifest',JSON.stringify(Object.fromEntries([...map].map(([name,entry])=>['/'+name,entry.key]))));
  form.set('branch',production?'main':'candidate-'+checked.receipt.artifact_sha256.slice(0,12));
  form.set('commit_message','Verified '+checked.receipt.artifact_sha256);form.set('commit_dirty','true');
  for(const name of ['_headers','_routes.json','_redirects'])if(checked.entries.some(entry=>entry.target===name))form.set(name,new File([await readFile(path.join(checked.client,name))],name));
  if(project==='korea-replay'){const bundle=await workerBundle(checked);form.set('_worker.bundle',new File([bundle],'_worker.bundle',{type:bundle.type}));}
  form.set('wrangler_config_hash',checked.receipt.config_sha256);form.set('pages_build_output_dir','client');
  // A deployment POST is never retried automatically: an uncertain result requires a read-only status check.
  const result=await api.deploy(project,form);let url;try{url=new URL(result.url);}catch{throw new Error('Pages did not return its actual deployment URL');}
  if(!new RegExp(`^[a-f0-9]{8}\\.${project}\\.pages\\.dev$`).test(url.hostname)||url.protocol!=='https:'||url.username||url.password||url.port||url.pathname!=='/'||url.search||url.hash||!/^([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$/.test(result.id))throw new Error('Pages returned an unexpected immutable URL/ID');
  if(result.environment!==(production?'production':'preview'))throw new Error('Pages returned an unexpected deployment environment');
  const record={schema_version:1,project,id:result.id,url:url.origin,environment:result.environment,artifact_sha256:checked.receipt.artifact_sha256,release_id:checked.receipt.release_id,uploaded,cached:hashes.length-unique.size};
  const output=path.join(checked.directory,'remote-deployment-'+String(result.id)+'.json');await writeFile(output,JSON.stringify(record,null,2)+'\n',{flag:'wx'});
  return record;
}
export async function verifyPagesRemote({receiptPath,origin,projectRoot=process.cwd(),fetcher=fetch}){
  const checked=await verifyPagesStage(receiptPath,{projectRoot}),project=checked.receipt.project,url=new URL(origin);
  const production=origin===`https://${project}.pages.dev`;
  if(url.origin!==origin||(!production&&!new RegExp(`^[a-f0-9]{8}\\.${project}\\.pages\\.dev$`).test(url.hostname))||url.protocol!=='https:')throw new Error('An actual immutable deployment or exact production origin is required');
  // The public host shares the already verified candidate. Immutable hosts always pin themselves,
  // including the immutable URL assigned to a production upload by Pages.
  const snapshotOrigin=project==='korea-replay'?(production?checked.receipt.policy?.snapshot_origin:origin):null;
  if(project==='korea-replay'&&!snapshotOrigin)throw new Error('Production verification requires the staged verified immutable share target');
  const samples=[],get=async pathname=>{
    const response=await fetcher(origin+pathname,{redirect:'error',cache:'no-store',signal:AbortSignal.timeout(30000)});
    return response;
  };
  if(project==='korea-replay'){
    const response=await get('/api/v2/runtime'),runtime=await response.json();
    if(response.status!==200||runtime.schema_version!==2||runtime.platform!=='cloudflare-pages'||runtime.project!==project
      ||runtime.artifact_sha256!==checked.receipt.artifact_sha256||runtime.release_id!==checked.receipt.policy.release_id
      ||runtime.snapshot?.origin!==snapshotOrigin||runtime.snapshot?.hash!==new URL(snapshotOrigin).hostname.slice(0,8)
      ||JSON.stringify(runtime.data)!==JSON.stringify(checked.receipt.policy.data))throw new Error('Remote runtime does not match the staged app/data pin');
    samples.push({path:'/api/v2/runtime',passed:true});
  }
  const names=project==='korea-replay'?['index.html','data/catalog.json',...checked.entries.filter(entry=>/^assets\/.*\.(?:js|css)$/.test(entry.target)).map(entry=>entry.target)]:[checked.receipt.atlas_manifest.path.slice(1)];
  for(const name of names){const entry=checked.entries.find(value=>value.target===name);if(!entry)throw new Error('Required remote sample missing from stage');const pathname=name==='index.html'?'/':'/'+name,response=await get(pathname),bytes=Buffer.from(await response.arrayBuffer());
    if(response.status!==200||bytes.length!==entry.bytes||sha(bytes)!==entry.sha256)throw new Error('Remote static bytes differ: '+name);
    if(project==='korea-replay-data'&&response.headers.get('Access-Control-Allow-Origin')!=='*')throw new Error('Data CORS is not public');samples.push({path:pathname,passed:true});}
  const missing='/data/__pages-verification-missing__.json',missingResponse=await get(missing);await missingResponse.body?.cancel();if(missingResponse.status!==404)throw new Error('Missing data did not return HTTP 404');samples.push({path:missing,passed:true});
  const report={schema_version:1,passed:true,origin,origin_kind:production?'production':'immutable',snapshot_origin:snapshotOrigin,project,artifact_sha256:checked.receipt.artifact_sha256,release_id:checked.receipt.release_id,samples,
    scope:'Runtime, all application JS/CSS, index/catalog or data atlas, CORS and static 404. Browser visuals, live API, data closure and rollback require separate verification.'};
  const reportName=production?'verified-production.json':'verified-preview-'+url.hostname.slice(0,8)+'.json';
  await writeFile(path.join(checked.directory,reportName),JSON.stringify(report,null,2)+'\n',{flag:'wx'});return report;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  try{
    const [command,target,flag]=process.argv.slice(2),api=command==='verify'?null:createPagesApi({accountId:process.env.CLOUDFLARE_ACCOUNT_ID,token:process.env.CLOUDFLARE_API_TOKEN});
    let result;
    if(command==='inspect')result=await api.inspect(target);
    else if(command==='create'&&flag==='--execute')result=await api.create(target);
    else if(command==='upload'&&['--preview','--production'].includes(flag))result=await deployPagesStage({receiptPath:target,api,production:flag==='--production',onProgress:value=>process.stdout.write(JSON.stringify(value)+'\n')});
    else if(command==='verify')result=await verifyPagesRemote({receiptPath:target,origin:flag});
    else throw new Error('Usage: inspect <project> | create <project> --execute | upload <receipt> --preview|--production | verify <receipt> <actual-preview-or-production-origin>; credentials only in environment');
    process.stdout.write(JSON.stringify(result,null,2)+'\n');
  }catch(error){process.stderr.write(String(error.message)+'\n');process.exitCode=1;}
}
