import {expect,it} from 'vitest';
import {propertyReleaseArchiveUrl} from '../src/property-release-archive';
import {assertPinnedDeploymentV2} from '../shared/runtime-v2';

const current='property-1111111111111111',previous='property-eea48453a819f517';
const hash=`#regionCode=11410&trade=sale&month=202608&historyMonths=120&propertyRelease=${previous}&complex=molit-apt%3A11410%3Atest&area=84-band`;
const origin='https://korea-replay.pages.dev/';
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
