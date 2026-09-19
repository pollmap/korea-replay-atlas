import {describe,it,expect} from 'vitest';
import {assertPinnedDeployment,validateRuntime,versionedShareUrl} from '../shared/share';
const version='9d3f5082-174c-4901-a25a-71ab40944ed8';
const runtime={version_id:version,preview_origin:'https://9d3f5082-korea-replay.example.workers.dev',release_id:'pub-0123456789abcdef'};
describe('immutable screen sharing',()=>{
  it('pins both deployment and source release with the selected camera/time',()=>{
    const url=new URL(versionedShareUrl(validateRuntime(runtime),runtime.release_id,new URLSearchParams({time:'2026-09-10T03:00:00Z',camera:'1,2,3,0,0,0'})));
    expect(url.origin).toBe(runtime.preview_origin);expect(url.searchParams.get('deployment')).toBe(version);
    const hash=new URLSearchParams(url.hash.slice(1));expect(hash.get('release')).toBe(runtime.release_id);expect(hash.get('time')).toBe('2026-09-10T03:00:00Z');expect(hash.get('camera')).toBe('1,2,3,0,0,0');
    expect(()=>assertPinnedDeployment(version,validateRuntime(runtime))).not.toThrow();
  });
  it('refuses absent/mismatched deployments or data releases instead of changing the selection',()=>{
    expect(()=>assertPinnedDeployment('00000000-0000-0000-0000-000000000000',runtime)).toThrow('복원을 중단');
    expect(()=>assertPinnedDeployment('latest',runtime)).toThrow('올바르지');
    expect(()=>versionedShareUrl(runtime,'pub-another',new URLSearchParams())).toThrow('자료의 버전');
    expect(()=>versionedShareUrl({version_id:null,preview_origin:null,release_id:runtime.release_id},runtime.release_id,new URLSearchParams())).toThrow('고정 배포 주소');
  });
  it.each(['http://9d3f5082-korea-replay.example.workers.dev','https://9d3f5082-korea-replay.example.workers.dev.evil.test','https://9d3f5082-other.example.workers.dev','https://deadbeef-korea-replay.example.workers.dev','https://9d3f5082-korea-replay.example.workers.dev/another','https://9d3f5082-korea-replay.example.workers.dev?next=1','https://user@9d3f5082-korea-replay.example.workers.dev'])('rejects a non-version origin: %s',origin=>{
    expect(()=>validateRuntime({...runtime,preview_origin:origin})).toThrow('주소');
  });
});
