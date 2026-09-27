import {readFileSync,mkdtempSync,mkdirSync,writeFileSync,rmSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {describe,expect,it} from 'vitest';
import {scanPublicText,scanPublicFile,auditPublicRepository} from '../scripts/pages-public-audit.mjs';
describe('public audit redaction',()=>{
  it('reports only categories and line numbers, never matched credentials or personal values',()=>{
    const credential='gh'+'p_'+'A'.repeat(36),body='credential='+credential+'\nContact individual'+'@mail.test';
    const report=scanPublicText(body);expect(report.some(row=>row.category==='provider-token')).toBe(true);expect(JSON.stringify(report)).not.toContain(credential);expect(JSON.stringify(report)).not.toContain('@');
  });
  it('does not mistake explicit placeholders and public noreply addresses for credentials',()=>{
    expect(scanPublicText('DATA_GO_KR_SERVICE_KEY="fixture-not-a-real-credential"\n123+developer@users.noreply.github.com')).toEqual([]);
  });
  it('detects archive broker credentials without disclosing their value',()=>{
    const value='a1'.repeat(32),report=scanPublicText('PROPERTY_ARCHIVE_BROKER_TOKEN="'+value+'"');
    expect(report).toEqual([{category:'credential-literal',line:1}]);
    expect(JSON.stringify(report)).not.toContain(value);
  });
});

const hashes=[
  '0c920530eee79824b7945b1d57fc59c6b63db7a9baf0f79c298dbf0977614799',
  '1882e374a318335c1852d288d5ad864c6e06be4d1044856fe3803aa83c76c686',
  '1e55f3dbe201b9ca739978c190025fa9700152e6415e6954c9b6d7cb53855ce9',
  '4c25ac2b75eaab6d5a58e99c2a30f43b4a07ae409581def039c7f12abce7f283',
  '8c3ec12d45cbfad3000420b447042fe7dc7458abb2d84dea34c6ddfef46cb2b5',
];
const sourceName=hash=>`src/data/property-poi/poi-${hash}.json`;
const sourceBody=hash=>readFileSync(new URL('../'+sourceName(hash),import.meta.url));
it('limits reviewed names to the exact five source hashes and seven original record identities',()=>{
  const reviewed=[];
  for(const hash of hashes){
    const body=sourceBody(hash),report=scanPublicFile(body,sourceName(hash));
    expect(report.findings).toEqual([]);reviewed.push(...report.reviewedFacilityNames);
    expect(scanPublicText(body.toString('utf8')).filter(row=>row.category==='personal-salutation').length).toBe(report.reviewedFacilityNames.length);
    const copied=scanPublicFile(body,'other/'+sourceName(hash));
    expect(copied.reviewedFacilityNames).toEqual([]);expect(copied.findings.length).toBeGreaterThan(0);
  }
  expect(reviewed).toHaveLength(7);expect(new Set(reviewed.map(row=>row.recordId)).size).toBe(7);
  expect(reviewed.find(row=>row.recordId==='way/712200046')?.name).toBe('\ud587\ub2d8\ub2ec\ub2d8 \uacf5\uc6d0'.replace(/\\u([0-9a-f]{4})/g,(_,value)=>String.fromCharCode(parseInt(value,16))));
});
it('rejects name, record identity and field substitutions rather than trusting a claimed filename hash',()=>{
  const hash=hashes[0],body=sourceBody(hash);
  for(const change of ['name','id','field','extra-field']){
    const doc=JSON.parse(body),record=doc.records.find(row=>row.id==='way/712200046');
    if(change==='name')record.name+=' changed';
    if(change==='id')record.id='way/1';
    if(change==='field'){record.address=record.name;record.name='Park';}
    if(change==='extra-field')record.address=record.name;
    const result=scanPublicFile(Buffer.from(JSON.stringify(doc)+'\n'),sourceName(hash));
    expect(result.reviewedFacilityNames).toEqual([]);
    expect(result.findings.some(row=>row.category==='personal-salutation')).toBe(true);
  }
  const unknown=scanPublicFile(body,sourceName('0'.repeat(64)));
  expect(unknown.reviewedFacilityNames).toEqual([]);expect(unknown.findings).not.toEqual([]);
});
it('continues detecting secrets, addresses and other personal fields in modified source files',()=>{
  const hash=hashes[0],doc=JSON.parse(sourceBody(hash));
  const record=doc.records.find(row=>row.id==='way/712200046');
  record.token='gh'+'p_'+'A'.repeat(36);record.contact='individual'+'@mail.test';
  record.localPath=['C:','Users','PrivateAccount','file'].join('/');
  const report=scanPublicFile(Buffer.from(JSON.stringify(doc)),sourceName(hash));
  expect(report.reviewedFacilityNames).toEqual([]);
  expect(report.findings.map(row=>row.category)).toEqual(expect.arrayContaining(['personal-salutation','provider-token','email','local-user-path']));
  expect(JSON.stringify(report.findings)).not.toContain(record.token);expect(JSON.stringify(report.findings)).not.toContain(record.contact);
});
it('applies identical reviewed-name scope to working files and historical Git blobs with explicit reporting',async()=>{
  const root=mkdtempSync(path.join(tmpdir(),'public-audit-poi-'));
  try{
    const git=args=>execFileSync('git',['-C',root,...args],{stdio:'pipe',windowsHide:true});
    git(['init','--initial-branch=main']);git(['config','user.name','Audit Fixture']);git(['config','user.email','audit@example.com']);
    const name=sourceName(hashes[0]);mkdirSync(path.dirname(path.join(root,name)),{recursive:true});writeFileSync(path.join(root,name),sourceBody(hashes[0]));
    writeFileSync(path.join(root,'LICENSE'),'MIT');writeFileSync(path.join(root,'THIRD_PARTY_NOTICES.md'),'OSM ODbL-1.0');
    git(['add','.']);git(['commit','-m','Add reviewed source fixture']);
    const report=await auditPublicRepository(root);
    expect(report.ready_for_public).toBe(true);expect(report.findings).toEqual([]);
    expect(report.reviewedFacilityNameCount).toBe(1);expect(report.reviewedFacilityNameOccurrences).toBe(2);
    expect(report.reviewedFacilityNames.map(row=>row.scope).sort()).toEqual(['history','working-tree']);
    expect(report.reviewedFacilityNames.every(row=>row.sha256===hashes[0]&&row.recordId==='way/712200046')).toBe(true);
  }finally{rmSync(root,{recursive:true,force:true});}
},30_000);
