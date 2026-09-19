import {execFileSync} from 'node:child_process';
import {readFile,lstat} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const rules=[
  ['private-key',/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g],
  ['provider-token',/\b(?:github_pat_[a-zA-Z0-9_]{30,}|gh[pousr]_[a-zA-Z0-9]{30,}|AKIA[A-Z0-9]{16})\b/g],
  ['credential-literal',/(?:DATA_GO_KR_SERVICE_KEY|SEOUL_SUBWAY_API_KEY|CLOUDFLARE_API_TOKEN|oauth_token|service[Kk]ey|auth[Kk]ey)\s*[=:]\s*["']?([a-zA-Z0-9+/%=_-]{24,})/g],
  ['personal-service-url',/https?:\/\/[a-z0-9-]+\.[a-z0-9-]+-workers\.workers\.dev[^\s"'<>]*/g],
  ['local-user-path',/[A-Z]:(?:\\{1,2}|\/)Users(?:\\{1,2}|\/)[^\s"'<>/\\]+/g],
  ['personal-salutation',/[\p{Script=Hangul}]{2,4}님/gu],
  ['email',/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi],
];
export function scanPublicText(text){
  const result=[];
  for(const [category,pattern] of rules){pattern.lastIndex=0;let match;
    while((match=pattern.exec(text))){
      const value=match[1]??match[0];
      if(category==='credential-literal'&&/fixture|example|placeholder|replace|^your_|^test[-_]|^x+$|^0+$/i.test(value))continue;
      // URL userinfo used by security fixtures is not an email address.
      if(category==='email'&&/https?:\/\/[^/\s"'<>]*$/i.test(text.slice(Math.max(0,match.index-200),match.index)))continue;
      if(category==='email'&&/(?:@(?:example\.(?:com|org|net|invalid)|[^@]+\.invalid|users\.noreply\.github\.com)$|^(?:noreply|support|security|opensource|license|wrangler)@)/i.test(value))continue;
      result.push({category,line:text.slice(0,match.index).split('\n').length});
    }
  }
  return result;
}
function git(root,args,input){return execFileSync('git',['-C',root,...args],{input,encoding:input===undefined?'utf8':undefined,maxBuffer:256*1024*1024,windowsHide:true,stdio:['pipe','pipe','pipe']});}
function forbiddenFile(name){return /^(?:\.local|\.venv|node_modules|dist|\.wrangler|public\/data)(?:\/|$)/.test(name)
  ||/(?:^|\/)(?:\.env(?:\..*)?|\.dev\.vars(?:\..*)?|[^/]+\.(?:pem|key|p12|pfx|sqlite|sqlite3|db))$/.test(name)&&!/(?:\.example|\.template)$/.test(name);}
export async function auditPublicRepository(root=process.cwd()){
  const tracked=git(root,['ls-files','-z']).split('\0').filter(Boolean),untracked=git(root,['ls-files','--others','--exclude-standard','-z']).split('\0').filter(Boolean),findings=[];let currentBytes=0;
  for(const name of [...new Set([...tracked,...untracked])]){
    if(forbiddenFile(name))findings.push({scope:'working-tree',path:name,category:'private-or-generated-file'});
    const file=path.join(root,name);let info;try{info=await lstat(file);}catch{continue;}
    if(!info.isFile()||info.isSymbolicLink()){findings.push({scope:'working-tree',path:name,category:'nonregular-file'});continue;}
    currentBytes+=info.size;if(info.size>50*1024*1024)findings.push({scope:'working-tree',path:name,category:'github-large-file'});
    if(info.size<=4*1024*1024){const body=await readFile(file);if(!body.includes(0))for(const finding of scanPublicText(body.toString('utf8')))findings.push({scope:'working-tree',path:name,...finding});}
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
      if(!body.includes(0))for(const finding of scanPublicText(body.toString('utf8')))findings.push({scope:'history',object:id,path:objects.get(id)||'(historical blob)',...finding});
    }
  }
  const identities=git(root,['log','--all','--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x00']);
  for(const finding of scanPublicText(identities))findings.push({scope:'commit-metadata',category:finding.category});
  const licenses={};for(const name of ['LICENSE','THIRD_PARTY_NOTICES.md']){try{licenses[name]=(await readFile(path.join(root,name))).length>0;}catch{licenses[name]=false;}}
  return {schema_version:1,read_only:true,commits:Number(git(root,['rev-list','--count','--all']).trim()),tracked_files:tracked.length,untracked_candidate_files:untracked.length,candidate_bytes:currentBytes,historical_blobs:blobs.length,
    automatic_secret_findings:findings.filter(finding=>['private-key','provider-token','credential-literal','private-or-generated-file'].includes(finding.category)).length,
    privacy_review_findings:findings.filter(finding=>['personal-service-url','local-user-path','personal-salutation','email'].includes(finding.category)).length,
    licenses,findings,ready_for_public:findings.length===0&&Object.values(licenses).every(Boolean),
    limitation:'Pattern scan is not proof of secret absence. Review findings, all prospective untracked additions, upstream data licenses and GitHub secret scanning before publication.'};
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  try{const report=await auditPublicRepository();process.stdout.write(JSON.stringify(report,null,2)+'\n');if(!report.ready_for_public)process.exitCode=2;}
  catch{process.stderr.write('Public repository audit failed; no content or credential values were printed.\n');process.exitCode=1;}
}
