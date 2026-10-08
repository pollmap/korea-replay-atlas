import {expect,it} from 'vitest';
import {propertyReleaseArchiveUrl} from '../src/property-release-archive';
import {assertPinnedDeploymentV2} from '../shared/runtime-v2';

const current='property-1111111111111111',previous='property-eea48453a819f517';
const hash=`#regionCode=11410&trade=sale&month=202608&historyMonths=120&propertyRelease=${previous}&complex=molit-apt%3A11410%3Atest&area=84-band`;
const origin='https://korea-replay.pages.dev/';
it('preserves the September 29 release when the PC publication advances',()=>{
  const release='property-8deba5b9951e48da',state=hash.replace(previous,release);
  const result=new URL(propertyReleaseArchiveUrl(origin+state,current)!);
  expect(result.origin).toBe('https://7ae4ec3d.korea-replay.pages.dev');
  expect(result.hash).toBe(state);
  expect(()=>assertPinnedDeploymentV2(result.searchParams.get('deployment'),{
    schema_version:2,platform:'cloudflare-pages',project:'korea-replay',release_id:'pub-b71d244ced0bff39',
    artifact_sha256:'235848135096b7f47d0468a5688663a323364a7a87c4e74b6bc5dff488d64b72',
    snapshot:{origin:result.origin,hash:'7ae4ec3d'},data:{origin:'https://126c6720.korea-replay-data.pages.dev',manifest_path:'/data/atlas/atlas-be6c95e367929558/manifest.json',manifest_sha256:'5e6dffa4f5c86f14b91c2df7f8d0a90cdc1944b6ff98924dc833ea6b7d0b0537'},
  },result.origin)).not.toThrow();
});
it('restores the previous public property release to its verified immutable snapshot',()=>{
  const release='property-87d1c67336e97209';
  const state=hash.replace(previous,release);
  const result=new URL(propertyReleaseArchiveUrl(origin+state,current)!);
  expect(result.origin).toBe('https://0c88b86f.korea-replay.pages.dev');
  expect(result.hash).toBe(state);
  expect(()=>assertPinnedDeploymentV2(result.searchParams.get('deployment'),{
    schema_version:2,platform:'cloudflare-pages',project:'korea-replay',release_id:'pub-b71d244ced0bff39',
    artifact_sha256:'1f4d796c585e6fd2bc2a031685ad3cfad4e176fa5143e8146f59ce8ecb2886c2',
    snapshot:{origin:result.origin,hash:'0c88b86f'},data:{origin:'https://e593bf43.korea-replay-data.pages.dev',manifest_path:'/data/atlas/atlas-82335e2c9d48f0e3/manifest.json',manifest_sha256:'8657ceaad96ec1a019af1947a564fc7c601ede2e059106919d28150db70b27b0'},
  },result.origin)).not.toThrow();
});
it('preserves the September 26 property release and its exact data deployment after publication advances',()=>{
  const release='property-54bf1817fdcc7bd9';
  const state=hash.replace(previous,release);
  const result=new URL(propertyReleaseArchiveUrl(`${origin}?qa=mobile-public${state}`,current)!);
  expect(result.origin).toBe('https://a77a2fcd.korea-replay.pages.dev');
  expect(result.hash).toBe(state);expect(result.searchParams.get('qa')).toBe('mobile-public');
  expect(()=>assertPinnedDeploymentV2(result.searchParams.get('deployment'),{
    schema_version:2,platform:'cloudflare-pages',project:'korea-replay',release_id:'pub-b71d244ced0bff39',
    artifact_sha256:'e78aa32db267d446a438963056663a3cb3ecc4427e0d9159bdad4266cbed33b0',
    snapshot:{origin:result.origin,hash:'a77a2fcd'},data:{origin:'https://65ab75c2.korea-replay-data.pages.dev',manifest_path:'/data/atlas/atlas-19a1b429ea2ce399/manifest.json',manifest_sha256:'06d77244a0e0876529a4a9016f21e2557a0e4d2fb1a9e8e9f4c69c822c31ab7f'},
  },result.origin)).not.toThrow();
  expect(propertyReleaseArchiveUrl(origin+state,release)).toBeNull();
  expect(propertyReleaseArchiveUrl(result.href,current)).toBeNull();
  expect(propertyReleaseArchiveUrl(`${origin}${state}&release=pub-other`,current)).toBeNull();
});
it('restores a known old property share to its exact immutable deployment and preserves hash state',()=>{
  const result=new URL(propertyReleaseArchiveUrl(`${origin}?measure=1${hash}`,current)!);
  expect(result.origin).toBe('https://ba3762eb.korea-replay.pages.dev');
  expect(result.hash).toBe(hash);expect(result.searchParams.get('measure')).toBe('1');
  expect(()=>assertPinnedDeploymentV2(result.searchParams.get('deployment'),{
    schema_version:2,platform:'cloudflare-pages',project:'korea-replay',release_id:'pub-b71d244ced0bff39',
    artifact_sha256:'f538ef5ee072d412f33cb5c0f8fcb876ce44f5b835f100d6d7b52371f0f93c2c',
    snapshot:{origin:result.origin,hash:'ba3762eb'},data:{origin:'https://9b4862f2.korea-replay-data.pages.dev',manifest_path:'/data/atlas/atlas-c9201eb92e21985e/manifest.json',manifest_sha256:'96eea296568e9cc930040243a99f8bad583d8ecc2754c0f36afab3982b7125e6'},
  },result.origin)).not.toThrow();
});
it('does not redirect matching data, unknown releases, ambiguous pins or conflicting app releases',()=>{
  expect(propertyReleaseArchiveUrl(origin+hash,previous)).toBeNull();
  expect(propertyReleaseArchiveUrl(`${origin}#propertyRelease=property-2222222222222222`,current)).toBeNull();
  expect(propertyReleaseArchiveUrl(`${origin}#propertyRelease=__proto__`,current)).toBeNull();
  expect(propertyReleaseArchiveUrl(`${origin}${hash}&propertyRelease=${previous}`,current)).toBeNull();
  expect(propertyReleaseArchiveUrl(`${origin}${hash}&release=pub-other`,current)).toBeNull();
  expect(propertyReleaseArchiveUrl(`${origin}${hash}&release=pub-b71d244ced0bff39`,current)).not.toBeNull();
});
it.each(['?deployment=','?deployment=invalid','?deployment=pages%3Aba3762eb%3Ainvalid','?measure=1&deployment='])('never bypasses an existing deployment pin: %s',query=>{
  expect(propertyReleaseArchiveUrl(origin+query+hash,current)).toBeNull();
});
it.each(['http://127.0.0.1:5173/','http://localhost:5188/','http://korea-replay.pages.dev/','https://ba3762eb.korea-replay.pages.dev/','https://other.korea-replay.pages.dev/','https://korea-replay.pages.dev.evil.test/','https://user@korea-replay.pages.dev/','https://korea-replay.pages.dev:444/','https://korea-replay.pages.dev/other'])('does not auto-restore outside the canonical HTTPS root: %s',source=>{
  expect(propertyReleaseArchiveUrl(source+hash,current)).toBeNull();
});
it('ignores arbitrary redirect destinations and cannot loop from the destination',()=>{
  const result=propertyReleaseArchiveUrl(`${origin}?redirect=https://evil.test/&archive=//evil.test${hash}`,current)!;
  expect(new URL(result).origin).toBe('https://ba3762eb.korea-replay.pages.dev');
  expect(propertyReleaseArchiveUrl(result,current)).toBeNull();
  expect(propertyReleaseArchiveUrl('not a url',current)).toBeNull();
});

