import {describe,expect,it} from 'vitest';
import {assertPinnedDeploymentV2,pagesSnapshot,validateAtlasManifest,validateRuntimeV2,versionedShareUrlV2,type RuntimeV2} from '../shared/runtime-v2';
const hash='a'.repeat(64);
const runtime:RuntimeV2={schema_version:2,platform:'cloudflare-pages',project:'korea-replay',release_id:'pub-0123456789abcdef',artifact_sha256:hash,
  snapshot:{origin:'https://1234abcd.korea-replay.pages.dev',hash:'1234abcd'},data:{origin:'https://abcd1234.korea-replay-data.pages.dev',manifest_path:'/data/atlas/atlas-test/manifest.json',manifest_sha256:'b'.repeat(64)}};
describe('Pages immutable runtime',()=>{
  it('pins the actual preview, app artifact and independent immutable data',()=>{
    const value=validateRuntimeV2(runtime),url=new URL(versionedShareUrlV2(value,value.release_id,new URLSearchParams({flatCamera:'127,36,7,0'})));
    expect(url.hostname).toBe('1234abcd.korea-replay.pages.dev');expect(url.href).not.toContain('workers.dev');
    expect(new URLSearchParams(url.hash.slice(1)).get('flatCamera')).toBe('127,36,7,0');
    expect(()=>assertPinnedDeploymentV2(url.searchParams.get('deployment'),value,url.origin)).not.toThrow();
  });
  it.each(['https://korea-replay.pages.dev','https://main.korea-replay.pages.dev','https://1234abcd.korea-replay.pages.dev.evil.invalid','http://1234abcd.korea-replay.pages.dev','https://user@1234abcd.korea-replay.pages.dev','https://1234abcd.korea-replay.pages.dev/path'])('rejects a non-immutable origin: %s',origin=>expect(()=>pagesSnapshot(origin,'korea-replay')).toThrow());
  it('rejects a different artifact, different actual origin and obsolete Worker UUID',()=>{
    for(const token of [`pages:1234abcd:${'c'.repeat(64)}`,'pages:eeeeeeee:'+hash,'1234abcd-0000-0000-0000-000000000000'])expect(()=>assertPinnedDeploymentV2(token,runtime)).toThrow();
    expect(()=>assertPinnedDeploymentV2('pages:1234abcd:'+hash,runtime,'https://ffffffff.korea-replay.pages.dev')).toThrow();
  });
  it('refuses latest-data fallback and unverified share destinations',()=>{
    expect(()=>validateRuntimeV2({...runtime,data:{...runtime.data,origin:'https://korea-replay-data.pages.dev'}})).toThrow();
    expect(()=>versionedShareUrlV2({...runtime,snapshot:null},runtime.release_id,new URLSearchParams())).toThrow();
    expect(()=>validateRuntimeV2({...runtime,snapshot:{...runtime.snapshot,hash:'ffffffff'}})).toThrow();
  });
  it('requires both map and property content references in the atlas manifest',()=>{
    const ref={path:'/data/map-tiles/map-1/catalog.json',sha256:hash,release_id:'map-1'};
    expect(validateAtlasManifest({schema_version:1,release_id:'atlas-1',map_catalog:ref,property_release:{...ref,path:'/data/property/property-1/release.json',release_id:'property-1'}}).map_catalog).toEqual(ref);
    expect(()=>validateAtlasManifest({schema_version:1,release_id:'atlas-1',map_catalog:ref})).toThrow();
    expect(()=>validateAtlasManifest({schema_version:1,release_id:'atlas-1',map_catalog:ref,property_release:{...ref,path:'/data/../private.json'}})).toThrow();
  });
});
