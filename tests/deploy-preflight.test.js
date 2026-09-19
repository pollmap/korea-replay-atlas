import {expect,test} from 'vitest';
import {validateDeployment} from '../scripts/deploy-preflight.mjs';

const release='pub-0123456789abcdef';
const config={name:'korea-replay',main:'./worker/index.js',no_bundle:true,vars:{ENVIRONMENT:'production',DATA_STORAGE:'static',COLLECTORS_ENABLED:'false',STATIC_RELEASE_ID:release},assets:{directory:'./client',binding:'ASSETS',not_found_handling:'none',run_worker_first:['/api/*']},version_metadata:{binding:'CF_VERSION_METADATA'},preview_urls:true};
const plan={release_id:release,count:18000,catalog_hash:'a'.repeat(64),mode:'static',complete:true,audit:{passed:true,release_id:release}};
test('missing production assets and a staged release fail before deployment',()=>{
  expect(validateDeployment({vars:{ENVIRONMENT:'development'}},null).length).toBeGreaterThan(0);
});
test('a complete static bundle needs neither R2 nor D1',()=>{
  expect(validateDeployment(config,plan)).toEqual([]);
  expect(validateDeployment(config,{...plan,audit:{passed:true,release_id:'pub-old'}})).toHaveLength(1);
});
test('partial uploads, quota excess and missing-file SPA fallbacks fail closed',()=>{
  expect(validateDeployment(config,{...plan,complete:false})).toHaveLength(1);
  expect(validateDeployment(config,{...plan,count:20001})).toHaveLength(1);
  expect(validateDeployment({...config,assets:{...config.assets,not_found_handling:'single-page-application'}},plan)).toHaveLength(1);
});
test('accidental paid storage and automatic collection are rejected by the free path',()=>{
  expect(validateDeployment({...config,r2_buckets:[{binding:'DATA',bucket_name:'data'}]},plan)).toHaveLength(1);
  expect(validateDeployment({...config,triggers:{crons:['* * * * *']}},plan)).toHaveLength(1);
});

test('version sharing requires runtime metadata and an enabled immutable preview',()=>{
  expect(validateDeployment({...config,version_metadata:undefined},plan)).toHaveLength(1);
  expect(validateDeployment({...config,version_metadata:{binding:'ANOTHER_BINDING'}},plan)).toHaveLength(1);
  expect(validateDeployment({...config,preview_urls:false},plan)).toHaveLength(1);
});