it('restores the previous 8879 release after the full history release is promoted',()=>{
  const target=propertyReleaseArchiveUrl('https://korea-replay.pages.dev/#regionCode=11710&complex=molit-apt%3A11710%3A11710-8865&historyMonths=36&area=84-band&propertyRelease=property-8879dff1b31ac5f0','property-ceeff63959643461');
  expect(target).not.toBeNull();const url=new URL(target!);
  expect(url.origin).toBe('https://57769488.korea-replay.pages.dev');
  expect(url.searchParams.get('deployment')).toBe('pages:57769488:30e6b535c1c4e6d22e656c103f86a2ecf969cd53cc88b7c6e9dac0fb6a70146d');
  const state=new URLSearchParams(url.hash.slice(1));expect(state.get('area')).toBe('84-band');expect(state.get('historyMonths')).toBe('36');expect(state.get('complex')).toBe('molit-apt:11710:11710-8865');
});


it('preserves the audited Songpa Helio share across the old-release app redirect',()=>{
  const hash='#regionCode=11710&complex=molit-apt%3A11710%3A11710-8865&trade=sale&month=202608&historyMonths=3&propertyRelease=property-87d1c67336e97209';
  const target=propertyReleaseArchiveUrl(`https://korea-replay.pages.dev/?qa=old-share-20261009${hash}`,'property-ceeff63959643461');
  expect(target).not.toBeNull();const url=new URL(target!);
  expect(url.origin).toBe('https://0c88b86f.korea-replay.pages.dev');
  expect(url.searchParams.get('deployment')).toBe('pages:0c88b86f:1f4d796c585e6fd2bc2a031685ad3cfad4e176fa5143e8146f59ce8ecb2886c2');
  expect(url.hash).toBe(hash);
  expect(propertyReleaseArchiveUrl(url.href,'property-87d1c67336e97209')).toBeNull();
});
