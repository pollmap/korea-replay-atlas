import {readFile,writeFile,mkdir,readdir,lstat,realpath,link,copyFile,constants} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import path from 'node:path';
import {gunzipSync} from 'node:zlib';
import {pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import {hashFile,verifyStagedDeployment} from './deploy-preflight.mjs';

export const PAGE_LIMITS=Object.freeze({files:20000,targetFiles:18000,bytes:25*1024*1024,archiveBytes:1024*1024});
const SHA=/^[a-f0-9]{64}$/,RELEASE=/^[a-z0-9][a-z0-9._-]{0,127}$/;
const json=value=>JSON.stringify(value,null,2)+'\n';
const digest=value=>createHash('sha256').update(value).digest('hex');
const policyTarget='_worker.js/policy.js';
const routes={version:1,include:['/api/*'],exclude:[]};
const wrapper="import application from './app/index.js';\nimport {createPagesAdapter} from './adapter.js';\nimport policy from './policy.js';\nexport default createPagesAdapter(application,policy);\n";
function safePath(value){
  if(typeof value!=='string'||!/^[a-zA-Z0-9._/-]+$/.test(value)||value.split('/').some(part=>!part||part==='.'||part==='..'))throw new Error('Unsafe publication path');
  return value;
}
function descendant(base,value){
  const relative=path.relative(path.resolve(base),path.resolve(value));
  if(!relative||relative.startsWith('..'+path.sep)||relative==='..'||path.isAbsolute(relative))throw new Error('Path escapes the permitted project directory');
  return path.resolve(value);
}
async function noLinks(base,file){
  const root=await realpath(base);let cursor=path.resolve(base);
  for(const part of path.relative(cursor,descendant(base,file)).split(path.sep)){
    cursor=path.join(cursor,part);const info=await lstat(cursor);
    if(info.isSymbolicLink())throw new Error('Symlink or junction is not a publication input');
  }
  descendant(root,await realpath(file));
}
async function filesIn(directory,prefix=''){
  const result=[];
  for(const entry of await readdir(directory,{withFileTypes:true})){
    if(entry.isSymbolicLink())throw new Error('Symlink or junction is not a publication input');
    const target=prefix+entry.name;safePath(target);
    if(entry.isDirectory())result.push(...await filesIn(path.join(directory,entry.name),target+'/'));
    else if(entry.isFile())result.push(target);else throw new Error('Unsupported publication file');
  }
  return result.sort();
}
async function parallel(items,action){let cursor=0;await Promise.all(Array.from({length:4},async()=>{while(cursor<items.length)await action(items[cursor++]);}));}
function fileEntry(target,bytes){return {target,bytes:bytes.byteLength,sha256:digest(bytes)};}
export function pagesHeaders(source){
  // GLBs in the audited static bundle are uncompressed glTF binaries. Pages
  // negotiates wire compression itself; a copied Worker gzip hint would label
  // an identity response incorrectly. Leave every other path policy intact.
  let route='';
  return source.split(/\r?\n/).filter(line=>{
    if(line&&!/^\s/.test(line)&&!line.startsWith('#'))route=line.trim();
    return !(route==='/data/*.glb'&&/^\s+Content-Encoding\s*:/i.test(line));
  }).join('\n');
}
function auditEntries(entries){
  if(!Array.isArray(entries)||!entries.length||entries.length>PAGE_LIMITS.files||new Set(entries.map(entry=>entry.target)).size!==entries.length)throw new Error('Duplicate assets or Pages 20,000-file limit exceeded');
  for(const entry of entries){safePath(entry.target);if(!SHA.test(entry.sha256)||!Number.isSafeInteger(entry.bytes)||entry.bytes<0||entry.bytes>PAGE_LIMITS.bytes)throw new Error('Invalid hash/size or Pages 25 MiB limit exceeded');}
}
function preview(origin,project){
  let url;try{url=new URL(origin);}catch{throw new Error('Actual immutable Pages URL required');}
  if(url.protocol!=='https:'||url.username||url.password||url.port||url.pathname!=='/'||url.search||url.hash||!new RegExp(`^[a-f0-9]{8}\\.${project}\\.pages\\.dev$`).test(url.hostname))throw new Error('Actual immutable Pages URL required');
  return url.origin;
}
function dataPin(value){
  if(!value||!SHA.test(value.manifest_sha256)||!/^\/data\/atlas\/[a-z0-9._-]+\/manifest\.json$/.test(value.manifest_path))throw new Error('A fixed atlas manifest is required');
  safePath(value.manifest_path.slice(1));return {origin:preview(value.origin,'korea-replay-data'),manifest_path:value.manifest_path,manifest_sha256:value.manifest_sha256};
}
function config(project){return {name:project,compatibility_date:'2026-09-16',compatibility_flags:['nodejs_compat'],pages_build_output_dir:'./client',
  ...(project==='korea-replay'?{services:[{binding:'KOREA_API',service:'korea-replay'}]}:{})};}
function validateConfig(value,project){if(json(value)!==json(config(project)))throw new Error('Pages configuration differs from the exact free-service allowlist');}
function artifact(entries,configuration,policy){
  const normalized=policy?{...policy,artifact_sha256:'',snapshot_origin:null}:null;
  return digest(json({schema_version:1,files:entries.filter(entry=>entry.target!==policyTarget).sort((a,b)=>a.target.localeCompare(b.target)),config:configuration,policy:normalized}));
}
async function writeNew(file,value){await mkdir(path.dirname(file),{recursive:true});await writeFile(file,value,{flag:'wx'});}
export async function copyImmutable(source,destination,{copyOnly=false}={}){
  await mkdir(path.dirname(destination),{recursive:true});
  if(!copyOnly){try{await link(source,destination);return 'linked';}catch(error){if(!['EXDEV','EPERM','EACCES','ENOSYS','EMLINK','ENOTSUP'].includes(error.code))throw error;}}
  await copyFile(source,destination,constants.COPYFILE_EXCL);return 'copied';
}
async function createStage(projectRoot,project,entries,generated,sources,policy,copyOnly){
  entries.sort((a,b)=>a.target.localeCompare(b.target));auditEntries(entries);
  const configuration=config(project),artifactSha=artifact(entries,configuration,policy);
  if(policy){policy={...policy,artifact_sha256:artifactSha};generated.set(policyTarget,Buffer.from('export default '+JSON.stringify(policy)+';\n'));entries.push(fileEntry(policyTarget,generated.get(policyTarget)));entries.sort((a,b)=>a.target.localeCompare(b.target));auditEntries(entries);}
  const suffix=policy?.snapshot_origin?'-production-'+new URL(policy.snapshot_origin).hostname.slice(0,8):'-candidate';
  const parent=path.join(projectRoot,'.local','pages-release');await mkdir(parent,{recursive:true});await noLinks(projectRoot,parent);
  const directory=descendant(parent,path.join(parent,project+'-'+artifactSha.slice(0,16)+suffix));
  try{await mkdir(directory);}catch(error){
    if(error.code!=='EEXIST')throw error;
    // An identical completed stage is a no-op; partial or altered stages fail verification.
    const existing=await verifyPagesStage(path.join(directory,'receipt.json'),{projectRoot});
    if(existing.receipt.artifact_sha256!==artifactSha)throw new Error('Existing stage identity differs');
    return {directory,receiptPath:path.join(directory,'receipt.json'),receipt:existing.receipt};
  }
  const client=path.join(directory,'client');await mkdir(client);let linked=0,copied=0;
  await parallel(entries,async entry=>{
    const destination=path.join(client,entry.target);
    if(generated.has(entry.target))await writeNew(destination,generated.get(entry.target));
    else {const method=await copyImmutable(sources.get(entry.target),destination,{copyOnly:copyOnly||entry.target.startsWith('_worker.js/')});if(method==='linked')linked++;else copied++;}
    if((await lstat(destination)).size!==entry.bytes||await hashFile(destination)!==entry.sha256)throw new Error('Staged asset failed final integrity verification: '+entry.target);
  });
  await writeNew(path.join(directory,'wrangler.json'),json(configuration));
  await writeNew(path.join(directory,'asset-manifest.json'),json(entries));
  const receipt={schema_version:1,platform:'cloudflare-pages',project,complete:true,artifact_sha256:artifactSha,
    release_id:policy?.release_id??generated.atlasRelease,files:entries.length,bytes:entries.reduce((sum,entry)=>sum+entry.bytes,0),
    within_target:entries.length<=PAGE_LIMITS.targetFiles,config_sha256:digest(json(configuration)),manifest_sha256:digest(json(entries)),
    ...(policy?{policy}:{atlas_manifest:generated.atlasManifest}),copied,linked};
  await writeNew(path.join(directory,'receipt.json'),json(receipt));
  return {directory,receiptPath:path.join(directory,'receipt.json'),receipt};
}
export async function verifyPagesStage(receiptPath,{projectRoot=process.cwd()}={}){
  const directory=path.dirname(path.resolve(receiptPath)),parent=path.join(projectRoot,'.local','pages-release');descendant(parent,directory);await noLinks(projectRoot,receiptPath);
  const receipt=JSON.parse(await readFile(receiptPath,'utf8'));
  if(receipt.schema_version!==1||receipt.platform!=='cloudflare-pages'||receipt.complete!==true||!['korea-replay','korea-replay-data'].includes(receipt.project))throw new Error('Incomplete or unexpected Pages stage');
  const configuration=JSON.parse(await readFile(path.join(directory,'wrangler.json'),'utf8'));validateConfig(configuration,receipt.project);
  if(await hashFile(path.join(directory,'wrangler.json'))!==receipt.config_sha256||await hashFile(path.join(directory,'asset-manifest.json'))!==receipt.manifest_sha256)throw new Error('Pages manifest/config integrity mismatch');
  const entries=JSON.parse(await readFile(path.join(directory,'asset-manifest.json'),'utf8'));auditEntries(entries);
  const client=path.join(directory,'client'),actual=await filesIn(client);
  const expected=entries.map(entry=>entry.target).sort();
  if(actual.length!==entries.length||entries.length!==receipt.files||actual.some((name,index)=>name!==expected[index]))throw new Error('Missing or additional Pages files');
  await parallel(entries,async entry=>{const file=path.join(client,entry.target);if((await lstat(file)).size!==entry.bytes||await hashFile(file)!==entry.sha256)throw new Error('Pages asset integrity mismatch: '+entry.target);});
  if(receipt.project==='korea-replay'){
    const policy=receipt.policy;dataPin(policy?.data);if(policy.snapshot_origin)preview(policy.snapshot_origin,'korea-replay');
    if(await readFile(path.join(client,policyTarget),'utf8')!=='export default '+JSON.stringify(policy)+';\n')throw new Error('Pages runtime policy differs from the receipt');
    if(json(JSON.parse(await readFile(path.join(client,'_routes.json'),'utf8')))!==json(routes))throw new Error('Only API paths may invoke Pages Functions');
  }else if(actual.some(name=>name.startsWith('_worker.js')||name==='_routes.json'))throw new Error('Data Pages must remain static');
  if(artifact(entries,configuration,receipt.policy??null)!==receipt.artifact_sha256)throw new Error('Pages artifact identity mismatch');
  return {directory,client,receipt,entries,configuration};
}
export async function stagePagesApp({projectRoot=process.cwd(),receiptPath,data,snapshotOrigin=null,candidateReceiptPath=null,copyOnly=false}){
  projectRoot=path.resolve(projectRoot);const sourceReceipt=path.resolve(projectRoot,receiptPath);
  descendant(path.join(projectRoot,'.local','deploy','bundles'),sourceReceipt);await noLinks(projectRoot,sourceReceipt);
  const preliminary=JSON.parse(await readFile(sourceReceipt,'utf8'));
  if(path.resolve(preliminary.bundle)!==path.dirname(sourceReceipt))throw new Error('Source receipt must be inside its immutable bundle');
  await noLinks(projectRoot,path.join(preliminary.bundle,'client'));await noLinks(projectRoot,path.join(preliminary.bundle,'worker'));
  const {receipt}=await verifyStagedDeployment(sourceReceipt);
  const input=JSON.parse(await readFile(path.join(receipt.bundle,'asset-manifest.json'),'utf8'));
  if(input.some(entry=>entry.target==='_routes.json'||entry.target.startsWith('_worker.js')))throw new Error('Reserved Pages file in source bundle');
  const generated=new Map(),sources=new Map(),entries=input.map(entry=>({target:entry.target,sha256:entry.sha256,bytes:entry.bytes}));
  for(const entry of entries)sources.set(entry.target,path.join(receipt.bundle,'client',entry.target));
  const headerIndex=entries.findIndex(entry=>entry.target==='_headers');
  if(headerIndex>=0){
    const original=await readFile(sources.get('_headers'),'utf8'),normalized=pagesHeaders(original);
    if(normalized!==original){entries.splice(headerIndex,1);sources.delete('_headers');generated.set('_headers',Buffer.from(normalized));}
  }
  for(const entry of receipt.worker_files){const target='_worker.js/app/'+entry.target.slice('worker/'.length),file=path.join(receipt.bundle,entry.target);sources.set(target,file);entries.push({target,sha256:entry.sha256,bytes:(await lstat(file)).size});}
  const compiled=await build({entryPoints:[path.join(projectRoot,'worker','pages-adapter.ts')],bundle:true,write:false,format:'esm',platform:'browser',target:'es2022',legalComments:'inline'});
  generated.set('_worker.js/adapter.js',Buffer.from(compiled.outputFiles[0].contents));generated.set('_worker.js/index.js',Buffer.from(wrapper));generated.set('_routes.json',Buffer.from(json(routes)));
  if(!entries.some(entry=>entry.target==='404.html'))throw new Error('A top-level 404.html is required to disable SPA fallback');
  for(const [target,value] of generated)entries.push(fileEntry(target,value));
  const catalog=JSON.parse(await readFile(path.join(receipt.bundle,'client','data','catalog.json'),'utf8'));
  const policy={release_id:receipt.release_id,artifact_sha256:'',snapshot_origin:snapshotOrigin?preview(snapshotOrigin,'korea-replay'):null,data:dataPin(data),...(catalog.live_transit_routes?{bus_catalog:catalog.live_transit_routes}:{})};
  if(snapshotOrigin){
    if(!candidateReceiptPath)throw new Error('Production must refer to a separately verified candidate stage');
    const candidate=await verifyPagesStage(candidateReceiptPath,{projectRoot});
    if(candidate.receipt.project!=='korea-replay'||candidate.receipt.policy.snapshot_origin!==null||candidate.receipt.artifact_sha256!==artifact(entries,config('korea-replay'),policy))throw new Error('Candidate and production application/data/configuration differ');
    const verified=JSON.parse(await readFile(path.join(candidate.directory,'verified-preview-'+new URL(snapshotOrigin).hostname.slice(0,8)+'.json'),'utf8'));
    if(verified.passed!==true||verified.origin!==snapshotOrigin||verified.artifact_sha256!==candidate.receipt.artifact_sha256||verified.project!=='korea-replay')throw new Error('Candidate preview has not passed remote verification');
  }
  return createStage(projectRoot,'korea-replay',entries,generated,sources,policy,copyOnly);
}
// Large 3D payloads stay in a verified immutable legacy deployment, never in
// the active 2D staging closure. The small catalog and all 2D/traffic data stay.
export const LEGACY_SPATIAL_PREFIXES=Object.freeze(['data/retiled/','data/terrain/','data/hierarchy/','data/building-streams/','data/building-parts/']);
const fixedPagesFile=target=>target.startsWith('collection/')||target.startsWith('data/')||target.startsWith('_worker.js/')||['_headers','404.html','_routes.json','_redirects'].includes(target);
// Exact PR95 retirement only; Python independently checks the whole Worker tree.
// Policy/adapter, application data, source snapshots and collection stay pinned.
export function validateRetiredWorkerReplacement(replacements,sourceEntries,worker,retire3d){
  if(!Array.isArray(replacements)||replacements.length>1)throw new Error('Invalid audited Worker replacement inventory');
  if(!replacements.length)return [];
  const entry=replacements[0],prior=sourceEntries.find(row=>row.target==='_worker.js/app/index.js');
  if(!retire3d||!entry||entry.target!=='_worker.js/app/index.js'||entry.transition!=='sun-runtime-retirement-20261009'
    ||entry.previous_sha256!=='f2d2d04f607fdca15f50fb863bbb1fab94aef713851c89bc9eeb9dd14f218acf'
    ||entry.sha256!=='d5547d557c8802ef8de00284844cfc375d1a4d9912f78fbaedd9e736b036c17d'||entry.bytes!==149458
    ||prior?.sha256!==entry.previous_sha256||prior?.bytes!==157095||path.resolve(entry.path)!==path.join(worker,'index.js'))throw new Error('Worker replacement is not the audited 2D retirement');
  return replacements;
}
async function inspectFreshFrontend(projectRoot,source,clientDirectory,workerDirectory,pythonExecutable,retire3d=false){
  const client=descendant(path.join(projectRoot,'dist'),path.resolve(projectRoot,clientDirectory));
  const worker=descendant(path.join(projectRoot,'dist'),path.resolve(projectRoot,workerDirectory));
  await noLinks(projectRoot,client);await noLinks(projectRoot,worker);
  const request={receiptPath:path.join(source.directory,'receipt.json'),clientDirectory:client,workerDirectory:worker,artifactSha256:source.receipt.artifact_sha256,retire3d};
  const payload=await new Promise((resolve,reject)=>{
    const child=spawn(pythonExecutable,['-m','pipeline.pages_frontend'],{cwd:projectRoot,stdio:['pipe','pipe','pipe']});
    let stdout='',stderr='',size=0;
    const timer=setTimeout(()=>{child.kill();reject(new Error('Frontend inspection timed out'));},120000);
    child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');
    const collect=(chunk,isError)=>{size+=Buffer.byteLength(chunk);if(size>4*1024*1024){child.kill();reject(new Error('Frontend inspection output exceeds budget'));return;}if(isError)stderr+=chunk;else stdout+=chunk;};
    child.stdout.on('data',chunk=>collect(chunk,false));child.stderr.on('data',chunk=>collect(chunk,true));
    child.on('error',error=>{clearTimeout(timer);reject(error);});
    child.on('close',code=>{clearTimeout(timer);if(code!==0){reject(new Error('Frontend audit failed: '+stderr.slice(-2000)));return;}try{resolve(JSON.parse(stdout));}catch{reject(new Error('Invalid frontend inspection result'));}});
    child.stdin.on('error',error=>{clearTimeout(timer);child.kill();reject(error);});child.stdin.end(JSON.stringify(request));
  });
  if(payload.schema_version!==1||payload.artifact_sha256!==source.receipt.artifact_sha256||payload.manifest_sha256!==source.receipt.manifest_sha256)throw new Error('Frontend inspection differs from the verified base');
  auditEntries(payload.files);
  for(const entry of payload.files){
    if(fixedPagesFile(entry.target)||path.resolve(entry.path)!==path.join(client,entry.target))throw new Error('Frontend attempts to replace fixed data or Pages policy');
    await noLinks(projectRoot,entry.path);
  }
  const workerReplacements=validateRetiredWorkerReplacement(payload.worker_replacements??[],source.entries,worker,retire3d);
  for(const entry of workerReplacements)await noLinks(projectRoot,entry.path);
  return {files:payload.files,workerReplacements};
}
export async function stagePagesLeanApp({projectRoot=process.cwd(),receiptPath,legacy3dOrigin,retire3d=false,data,snapshotOrigin=null,candidateReceiptPath=null,copyOnly=false,clientDirectory=null,workerDirectory=null,pythonExecutable=process.platform==='win32'?'python':'python3'}){
  projectRoot=path.resolve(projectRoot);
  const legacy=legacy3dOrigin?preview(legacy3dOrigin,'korea-replay'):null;
  if(!retire3d&&!legacy)throw new Error('Legacy origin or explicit 3D retirement required');
  const source=await verifyPagesStage(path.resolve(projectRoot,receiptPath),{projectRoot});
  if(source.receipt.project!=='korea-replay')throw new Error('A verified application stage is required');
  const redirectText=retire3d?'':LEGACY_SPATIAL_PREFIXES.map(prefix=>`/${prefix}* ${legacy}/${prefix}:splat 302`).join('\n')+'\n';
  const oldRedirect=source.entries.find(entry=>entry.target==='_redirects');
  if(oldRedirect&&!retire3d&&await readFile(path.join(source.client,'_redirects'),'utf8')!==redirectText)throw new Error('Existing redirect policy differs; explicit migration required');
  let entries=source.entries.filter(entry=>entry.target!==policyTarget&&entry.target!=='_redirects'&&(!retire3d||!entry.target.startsWith('cesium/'))&&!LEGACY_SPATIAL_PREFIXES.some(prefix=>entry.target.startsWith(prefix)));
  const sources=new Map(entries.map(entry=>[entry.target,path.join(source.client,entry.target)]));
  if(Boolean(clientDirectory)!==Boolean(workerDirectory))throw new Error('Fresh frontend requires both clientDirectory and workerDirectory');
  if(clientDirectory){
    const frontend=await inspectFreshFrontend(projectRoot,source,clientDirectory,workerDirectory,pythonExecutable,retire3d);
    const priorFrontend=new Map(source.entries.map(entry=>[entry.target,entry]));
    for(const entry of entries)if(!fixedPagesFile(entry.target))sources.delete(entry.target);
    entries=entries.filter(entry=>fixedPagesFile(entry.target));
    for(const entry of frontend.files){
      entries.push({target:entry.target,bytes:entry.bytes,sha256:entry.sha256});
      const prior=priorFrontend.get(entry.target),unchanged=prior?.sha256===entry.sha256&&prior?.bytes===entry.bytes;
      sources.set(entry.target,unchanged?path.join(source.client,entry.target):entry.path);
    }
    for(const entry of frontend.workerReplacements){
      entries=entries.filter(prior=>prior.target!==entry.target);
      entries.push({target:entry.target,bytes:entry.bytes,sha256:entry.sha256});
      sources.set(entry.target,entry.path);
    }
  }
  const generated=new Map();
  if(!retire3d){generated.set('_redirects',Buffer.from(redirectText));entries.push(fileEntry('_redirects',generated.get('_redirects')));}
  const policy={...source.receipt.policy,artifact_sha256:'',snapshot_origin:snapshotOrigin?preview(snapshotOrigin,'korea-replay'):null,data:dataPin(data??source.receipt.policy.data)};
  const policyBytes=Buffer.byteLength('export default '+JSON.stringify({...policy,artifact_sha256:'f'.repeat(64)})+';\n');
  if(entries.reduce((sum,entry)=>sum+entry.bytes,policyBytes)>256*1024*1024)throw new Error('Lean application exceeds 256 MiB; publish large data separately');
  if(snapshotOrigin){
    if(!candidateReceiptPath)throw new Error('Production must refer to a separately verified candidate stage');
    const candidate=await verifyPagesStage(candidateReceiptPath,{projectRoot});
    if(candidate.receipt.project!=='korea-replay'||candidate.receipt.policy.snapshot_origin!==null||candidate.receipt.artifact_sha256!==artifact(entries,config('korea-replay'),policy))throw new Error('Candidate and production application/data/configuration differ');
    const verified=JSON.parse(await readFile(path.join(candidate.directory,'verified-preview-'+new URL(snapshotOrigin).hostname.slice(0,8)+'.json'),'utf8'));
    if(verified.passed!==true||verified.origin!==snapshotOrigin||verified.artifact_sha256!==candidate.receipt.artifact_sha256||verified.project!=='korea-replay')throw new Error('Candidate preview has not passed remote verification');
  }
  return createStage(projectRoot,'korea-replay',entries,generated,sources,policy,copyOnly);
}
async function publication(projectRoot,filename,kind){
  filename=path.resolve(projectRoot,filename);descendant(path.join(projectRoot,'.local'),filename);await noLinks(projectRoot,filename);
  const root=path.dirname(filename),value=JSON.parse(await readFile(filename,'utf8')),rawReference=value[kind];
  const reference=kind==='summary_release'&&rawReference?{...rawReference,release_id:rawReference.summary_release_id}:rawReference;
  if(value.schema_version!==1||!reference||!RELEASE.test(reference.release_id)||!SHA.test(reference.sha256))throw new Error('Invalid data publication contract');
  const referencePath=safePath(reference.path.replace(/^\//,''));if(!referencePath.startsWith('data/'))throw new Error('Data reference must be public data');
  const entries=value.files.map(item=>({target:safePath(item.path.replace(/^\//,'')),sha256:item.sha256,bytes:item.byte_length}));auditEntries(entries);
  if(entries.some(entry=>!entry.target.startsWith('data/')||entry.target.endsWith('.pmtiles')&&entry.bytes>PAGE_LIMITS.archiveBytes))throw new Error('Non-data file or PMTiles archive above 1 MiB');
  const actual=await filesIn(path.join(root,'data'),'data/');if(json(actual)!==json(entries.map(entry=>entry.target).sort()))throw new Error('Publication does not contain its exact declared data closure');
  await parallel(entries,async entry=>{const file=path.join(root,entry.target);await noLinks(root,file);if((await lstat(file)).size!==entry.bytes||await hashFile(file)!==entry.sha256)throw new Error('Data publication hash/size mismatch');const metadata=value.files.find(item=>item.path.replace(/^\//,'')===entry.target)?.transport;
    if(metadata){if(metadata.encoding!=='gzip'||!SHA.test(metadata.decoded_sha256)||!Number.isSafeInteger(metadata.decoded_bytes)||metadata.decoded_bytes<=0||metadata.decoded_bytes>24*1024*1024)throw new Error('Invalid compressed publication metadata');const decoded=gunzipSync(await readFile(file),{maxOutputLength:metadata.decoded_bytes});if(decoded.length!==metadata.decoded_bytes||digest(decoded)!==metadata.decoded_sha256)throw new Error('Decoded publication hash/size mismatch');}});
  const referenced=entries.find(entry=>entry.target===referencePath);if(referenced?.sha256!==reference.sha256)throw new Error('Publication entry is absent or has a different hash');
  const body=JSON.parse(await readFile(path.join(root,referencePath),'utf8'));if((kind==='summary_release'?body.summary_release_id:body.release_id)!==reference.release_id)throw new Error('Publication release differs from entry body');
  if(kind==='summary_release'&&(body.kind!=='property-complex-summary-release'||body.property_release_id!==value.property_release_id))throw new Error('Summary property release differs from publication');
  return {root,entries,propertyRelease:body.property_release_id,reference:{path:'/'+referencePath,sha256:reference.sha256,release_id:reference.release_id}};
}
export async function stagePagesData({projectRoot=process.cwd(),mapPublication,propertyPublication,summaryPublications=[],copyOnly=false}){
  projectRoot=path.resolve(projectRoot);
  if(!Array.isArray(summaryPublications)||summaryPublications.length>8)throw new Error('Invalid summary publication list');
  const [map,property]=await Promise.all([publication(projectRoot,mapPublication,'map_catalog'),publication(projectRoot,propertyPublication,'property_release')]);
  const summaries=await Promise.all(summaryPublications.map(file=>publication(projectRoot,file,'summary_release')));
  if(summaries.some(row=>row.propertyRelease!==property.reference.release_id))throw new Error('Summary and transaction publications must pin the same property release');
  const content={schema_version:1,map_catalog:map.reference,property_release:property.reference,...(summaries.length?{property_summaries:summaries.map(row=>row.reference)}:{})},release='atlas-'+digest(json(content)).slice(0,16);
  const manifest={...content,release_id:release},target=`data/atlas/${release}/manifest.json`,body=Buffer.from(json(manifest));
  const generated=new Map([[target,body],['404.html',Buffer.from('<!doctype html><meta charset="utf-8"><title>자료 없음</title><p>요청한 자료가 없습니다.</p>')],['_headers',Buffer.from('/*\n  Access-Control-Allow-Origin: *\n  Access-Control-Allow-Methods: GET, HEAD, OPTIONS\n  Access-Control-Expose-Headers: Content-Length, ETag, Content-Encoding\n  X-Content-Type-Options: nosniff\n/data/*\n  Cache-Control: public, max-age=31536000, immutable\n/data/*.pmtiles\n  Content-Type: application/octet-stream\n')]]);
  generated.atlasRelease=release;generated.atlasManifest={path:'/'+target,sha256:digest(body),release_id:release};
  const entries=[],sources=new Map();
  for(const publication of [map,property,...summaries])for(const entry of publication.entries){entries.push(entry);sources.set(entry.target,path.join(publication.root,entry.target));}
  for(const [target,value] of generated)entries.push(fileEntry(target,value));
  return createStage(projectRoot,'korea-replay-data',entries,generated,sources,null,copyOnly);
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  try{
    const [command,file]=process.argv.slice(2);
    if(command==='check'){const result=await verifyPagesStage(file);process.stdout.write(json({passed:true,project:result.receipt.project,files:result.receipt.files,artifact_sha256:result.receipt.artifact_sha256}));}
    else if(command==='app'||command==='data'||command==='lean-app'){
      const options=JSON.parse(await readFile(file,'utf8'));
      const result=await(command==='app'?stagePagesApp(options):command==='lean-app'?stagePagesLeanApp(options):stagePagesData(options));process.stdout.write(json({receipt:result.receiptPath,...result.receipt}));
    }else throw new Error('Usage: node scripts/pages-release.mjs app|lean-app|data <private-options.json> | check <receipt.json>');
  }catch(error){process.stderr.write(String(error.message)+'\n');process.exitCode=1;}
}
