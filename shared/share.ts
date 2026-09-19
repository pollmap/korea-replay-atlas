export interface RuntimeVersion {version_id:string|null;preview_origin:string|null;release_id:string;}
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** Accept only the immutable Cloudflare version host belonging to this app. */
export function validateRuntime(value:unknown):RuntimeVersion {
  const runtime=value as Partial<RuntimeVersion>|null;
  if(!runtime||typeof runtime.release_id!=='string'||(runtime.version_id!==null&&(typeof runtime.version_id!=='string'||!UUID.test(runtime.version_id))))throw new Error('배포 버전을 확인하지 못했습니다.');
  if(runtime.preview_origin!==null){
    if(typeof runtime.preview_origin!=='string'||!runtime.version_id)throw new Error('공유용 배포 주소가 올바르지 않습니다.');
    let url:URL;try{url=new URL(runtime.preview_origin);}catch{throw new Error('공유용 배포 주소가 올바르지 않습니다.');}
    const expected=`${runtime.version_id.slice(0,8).toLowerCase()}-korea-replay.`;
    if(url.protocol!=='https:'||url.username||url.password||url.port||url.pathname!=='/'||url.search||url.hash||!url.hostname.startsWith(expected)||!(/^[a-z0-9]{8}-korea-replay\.[a-z0-9-]+\.workers\.dev$/i).test(url.hostname))throw new Error('공유용 배포 주소가 올바르지 않습니다.');
    return {version_id:runtime.version_id.toLowerCase(),preview_origin:url.origin,release_id:runtime.release_id};
  }
  return {version_id:runtime.version_id?.toLowerCase()??null,preview_origin:null,release_id:runtime.release_id};
}
export function assertPinnedDeployment(requested:string|null,runtime:RuntimeVersion):void {
  if(requested===null)return;
  if(!UUID.test(requested))throw new Error('공유 링크의 배포 버전이 올바르지 않습니다.');
  if(runtime.version_id!==requested.toLowerCase())throw new Error('이 링크의 배포 버전을 찾지 못했습니다. 최신 버전으로 바꾸지 않고 복원을 중단했습니다.');
}
export function versionedShareUrl(runtime:RuntimeVersion,release:string,params:URLSearchParams):string {
  if(!runtime.version_id||!runtime.preview_origin)throw new Error('이 화면의 고정 배포 주소를 확인하지 못했습니다. 배포가 확인된 화면에서 공유해 주세요.');
  const validated=validateRuntime(runtime);
  if(runtime.release_id!==release)throw new Error('화면 자료와 배포 자료의 버전이 다릅니다. 현재 화면을 새로 연 뒤 공유해 주세요.');
  const url=new URL(validated.preview_origin!);url.searchParams.set('deployment',validated.version_id!);
  const hash=new URLSearchParams(params);hash.set('release',release);url.hash=hash.toString();return url.href;
}
