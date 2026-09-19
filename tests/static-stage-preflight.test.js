import {afterEach,describe,expect,it} from 'vitest';
import {mkdtemp,mkdir,readFile,writeFile,rm,symlink,truncate} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {BROKER_DEPLOYMENT_CONTRACT,verifyStagedDeployment} from '../scripts/deploy-preflight.mjs';

const release='pub-0123456789abcdef';
const temporary=[];
const sha=value=>createHash('sha256').update(value).digest('hex');
const json=value=>JSON.stringify(value);
async function put(file,value){await mkdir(path.dirname(file),{recursive:true});await writeFile(file,value);}
afterEach(async()=>{
  for(const directory of temporary.splice(0)){
    // Recursive cleanup is restricted to directories created by this fixture.
    if(path.dirname(path.resolve(directory))!==path.resolve(tmpdir())||!path.basename(directory).startsWith('korea-static-stage-'))throw new Error('Unsafe fixture cleanup path');
    await rm(directory,{recursive:true,force:true});
  }
});
async function fixture({broker=false}={}){
  const root=await mkdtemp(path.join(tmpdir(),'korea-static-stage-'));temporary.push(root);
  const bundle=path.join(root,'bundle'),client=path.join(bundle,'client');
  const catalog={schema_version:2,release_id:release,assets:[{id:'fixture',format:'geojson',url:'/data/scene.geojson'}],indexes:[]};
  const content={
    'index.html':'<!doctype html><title>Test fixture</title>',
    'data/catalog.json':json(catalog),
    'data/scene.geojson':json({type:'FeatureCollection',features:[]}),
    '_headers':'/data/catalog.json\n  Cache-Control: no-cache\n',
    '404.html':'<!doctype html><title>Missing test fixture</title>',
  };
  const manifest=[];
  for(const [target,value] of Object.entries(content)){await put(path.join(client,target),value);manifest.push({target,sha256:sha(value),bytes:Buffer.byteLength(value)});}
  const workerFiles=[];
  for(const [target,value] of Object.entries({'worker/index.js':"import {label} from './label.js'; export default {fetch(){return new Response(label);}}",'worker/label.js':"export const label='test fixture';"})){
    await put(path.join(bundle,target),value);workerFiles.push({target,sha256:sha(value)});
  }
  const config={name:'korea-replay',main:'./worker/index.js',no_bundle:true,
    compatibility_date:'2026-09-16',compatibility_flags:['nodejs_compat'],observability:{enabled:true},
    vars:{ENVIRONMENT:'production',DATA_STORAGE:'static',COLLECTORS_ENABLED:'false',STATIC_RELEASE_ID:release},
    assets:{directory:'./client',binding:'ASSETS',not_found_handling:'none',run_worker_first:['/api/*']},
    version_metadata:{binding:'CF_VERSION_METADATA'},workers_dev:true,preview_urls:true};
  if(broker){config.vars.LIVE_TRANSIT_MODE='broker';config.services=[{...BROKER_DEPLOYMENT_CONTRACT.broker}];}
  const configPath=path.join(bundle,'wrangler.json'),manifestPath=path.join(bundle,'asset-manifest.json'),receiptPath=path.join(root,'receipt.json');
  await put(configPath,json(config));await put(manifestPath,json(manifest));
  const receipt={mode:'static',complete:true,release_id:release,count:manifest.length,catalog_hash:sha(content['data/catalog.json']),
    audit:{passed:true,release_id:release},bundle,config:configPath,config_hash:sha(json(config)),manifest_hash:sha(json(manifest)),worker_files:workerFiles};
  if(broker){receipt.schema_version=2;receipt.deployment_contract=structuredClone(BROKER_DEPLOYMENT_CONTRACT);}
  await put(receiptPath,json(receipt));
  return {root,bundle,client,config,configPath,manifest,manifestPath,receipt,receiptPath,
    async saveReceipt(){await put(receiptPath,json(receipt));},
    async saveManifest(){await put(manifestPath,json(manifest));receipt.manifest_hash=sha(json(manifest));await this.saveReceipt();},
    async saveConfig(){await put(configPath,json(config));receipt.config_hash=sha(json(config));await this.saveReceipt();},
  };
}

