import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile,lstat} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const rules=[
  ['private-key',/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g],
  ['provider-token',/\b(?:github_pat_[a-zA-Z0-9_]{30,}|gh[pousr]_[a-zA-Z0-9]{30,}|AKIA[A-Z0-9]{16})\b/g],
  ['credential-literal',/(?:DATA_GO_KR_SERVICE_KEY|SEOUL_SUBWAY_API_KEY|CLOUDFLARE_API_TOKEN|PROPERTY_ARCHIVE_BROKER_TOKEN|oauth_token|service[Kk]ey|auth[Kk]ey)\s*[=:]\s*["']?([a-zA-Z0-9+/%=_-]{24,})/g],
  ['personal-service-url',/https?:\/\/[a-z0-9-]+\.[a-z0-9-]+-workers\.workers\.dev[^\s"'<>]*/g],
  ['local-user-path',/[A-Z]:(?:\\{1,2}|\/)Users(?:\\{1,2}|\/)[^\s"'<>/\\]+/g],
  ['personal-salutation',/[\p{Script=Hangul}]{2,4}님/gu],
  ['email',/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi],
];
// These seven OSM facility names were reviewed in five immutable source files.
// Escaped literals keep the audit policy itself distinct from source-name matches.
const reviewedNames=new Map([
  ['0c920530eee79824b7945b1d57fc59c6b63db7a9baf0f79c298dbf0977614799',[
    ['way/712200046','\ud587\ub2d8\ub2ec\ub2d8 \uacf5\uc6d0']]],
  ['1882e374a318335c1852d288d5ad864c6e06be4d1044856fe3803aa83c76c686',[
    ['node/13911615203','CU \ubd81\uc544\ud604\ud587\ub2d8\uc810']]],
  ['1e55f3dbe201b9ca739978c190025fa9700152e6415e6954c9b6d7cb53855ce9',[
    ['node/9796362739','\ud558\ub098\ub2d8\uc758\uad50\ud68c'],['node/9796362747','\ud558\ub098\ub2d8\uc758\uad50\ud68c']]],
  ['4c25ac2b75eaab6d5a58e99c2a30f43b4a07ae409581def039c7f12abce7f283',[
    ['node/13997824994','\ube75\uc9d1\uacf0\uc120\uc0dd\ub2d8']]],
  ['8c3ec12d45cbfad3000420b447042fe7dc7458abb2d84dea34c6ddfef46cb2b5',[
    ['node/9362949543','\ud558\ub098\ub2d8\uc758\uad50\ud68c\uc55e'],['node/9362949544','\ud558\ub098\ub2d8\uc758\uad50\ud68c\uc55e']]],
]);
// Official reported complex names; only these name tokens in one pinned index are reviewed.
const reviewedComplexRows=[["molit-apt:26290:26290-1916","\uc6a9\ud638\ub3d9\uc77c\uc2e0\ub2d8(\uf9f4)\u2161"],["molit-apt:28200:28200-163","\uc778\ud3c9\uc2a4\ud398\uc2a4\ud790\ud587\ub2d8\ub9c8\uc744"],["molit-apt:30170:30170-82","\uc601\uc9c4\ud587\ub2d8"]];
function scanText(text,salutationSpans=[]){
  const result=[];
  for(const [category,pattern] of rules){pattern.lastIndex=0;let match;
    while((match=pattern.exec(text))){
      const value=match[1]??match[0];
      if(category==='personal-salutation'&&salutationSpans.some(span=>match.index>=span.start&&match.index+match[0].length<=span.end))continue;
      if(category==='credential-literal'&&/fixture|example|placeholder|replace|^your_|^test[-_]|^x+$|^0+$/i.test(value))continue;
      // URL userinfo used by security fixtures is not an email address.
      if(category==='email'&&/https?:\/\/[^/\s"'<>]*$/i.test(text.slice(Math.max(0,match.index-200),match.index)))continue;
      if(category==='email'&&/(?:@(?:example\.(?:com|org|net|invalid)|[^@]+\.invalid|users\.noreply\.github\.com)$|^(?:noreply|support|security|opensource|license|wrangler)@)/i.test(value))continue;
      result.push({category,line:text.slice(0,match.index).split('\n').length});
    }
  }
  return result;
}
export function scanPublicText(text){return scanText(text);}
export function scanPublicFile(body,name){
  const text=body.toString('utf8'),spans=[],reviewedFacilityNames=[],reviewedComplexNames=[];
  const matched=/^src\/data\/property-poi\/poi-([a-f0-9]{64})\.json$/.exec(name);
  const expected=matched&&reviewedNames.get(matched[1]);
  if(expected&&createHash('sha256').update(body).digest('hex')===matched[1]){
    let data;try{data=JSON.parse(text);}catch{/* Fail closed: ordinary scanning still runs. */}
    if(data?.schema===1&&Array.isArray(data.records))for(const [recordId,facilityName] of expected){
      const matches=data.records.filter(row=>row?.id===recordId&&row?.name===facilityName);
      if(matches.length!==1)continue;
      // Canonical JSON's exact record and exact top-level name token identify its
      // original byte location. Any changed serialization receives normal scanning.
      const record=JSON.stringify(matches[0]),start=text.indexOf(record),token='"name":'+JSON.stringify(facilityName),at=record.indexOf(token);
      if(start<0||text.indexOf(record,start+record.length)!==-1||at<0||record.indexOf(token,at+token.length)!==-1)continue;
      spans.push({start:start+at,end:start+at+token.length});
      reviewedFacilityNames.push({sha256:matched[1],recordId,name:facilityName});
    }
  }
  if(name==='src/data/property-search-index-ceeff63959643461.json'&&createHash('sha256').update(body).digest('hex')==='5472abd27fa4eb3c223892eab17a44397428873c7bccb37bb65e8a30dc42114c'){
    let data;try{data=JSON.parse(text);}catch{/* Normal scanning remains enabled. */}
    if(data?.schema_version===1&&data.kind==='property-complex-search-index'&&data.property_release_id==='property-ceeff63959643461'&&Array.isArray(data.rows))for(const [recordId,complexName] of reviewedComplexRows){
      const rows=data.rows.filter(row=>Array.isArray(row)&&row[0]===recordId&&row[1]===complexName);
      if(rows.length!==1)continue;
      const record=JSON.stringify(rows[0]),start=text.indexOf(record),token=JSON.stringify(complexName),at=record.indexOf(token);
      if(start<0||text.indexOf(record,start+record.length)!==-1||at<0||record.indexOf(token,at+token.length)!==-1)continue;
      spans.push({start:start+at,end:start+at+token.length});reviewedComplexNames.push({recordId,name:complexName});
    }
  }
  return {findings:scanText(text,spans),reviewedFacilityNames,reviewedComplexNames};
}
function git(root,args,input){return execFileSync('git',['-C',root,...args],{input,encoding:input===undefined?'utf8':undefined,maxBuffer:256*1024*1024,windowsHide:true,stdio:['pipe','pipe','pipe']});}
function forbiddenFile(name){return /^(?:\.local|\.venv|node_modules|dist|\.wrangler|public\/data)(?:\/|$)/.test(name)
  ||/(?:^|\/)(?:\.env(?:\..*)?|\.dev\.vars(?:\..*)?|[^/]+\.(?:pem|key|p12|pfx|sqlite|sqlite3|db))$/.test(name)&&!/(?:\.example|\.template)$/.test(name);}
export async function auditPublicRepository(root=process.cwd()){
  const tracked=git(root,['ls-files','-z']).split('\0').filter(Boolean),untracked=git(root,['ls-files','--others','--exclude-standard','-z']).split('\0').filter(Boolean),findings=[],reviewedFacilityNames=[],reviewedComplexNames=[];let currentBytes=0;
  const scan=(body,context)=>{const result=scanPublicFile(body,context.path);for(const finding of result.findings)findings.push({...context,...finding});for(const reviewed of result.reviewedFacilityNames)reviewedFacilityNames.push({...context,...reviewed});for(const reviewed of result.reviewedComplexNames)reviewedComplexNames.push({...context,...reviewed});};
  for(const name of [...new Set([...tracked,...untracked])]){
    if(forbiddenFile(name))findings.push({scope:'working-tree',path:name,category:'private-or-generated-file'});
    const file=path.join(root,name);let info;try{info=await lstat(file);}catch{continue;}
    if(!info.isFile()||info.isSymbolicLink()){findings.push({scope:'working-tree',path:name,category:'nonregular-file'});continue;}
    currentBytes+=info.size;if(info.size>50*1024*1024)findings.push({scope:'working-tree',path:name,category:'github-large-file'});
    if(info.size<=4*1024*1024){const body=await readFile(file);if(!body.includes(0))scan(body,{scope:'working-tree',path:name});}
  }
  const objectLines=git(root,['rev-list','--objects','--all']).trim().split('\n').filter(Boolean),objects=new Map();
  for(const line of objectLines){const split=line.indexOf(' '),id=split<0?line:line.slice(0,split);objects.set(id,split<0?'':line.slice(split+1));}
  const checks=git(root,['cat-file','--batch-check=%(objectname) %(objecttype) %(objectsize)'],Buffer.from([...objects.keys()].join('\n')+'\n')).toString('utf8');
  const blobs=[];for(const line of checks.trim().split('\n')){const [id,type,bytes]=line.split(' ');if(type!=='blob')continue;const name=objects.get(id)||'(historical blob)';
    if(forbiddenFile(name))findings.push({scope:'history',object:id,path:name,category:'private-or-generated-file'});
    if(Number(bytes)>50*1024*1024)findings.push({scope:'history',object:id,path:name,category:'github-large-file'});
    if(Number(bytes)<=4*1024*1024)blobs.push(id);
  }
  if(blobs.length){
    const output=git(root,['cat-file','--batch'],Buffer.from(blobs.join('\n')+'\n'));let offset=0;
    for(const id of blobs){const end=output.indexOf(10,offset),header=output.subarray(offset,end).toString('utf8').split(' '),size=Number(header[2]);
      if(header[0]!==id||header[1]!=='blob'||!Number.isSafeInteger(size))throw new Error('Git object audit framing failed');
      const body=output.subarray(end+1,end+1+size);offset=end+1+size+1;
      if(!body.includes(0))scan(body,{scope:'history',object:id,path:objects.get(id)||'(historical blob)'});
    }
  }
  const identities=git(root,['log','--all','--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x00']);
  for(const finding of scanPublicText(identities))findings.push({scope:'commit-metadata',category:finding.category});
  const licenses={};for(const name of ['LICENSE','THIRD_PARTY_NOTICES.md']){try{licenses[name]=(await readFile(path.join(root,name))).length>0;}catch{licenses[name]=false;}}
  return {schema_version:1,read_only:true,commits:Number(git(root,['rev-list','--count','--all']).trim()),tracked_files:tracked.length,untracked_candidate_files:untracked.length,candidate_bytes:currentBytes,historical_blobs:blobs.length,
    automatic_secret_findings:findings.filter(finding=>['private-key','provider-token','credential-literal','private-or-generated-file'].includes(finding.category)).length,
    privacy_review_findings:findings.filter(finding=>['personal-service-url','local-user-path','personal-salutation','email'].includes(finding.category)).length,
    reviewedComplexNames,
    reviewedFacilityNames,reviewedFacilityNameCount:new Set(reviewedFacilityNames.map(row=>row.sha256+':'+row.recordId)).size,reviewedFacilityNameOccurrences:reviewedFacilityNames.length,
    licenses,findings,ready_for_public:findings.length===0&&Object.values(licenses).every(Boolean),
    limitation:'Pattern scan is not proof of secret absence. Review findings, all prospective untracked additions, upstream data licenses and GitHub secret scanning before publication.'};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  try{const report=await auditPublicRepository();process.stdout.write(JSON.stringify(report,null,2)+'\n');if(!report.ready_for_public)process.exitCode=2;}
  catch{process.stderr.write('Public repository audit failed; no content or credential values were printed.\n');process.exitCode=1;}
}
