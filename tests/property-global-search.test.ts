import {describe,it,expect,afterEach,vi} from 'vitest';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {parseSearchIndex,searchComplexIndex,currentSearchReply} from '../src/property-global-search';
import {fetchPinnedPropertySearchJson} from '../src/atlas-client';
const release='property-ceeff63959643461',value=JSON.parse(readFileSync('src/data/property-search-index-ceeff63959643461.json','utf8')),index=parseSearchIndex(value,release);
afterEach(()=>vi.unstubAllGlobals());
describe('source-pinned nine-region apartment search',()=>{
 it('covers every published in-scope complex without acquiring other regions',()=>{expect(index).toHaveLength(29497);expect(value.regions).toHaveLength(112);expect(new Set(index.map(row=>row.complex.lawd_code)).size).toBe(111);});
 it('finds another region directly and keeps its exact ID and full address',()=>{const rows=searchComplexIndex(index,'헬리오시티','41171');expect(rows[0]).toMatchObject({id:'molit-apt:11710:11710-8865',region_name:'서울특별시 송파구',legal_dong_name:'가락동'});});
 it('matches Korean decomposed spelling and tokenized full addresses',()=>{expect(searchComplexIndex(index,'헬리오시티'.normalize('NFD'),'')[0].id).toBe('molit-apt:11710:11710-8865');expect(searchComplexIndex(index,'송파 가락동 헬리오','')[0].id).toBe('molit-apt:11710:11710-8865');});
 it('keeps same-name complexes separate and caps results',()=>{const rows=searchComplexIndex(index,'푸르지오','');expect(rows.length).toBe(8);expect(new Set(rows.map(row=>row.id)).size).toBe(8);expect(rows.every(row=>row.region_name)).toBe(true);});
 it('ranks exact names before partial names and never infers coordinates',()=>{expect(searchComplexIndex(index,'헬리오시티','')[0].name).toBe('헬리오시티');expect(index.every(row=>!('position' in row.complex))).toBe(true);expect(searchComplexIndex(index,'x','')).toEqual([]);});
 it('rejects version, region, duplicate ID and row schema corruption',()=>{
  expect(()=>parseSearchIndex(value,'property-wrong')).toThrow();
  for(const mutate of [(v:typeof value)=>v.regions.pop(),(v:typeof value)=>v.regions[0][0]='47230',(v:typeof value)=>v.rows[1]=v.rows[0],(v:typeof value)=>v.rows[0][2]='47230',(v:typeof value)=>v.rows[0][3]=3]){const v=structuredClone(value);mutate(v);expect(()=>parseSearchIndex(v,release)).toThrow();}
 });
 it('does not accept a stale result after query changes or a worker restarts',()=>{expect(currentSearchReply({sequence:1,query:'헬리오'},2,'헬리오')).toBe(false);expect(currentSearchReply({sequence:2,query:'헬리오'},2,'푸르지오')).toBe(false);expect(currentSearchReply({sequence:2,query:'푸르지오'},2,'푸르지오')).toBe(true);});
 it('checks the asset hash and keeps same-origin paths and size bounded',async()=>{
  vi.stubGlobal('location',{origin:'https://example.com'});const body='{"case":"global-search"}',ref={sha256:createHash('sha256').update(body).digest('hex'),bytes:Buffer.byteLength(body)},url='/assets/property-search-index-ceeff63959643461-abcdefgh.json';vi.stubGlobal('fetch',vi.fn(async()=>new Response(body)));
  expect(await fetchPinnedPropertySearchJson(url,ref,new AbortController().signal)).toEqual({case:'global-search'});
  for(const bad of ['https://foreign.example'+url,url+'?latest=1','/assets/arbitrary.json'])await expect(fetchPinnedPropertySearchJson(bad,ref,new AbortController().signal)).rejects.toThrow();
  await expect(fetchPinnedPropertySearchJson(url,{...ref,bytes:9*1024**2},new AbortController().signal)).rejects.toThrow();
  await expect(fetchPinnedPropertySearchJson(url,{...ref,sha256:'a'.repeat(64)},new AbortController().signal)).rejects.toThrow('내용');
 });
});