describe('staged static deployment files',()=>{
  it('accepts a complete immutable local bundle with no storage or collector bindings',async()=>{
    const f=await fixture(),verified=await verifyStagedDeployment(f.receiptPath);
    expect(verified.configPath).toBe(f.configPath);expect(verified.receipt.count).toBe(5);
    expect(verified.config.vars.STATIC_RELEASE_ID).toBe(release);
  });
  it('rejects a data modification even when its file size is unchanged',async()=>{
    const f=await fixture(),file=path.join(f.client,'data/scene.geojson');
    const original=await readFile(file,'utf8');await writeFile(file,original.replace('FeatureCollection','featureCollection'));
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('배포 파일 검증 실패');
  });
  it('rejects a missing static file before upload',async()=>{
    const f=await fixture();await rm(path.join(f.client,'data/scene.geojson'));
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('누락되거나 추가');
  });
  it('rejects an extra static file before upload',async()=>{
    const f=await fixture();await put(path.join(f.client,'unexpected.txt'),'unexpected fixture');
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('누락되거나 추가');
  });
  it.each(['worker/index.js','worker/label.js'])('rejects modified Worker code: %s',async target=>{
    const f=await fixture();await writeFile(path.join(f.bundle,target),'export default "changed fixture";');
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('Worker 코드');
  });
  it('rejects a missing Worker dependency',async()=>{
    const f=await fixture();await rm(path.join(f.bundle,'worker/label.js'));
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow();
  });
  it('rejects an additional Worker module not listed in the receipt',async()=>{
    const f=await fixture();await put(path.join(f.bundle,'worker/unlisted.js'),'export const extra=true;');
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('Worker 파일');
  });
  it('rejects duplicated Worker entries',async()=>{
    const f=await fixture();f.receipt.worker_files[1]={...f.receipt.worker_files[0]};await f.saveReceipt();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('Worker 파일');
  });
  it.each(['client','worker'])('rejects a directory link in the %s bundle',async directory=>{
    const f=await fixture(),external=path.join(f.root,'external-fixture');await put(path.join(external,'file.txt'),'external test fixture');
    await symlink(external,path.join(f.bundle,directory,'linked-directory'),'junction');
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('심볼릭 링크');
  });
  it('rejects a changed config even when the altered file is valid JSON',async()=>{
    const f=await fixture();await writeFile(f.configPath,json({...f.config,name:'different-fixture-worker'}));
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('배포 설정');
  });
  it.each([
    ['worker name',config=>{config.name='unrelated-fixture-worker';}],
    ['main path',config=>{config.main='../unverified-worker.js';}],
    ['asset directory',config=>{config.assets.directory='../unverified-client';}],
    ['asset binding',config=>{config.assets.binding='OTHER_FIXTURE';}],
    ['post-audit bundling',config=>{config.no_bundle=false;}],
  ])('rejects an audited config with the wrong %s',async(_label,change)=>{
    const f=await fixture();change(f.config);await f.saveConfig();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('검증한 client/worker');
  });
  it('rejects a changed asset manifest instead of trusting its contents',async()=>{
    const f=await fixture();f.manifest[0].sha256='0'.repeat(64);await writeFile(f.manifestPath,json(f.manifest));
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('목록 해시');
  });
  it('rejects a release mismatch between the generated Worker config and the data receipt',async()=>{
    const f=await fixture();f.config.vars.STATIC_RELEASE_ID='pub-fedcba9876543210';await f.saveConfig();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('코드와 지도 릴리스');
  });
  it('rejects a release mismatch in the audit receipt',async()=>{
    const f=await fixture();f.receipt.audit.release_id='pub-fedcba9876543210';await f.saveReceipt();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('완료된 정적 배포 묶음');
  });
  it.each(['../outside.txt','/outside.txt','data/../../outside.txt','data\\scene.geojson','data//scene.geojson','data/./scene.geojson'])('rejects unsafe asset paths even when the manifest hash is recomputed: %s',async target=>{
    const f=await fixture();await put(path.join(f.root,'outside.txt'),'external fixture');
    f.manifest[0].target=target;await f.saveManifest();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow(/경로/);
  });
  it('rejects a Worker path which escapes its bundle',async()=>{
    const f=await fixture();f.receipt.worker_files[0].target='worker/../../outside.txt';await f.saveReceipt();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow(/경로/);
  });
  it('rejects duplicate target entries and inconsistent file counts',async()=>{
    const f=await fixture();f.manifest[0]={...f.manifest[1]};await f.saveManifest();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('목록 개수');
  });
  it('rejects a catalog pointer hash that differs from the receipt',async()=>{
    const f=await fixture();f.receipt.catalog_hash='1'.repeat(64);await f.saveReceipt();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('선택한 릴리스');
  });
  it('rejects a file at the conservative 24 MiB threshold',async()=>{
    const f=await fixture(),entry=f.manifest.find(item=>item.target==='data/scene.geojson');
    entry.bytes=24*1024*1024;await truncate(path.join(f.client,entry.target),entry.bytes);await f.saveManifest();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('배포 파일 검증 실패');
  });
  it('rejects an asset count above the free deployment ceiling',async()=>{
    const f=await fixture();f.receipt.count=20001;await f.saveReceipt();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('20,000개 이하');
  });
  it.each(['r2_buckets','d1_databases'])('rejects accidentally enabled paid storage: %s',async binding=>{
    const f=await fixture();f.config[binding]=[{binding:'TEST_FIXTURE'}];await f.saveConfig();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('무료 정적 배포');
  });
  it('rejects newly enabled scheduled collection',async()=>{
    const f=await fixture();f.config.triggers={crons:['* * * * *']};await f.saveConfig();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('무료 정적 배포');
  });
  it('accepts the exact broker dependency and preserves its receipt contract',async()=>{
    const f=await fixture({broker:true}),verified=await verifyStagedDeployment(f.receiptPath);
    expect(verified.config.vars.LIVE_TRANSIT_MODE).toBe('broker');
    expect(verified.config.services).toEqual([{binding:'LIVE_TRANSIT_BROKER',service:'korea-replay-live-broker'}]);
    expect(verified.receipt.deployment_contract).toEqual(BROKER_DEPLOYMENT_CONTRACT);
  });
  it.each([
    ['missing service',f=>{delete f.config.services;}],
    ['wrong binding',f=>{f.config.services[0].binding='OTHER_BROKER';}],
    ['wrong target',f=>{f.config.services[0].service='another-worker';}],
    ['service environment',f=>{f.config.services[0].environment='preview';}],
    ['service entrypoint',f=>{f.config.services[0].entrypoint='AnotherHandler';}],
    ['extra service',f=>{f.config.services.push({binding:'OTHER',service:'other-worker'});}],
    ['duplicate service',f=>{f.config.services.push({...f.config.services[0]});}],
    ['environment override',f=>{f.config.env={production:{vars:{LIVE_TRANSIT_MODE:'direct'}}};}],
    ['missing mode',f=>{delete f.config.vars.LIVE_TRANSIT_MODE;}],
    ['direct bypass',f=>{f.config.vars.LIVE_TRANSIT_MODE='direct';}],
    ['extra variable',f=>{f.config.vars.LIVE_TRANSIT_BROKER='https://other.invalid';}],
    ['map state binding',f=>{f.config.durable_objects={bindings:[{name:'STATE',class_name:'TransitCoordinator'}]};}],
    ['map migration',f=>{f.config.migrations=[{tag:'v1',new_sqlite_classes:['TransitCoordinator']}];}],
    ['changed contract target',f=>{f.receipt.deployment_contract.broker.service='another-worker';}],
    ['missing contract',f=>{delete f.receipt.deployment_contract;}],
    ['stripped broker fields',f=>{delete f.receipt.deployment_contract;delete f.config.vars.LIVE_TRANSIT_MODE;delete f.config.services;}],
    ['legacy receipt with new contract',f=>{f.receipt.schema_version=1;}],
    ['null contract',f=>{f.receipt.deployment_contract=null;}],
    ['unknown contract version',f=>{f.receipt.deployment_contract.schema_version=2;}],
  ])('rejects a broker contract violation even after rehashing config: %s',async(_label,change)=>{
    const f=await fixture({broker:true});change(f);await f.saveConfig();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow(/브로커|과거|env/);
  });
  it('does not mistake an unaudited broker dependency for a legacy archive',async()=>{
    const f=await fixture();f.config.services=[{binding:'LIVE_TRANSIT_BROKER',service:'korea-replay-live-broker'}];await f.saveConfig();
    await expect(verifyStagedDeployment(f.receiptPath)).rejects.toThrow('과거 배포');
  });
});
