import {spawn} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import {pathToFileURL} from 'node:url';
import {verifyStagedDeployment} from './deploy-preflight.mjs';

async function runWrangler(args,{capture=false}={}){
  return new Promise((resolve,reject)=>{
    const child=spawn(process.execPath,['node_modules/wrangler/bin/wrangler.js',...args],{stdio:capture?'pipe':'inherit',windowsHide:true});
    let output='';
    if(capture){child.stdout.on('data',bytes=>{output+=bytes;});child.stderr.on('data',bytes=>{output+=bytes;});}
    child.on('error',reject);child.on('close',code=>resolve({code,output}));
  });
}

// Dependencies are injectable so initialization failures can be tested without
// reading credentials, calling Cloudflare or uploading any local data.
export async function deployRelease({initialize=false,verify=verifyStagedDeployment,run=runWrangler,read=readFile,write=writeFile}={}){
  const {receipt,configPath}=await verify();
  // Broker creation has its own non-public config and state lifecycle. A map
  // release must never turn this existing deployment into an initialization.
  if(initialize&&Object.hasOwn(receipt,'deployment_contract'))throw new Error('Broker-mode map releases require versions upload; broker initialization is a separate deployment.');
  let args;
  if(initialize){
    // Wrangler requires an initial deploy before versions upload. Initialize
    // only this new Worker with its public hostname disabled; version previews
    // remain enabled for the same validation/promotion sequence.
    const config=JSON.parse(await read(configPath,'utf8'));
    // Use exactly the deployment account/config. JSON listing skips Wrangler's
    // follow-up version-detail lookups, whose errors cannot prove a missing Worker.
    const existing=await run(['deployments','list','--name','korea-replay','--config',configPath,'--json'],{capture:true});
    if(!Number.isInteger(existing.code)||existing.code===0||!existing.output.includes('[code: 10007]'))throw new Error('Initialization requires a confirmed missing korea-replay Worker. Existing or unverified Workers are left unchanged.');
    const initialPath=path.join(path.dirname(configPath),'wrangler.preview.json');
    await write(initialPath,JSON.stringify({...config,workers_dev:false,preview_urls:true},null,2)+'\n');
    args=['deploy','--config',initialPath];
  }else{
    args=['versions','upload','--config',configPath,'--message',`Verified static bundle ${receipt.bundle_id}; ${receipt.release_id}`];
  }
  const {code}=await run(args);return code??1;
}

export function parseDeploymentOptions(args){
  if(args.some(arg=>arg!=='--initialize')||args.length>1)throw new Error('Only --initialize is supported. Config, environment and mode overrides are not accepted.');
  return {initialize:args.includes('--initialize')};
}

// Upload a version only. Promotion follows the recorded preview verification.
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  try{process.exitCode=await deployRelease(parseDeploymentOptions(process.argv.slice(2)));}
  catch(error){process.stderr.write(String(error)+'\n');process.exitCode=1;}
}
