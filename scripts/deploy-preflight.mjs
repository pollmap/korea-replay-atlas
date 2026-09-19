import {readFile,readdir,stat} from 'node:fs/promises';
import {createReadStream} from 'node:fs';
import {createHash} from 'node:crypto';
import path from 'node:path';
import process from 'node:process';
import {pathToFileURL} from 'node:url';

const hashPattern=/^[a-f0-9]{64}$/;
export const BROKER_DEPLOYMENT_CONTRACT=Object.freeze({schema_version:1,live_transit_mode:'broker',
  broker:Object.freeze({binding:'LIVE_TRANSIT_BROKER',service:'korea-replay-live-broker'})});
const brokerConfigKeys=['name','main','compatibility_date','compatibility_flags','no_bundle','assets','vars','services','version_metadata','observability','workers_dev','preview_urls'];
const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
function exactFields(actual,expected){
  return record(actual)&&Object.keys(actual).length===Object.keys(expected).length
    &&Object.entries(expected).every(([key,value])=>record(value)?exactFields(actual[key],value):actual[key]===value);
}
export function validateTransitDeployment(config,plan){
  if(['env','durable_objects','migrations','exports'].some(key=>Object.hasOwn(config,key)))return '지도 배포에서 추가 env나 브로커 상태 바인딩을 선언할 수 없습니다.';
  const variables=config.vars??{};
  const schema=Object.hasOwn(plan??{},'schema_version')?plan.schema_version:1;
  if(schema!==1&&schema!==2)return '알 수 없는 브로커 배포 receipt 스키마입니다.';
  if(!Object.hasOwn(plan??{},'deployment_contract')){
    if(schema!==1)return '새 브로커 receipt에는 배포 계약이 필요합니다.';
    return Object.hasOwn(variables,'LIVE_TRANSIT_MODE')||(Object.hasOwn(config,'services')&&(!Array.isArray(config.services)||config.services.length!==0))
      ?'과거 배포 묶음에는 새로운 교통 모드·서비스를 추가할 수 없습니다.':null;
  }
  if(schema!==2||!exactFields(plan.deployment_contract,BROKER_DEPLOYMENT_CONTRACT))return '브로커 배포 계약이 정확한 허용 목록과 일치하지 않습니다.';
  const expectedVars={ENVIRONMENT:'production',DATA_STORAGE:'static',COLLECTORS_ENABLED:'false',STATIC_RELEASE_ID:plan.release_id,LIVE_TRANSIT_MODE:'broker'};
  if(Object.keys(config).length!==brokerConfigKeys.length||brokerConfigKeys.some(key=>!Object.hasOwn(config,key))
    ||!exactFields(variables,expectedVars)||!Array.isArray(config.services)||config.services.length!==1
    ||!exactFields(config.services[0],BROKER_DEPLOYMENT_CONTRACT.broker))return '브로커 배포는 정확한 모드·서비스·설정 필드를 요구합니다.';
  return null;
}
export function validateDeployment(config, plan) {
  const errors=[];
  if(config.vars?.ENVIRONMENT!=='production')errors.push('ENVIRONMENT를 production으로 설정해야 합니다.');
  if(config.vars?.DATA_STORAGE!=='static')errors.push('무료 배포는 static 저장 모드여야 합니다.');
  if(config.name!=='korea-replay'||config.main!=='./worker/index.js'||config.no_bundle!==true||config.assets?.directory!=='./client'||config.assets?.binding!=='ASSETS')errors.push('검증한 client/worker 파일만 korea-replay로 배포해야 합니다.');
  if(config.r2_buckets?.length||config.d1_databases?.length||config.triggers?.crons?.length||config.vars?.COLLECTORS_ENABLED!=='false')errors.push('무료 정적 배포에는 R2·D1·자동 수집을 연결하지 않습니다.');
  if(config.assets?.not_found_handling!=='none'||JSON.stringify(config.assets?.run_worker_first)!=='["/api/*"]')errors.push('지도 파일은 정적 경로로 제공하고 API만 Worker를 거쳐야 합니다.');
  if(config.version_metadata?.binding!=='CF_VERSION_METADATA'||config.preview_urls!==true)errors.push('고정 공유를 위해 배포 버전 메타데이터와 버전 미리보기 주소가 필요합니다.');
  if(!plan?.audit?.passed||plan.audit.release_id!==plan.release_id||!/^pub-[a-f0-9]{16}$/.test(plan.release_id??'')||!hashPattern.test(plan.catalog_hash??'')||plan.mode!=='static'||!plan.complete)errors.push('완료된 정적 배포 묶음이 없습니다. deploy:stage를 실행하세요.');
  if(!Number.isSafeInteger(plan?.count)||plan.count<1||plan.count>20000)errors.push('배포 파일 수는 20,000개 이하여야 합니다.');
  if(plan?.release_id!==config.vars?.STATIC_RELEASE_ID)errors.push('코드와 지도 릴리스가 일치하지 않습니다.');
  const transitError=validateTransitDeployment(config,plan);if(transitError)errors.push(transitError);
  return errors;
}

