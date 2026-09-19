import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import {createHash} from 'node:crypto';
import {parseArgs} from 'node:util';
import validator from 'gltf-validator';

const root=path.resolve('public/data');
const {values}=parseArgs({options:{catalog:{type:'string'},output:{type:'string'}}});
const catalogBytes=await readFile(values.catalog??path.join(root,'catalog.json'));
const catalog=JSON.parse(catalogBytes.toString('utf8'));
const reports=[];
const manifests=new Set(),files=new Set();
function localFile(uri,base){
  if(typeof uri!=='string'||!uri||/[?#:\\]/u.test(uri))throw new Error('Invalid local tile URI');
  const file=path.resolve(base,uri),relative=path.relative(root,file);
  if(relative.startsWith('..')||path.isAbsolute(relative))throw new Error('Tile escapes public data');
  return file;
}
async function checkManifest(manifest,expectedHash){
  if(manifests.has(manifest))throw new Error('Repeated or cyclic tileset: '+manifest);
  manifests.add(manifest);
  const bytes=await readFile(manifest);
  if(expectedHash&&createHash('sha256').update(bytes).digest('hex')!==expectedHash)throw new Error('Tileset hash mismatch');
  const tileset=JSON.parse(bytes.toString('utf8')),stack=[tileset.root];
  while(stack.length){
    const tile=stack.pop();stack.push(...(tile.children??[]));
    for(const content of [...(tile.content?[tile.content]:[]),...(tile.contents??[])]){
      const file=localFile(content.uri??content.url,path.dirname(manifest));
      if(path.extname(file)==='.json'){await checkManifest(file);continue;}
      if(path.extname(file)!=='.glb'||files.has(file))throw new Error('Invalid or repeated GLB: '+file);
      files.add(file);
      const bytes=await readFile(file),hash=createHash('sha256').update(bytes).digest('hex');
      if(tile.extras?.sha256&&hash!==tile.extras.sha256)throw new Error('GLB hash mismatch: '+file);
      const report=await validator.validateBytes(new Uint8Array(bytes),{uri:path.basename(file),maxIssues:100,ignoredIssues:['UNUSED_OBJECT']});
      reports.push({file:path.relative(root,file),sha256:hash,errors:report.issues.numErrors,warnings:report.issues.numWarnings,messages:report.issues.messages});
      if(reports.length%100===0)process.stdout.write(JSON.stringify({stage:'gltf-core-validation',checked:reports.length})+'\n');
    }
  }
}
for(const asset of catalog.assets.filter(a=>a.format==='3d-tiles')){
  if(!asset.url.startsWith('/data/'))throw new Error('Invalid public asset URI');
  await checkManifest(localFile(asset.url.slice(6),root),asset.sha256);
}
const summary={validator:validator.version(),release_id:catalog.release_id,catalog_sha256:createHash('sha256').update(catalogBytes).digest('hex'),checked:reports.length,tilesets:manifests.size,errors:reports.reduce((n,r)=>n+r.errors,0),warnings:reports.reduce((n,r)=>n+r.warnings,0),ignored_information_codes:['UNUSED_OBJECT'],scope:'Khronos glTF core validation; metadata extension structure is checked separately by pipeline.mesh_metadata_audit',reports};
if(!reports.length)throw new Error('No GLBs found to validate');
const output=values.output??'.local/audit/gltf-validator.json';
await mkdir(path.dirname(output),{recursive:true});await writeFile(output,JSON.stringify(summary,null,2));
process.stdout.write(JSON.stringify({...summary,reports:reports.filter(r=>r.errors).slice(0,2)},null,2)+'\n');
if(summary.errors)process.exitCode=1;
