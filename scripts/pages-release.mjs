import {readFile,writeFile,mkdir,readdir,lstat,realpath,link,copyFile,constants} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';
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
  await mkdir(directory); // EEXIST is intentional: never resume by replacing a hardlinked input.
  const client=path.join(directory,'client');await mkdir(client);let linked=0,copied=0;
  await parallel(entries,async entry=>{
    const destination=path.join(client,entry.target);
    if(generated.has(entry.target))await writeNew(destination,generated.get(entry.target));
    else {const method=await copyImmutable(sources.get(entry.target),destination,{copyOnly});if(method==='linked')linked++;else copied++;}
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
async function publication(projectRoot,filename,kind){
  filename=path.resolve(projectRoot,filename);descendant(path.join(projectRoot,'.local'),filename);await noLinks(projectRoot,filename);
  const root=path.dirname(filename),value=JSON.parse(await readFile(filename,'utf8')),reference=value[kind];
  if(value.schema_version!==1||!reference||!RELEASE.test(reference.release_id)||!SHA.test(reference.sha256))throw new Error('Invalid data publication contract');
  const referencePath=safePath(reference.path.replace(/^\//,''));if(!referencePath.startsWith('data/'))throw new Error('Data reference must be public data');
  const entries=value.files.map(item=>({target:safePath(item.path.replace(/^\//,'')),sha256:item.sha256,bytes:item.byte_length}));auditEntries(entries);
  if(entries.some(entry=>!entry.target.startsWith('data/')||entry.target.endsWith('.pmtiles')&&entry.bytes>PAGE_LIMITS.archiveBytes))throw new Error('Non-data file or PMTiles archive above 1 MiB');
  const actual=await filesIn(path.join(root,'data'),'data/');if(json(actual)!==json(entries.map(entry=>entry.target).sort()))throw new Error('Publication does not contain its exact declared data closure');
  await parallel(entries,async entry=>{const file=path.join(root,entry.target);await noLinks(root,file);if((await lstat(file)).size!==entry.bytes||await hashFile(file)!==entry.sha256)throw new Error('Data publication hash/size mismatch');});
  const referenced=entries.find(entry=>entry.target===referencePath);if(referenced?.sha256!==reference.sha256)throw new Error('Publication entry is absent or has a different hash');
  const body=JSON.parse(await readFile(path.join(root,referencePath),'utf8'));if(body.release_id!==reference.release_id)throw new Error('Publication release differs from entry body');
  return {root,entries,reference:{path:'/'+referencePath,sha256:reference.sha256,release_id:reference.release_id}};
}
export async function stagePagesData({projectRoot=process.cwd(),mapPublication,propertyPublication,copyOnly=false}){
  projectRoot=path.resolve(projectRoot);
  const [map,property]=await Promise.all([publication(projectRoot,mapPublication,'map_catalog'),publication(projectRoot,propertyPublication,'property_release')]);
  const content={schema_version:1,map_catalog:map.reference,property_release:property.reference},release='atlas-'+digest(json(content)).slice(0,16);
  const manifest={...content,release_id:release},target=`data/atlas/${release}/manifest.json`,body=Buffer.from(json(manifest));
  const generated=new Map([[target,body],['404.html',Buffer.from('<!doctype html><meta charset="utf-8"><title>자료 없음</title><p>요청한 자료가 없습니다.</p>')],['_headers',Buffer.from('/*\n  Access-Control-Allow-Origin: *\n  Access-Control-Allow-Methods: GET, HEAD, OPTIONS\n  Access-Control-Expose-Headers: Content-Length, ETag, Content-Encoding\n  X-Content-Type-Options: nosniff\n/data/*\n  Cache-Control: public, max-age=31536000, immutable\n/data/*.pmtiles\n  Content-Type: application/octet-stream\n')]]);
  generated.atlasRelease=release;generated.atlasManifest={path:'/'+target,sha256:digest(body),release_id:release};
  const entries=[],sources=new Map();
  for(const publication of [map,property])for(const entry of publication.entries){entries.push(entry);sources.set(entry.target,path.join(publication.root,entry.target));}
  for(const [target,value] of generated)entries.push(fileEntry(target,value));
  return createStage(projectRoot,'korea-replay-data',entries,generated,sources,null,copyOnly);
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  try{
    const [command,file]=process.argv.slice(2);
    if(command==='check'){const result=await verifyPagesStage(file);process.stdout.write(json({passed:true,project:result.receipt.project,files:result.receipt.files,artifact_sha256:result.receipt.artifact_sha256}));}
    else if(command==='app'||command==='data'){
      const options=JSON.parse(await readFile(file,'utf8'));
      const result=await(command==='app'?stagePagesApp(options):stagePagesData(options));process.stdout.write(json({receipt:result.receiptPath,...result.receipt}));
    }else throw new Error('Usage: node scripts/pages-release.mjs app|data <private-options.json> | check <receipt.json>');
  }catch(error){process.stderr.write(String(error.message)+'\n');process.exitCode=1;}
}