export async function hashFile(file){
  const h=createHash('sha256');for await(const chunk of createReadStream(file))h.update(chunk);return h.digest('hex');
}
function inside(base,relative){
  if(typeof relative!=='string'||relative.includes('\\')||relative.split('/').some(p=>!p||p==='.'||p==='..'))throw new Error('잘못된 배포 파일 경로입니다.');
  const result=path.resolve(base,relative);
  if(!result.startsWith(path.resolve(base)+path.sep))throw new Error('배포 경로가 묶음을 벗어납니다.');
  return result;
}
async function countFiles(directory){
  let count=0;
  for(const entry of await readdir(directory,{withFileTypes:true})){
    if(entry.isSymbolicLink())throw new Error('배포 묶음에 심볼릭 링크가 있습니다.');
    count+=entry.isDirectory()?await countFiles(path.join(directory,entry.name)):1;
  }
  return count;
}
export async function verifyStagedDeployment(receiptPath='.local/deploy/static-stage.json'){
  const receipt=JSON.parse(await readFile(receiptPath,'utf8'));
  const configPath=path.join(receipt.bundle,'wrangler.json');
  if(path.resolve(receipt.config)!==path.resolve(configPath)||await hashFile(configPath)!==receipt.config_hash)throw new Error('배포 설정이 준비 후 변경됐습니다.');
  const config=JSON.parse(await readFile(configPath,'utf8'));
  const errors=validateDeployment(config,receipt);if(errors.length)throw new Error(errors.join('\n'));
  const manifestPath=path.join(receipt.bundle,'asset-manifest.json');
  if(await hashFile(manifestPath)!==receipt.manifest_hash)throw new Error('배포 파일 목록 해시가 일치하지 않습니다.');
  const files=JSON.parse(await readFile(manifestPath,'utf8'));
  if(!Array.isArray(files)||files.length!==receipt.count||new Set(files.map(e=>e.target)).size!==files.length)throw new Error('배포 파일 목록 개수가 일치하지 않습니다.');
  const client=path.join(receipt.bundle,'client');
  if(await countFiles(client)!==files.length)throw new Error('배포 묶음에 누락되거나 추가된 파일이 있습니다.');
  let cursor=0;
  await Promise.all(Array.from({length:4},async()=>{
    while(cursor<files.length){
      const entry=files[cursor++],file=inside(client,entry.target),info=await stat(file);
      if(!hashPattern.test(entry.sha256)||info.size!==entry.bytes||info.size>=24*1024*1024||await hashFile(file)!==entry.sha256)throw new Error(`배포 파일 검증 실패: ${entry.target}`);
    }
  }));
  if(await hashFile(path.join(client,'data','catalog.json'))!==receipt.catalog_hash)throw new Error('게시할 카탈로그가 선택한 릴리스와 다릅니다.');
  if(!receipt.worker_files?.length)throw new Error('검증할 Worker 코드가 없습니다.');
  if(new Set(receipt.worker_files.map(e=>e.target)).size!==receipt.worker_files.length||await countFiles(path.join(receipt.bundle,'worker'))!==receipt.worker_files.length)throw new Error('Worker 파일이 누락되거나 추가됐습니다.');
  for(const entry of receipt.worker_files){
    if(!entry.target.startsWith('worker/')||await hashFile(inside(receipt.bundle,entry.target))!==entry.sha256)throw new Error('Worker 코드가 준비 후 변경됐습니다.');
  }
  return {receipt,config,configPath};
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  try{
    const {receipt}=await verifyStagedDeployment(process.argv[2]);
    process.stdout.write(`정적 배포 묶음 확인: ${receipt.release_id}, ${receipt.count} files, 코드·지도 해시 일치.\n`);
  }catch(error){process.stderr.write(String(error)+'\n');process.exitCode=1;}
}
