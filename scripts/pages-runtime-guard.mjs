/** Requires the common VPS publisher lock; the provider does not offer remote CAS. */
const hash=/^[a-f0-9]{64}$/;
export async function assertExpectedPagesRuntime(expected,{fetcher=fetch}={}){
 if(!expected||!hash.test(expected.artifact_sha256??'')||typeof expected.release_id!=='string'||!expected.data)throw new Error('Expected verified app and data pin required');
 const response=await fetcher('https://korea-replay.pages.dev/api/v2/runtime',{cache:'no-store',redirect:'error',signal:AbortSignal.timeout(15000)});
 if(!response.ok)throw new Error('Current Pages runtime unavailable; publication stopped');
 const text=await response.text();if(text.length>65536)throw new Error('Runtime exceeds limit');
 let current;try{current=JSON.parse(text);}catch{throw new Error('Invalid current runtime');}
 if(current.schema_version!==2||current.project!=='korea-replay'||current.platform!=='cloudflare-pages'||current.artifact_sha256!==expected.artifact_sha256||current.release_id!==expected.release_id||['origin','manifest_path','manifest_sha256'].some(key=>current.data?.[key]!==expected.data[key]))throw new Error('Pages changed since staging; rebuild against current app and collection');
 return current;
}
/** Recheck after uploads, immediately before the production POST. Never retry deployment. */
export function guardPagesProduction(api,expected,options){
 return {...api,async deploy(project,form){
  if(project==='korea-replay'&&form.get('branch')==='main')await assertExpectedPagesRuntime(expected,options);
  return api.deploy(project,form);
 }};
}
