export interface DataReleaseReference {path:string;sha256:string;release_id:string;}
export interface AtlasReleaseManifest {
  schema_version:1;release_id:string;
  map_catalog:DataReleaseReference;
  property_release:DataReleaseReference;
}
export interface RuntimeV2 {
  schema_version:2;platform:'cloudflare-pages';project:'korea-replay';
  release_id:string;artifact_sha256:string;
  snapshot:{origin:string;hash:string}|null;
  data:{origin:string;manifest_path:string;manifest_sha256:string};
}
const SHA=/^[a-f0-9]{64}$/;
const RELEASE=/^[a-z0-9][a-z0-9._-]{0,127}$/;
export function pagesSnapshot(origin:string,project:'korea-replay'|'korea-replay-data'):{origin:string;hash:string} {
  let url:URL;try{url=new URL(origin);}catch{throw new Error('고정 Pages 주소를 확인하지 못했습니다.');}
  const match=new RegExp(`^([a-f0-9]{8})\\.${project}\\.pages\\.dev$`).exec(url.hostname);
  if(!match||url.protocol!=='https:'||url.username||url.password||url.port||url.pathname!=='/'||url.search||url.hash)throw new Error('실제 Pages 미리보기 주소가 필요합니다.');
  return {origin:url.origin,hash:match[1]};
}
export function safeDataPath(value:unknown):value is string {
  return typeof value==='string'&&/^\/data\/[a-zA-Z0-9._/-]+$/.test(value)
    &&value.split('/').slice(1).every(segment=>!!segment&&segment!=='.'&&segment!=='..');
}
export function validateAtlasManifest(value:unknown):AtlasReleaseManifest {
  const manifest=value as Partial<AtlasReleaseManifest>|null;
  if(!manifest||manifest.schema_version!==1||typeof manifest.release_id!=='string'||!RELEASE.test(manifest.release_id))throw new Error('지도·부동산 자료 목록을 확인하지 못했습니다.');
  for(const reference of [manifest.map_catalog,manifest.property_release]){
    if(!reference||!safeDataPath(reference.path)||!SHA.test(reference.sha256)||typeof reference.release_id!=='string'||!RELEASE.test(reference.release_id))throw new Error('자료 목록의 고정 참조가 올바르지 않습니다.');
  }
  return {schema_version:1,release_id:manifest.release_id,map_catalog:{...manifest.map_catalog!},property_release:{...manifest.property_release!}};
}
export function validateRuntimeV2(value:unknown):RuntimeV2 {
  const runtime=value as Partial<RuntimeV2>|null;
  if(!runtime||runtime.schema_version!==2||runtime.platform!=='cloudflare-pages'||runtime.project!=='korea-replay'
    ||typeof runtime.release_id!=='string'||!RELEASE.test(runtime.release_id)||!SHA.test(runtime.artifact_sha256??''))throw new Error('Pages 배포 버전을 확인하지 못했습니다.');
  let snapshot:RuntimeV2['snapshot']=null;
  if(runtime.snapshot!==null){
    if(!runtime.snapshot||typeof runtime.snapshot.origin!=='string')throw new Error('공유용 고정 배포 주소가 없습니다.');
    snapshot=pagesSnapshot(runtime.snapshot.origin,'korea-replay');
    if(snapshot.hash!==runtime.snapshot.hash)throw new Error('공유용 배포 식별자가 주소와 다릅니다.');
  }
  const data=runtime.data;
  if(!data||typeof data.origin!=='string'||!safeDataPath(data.manifest_path)||!/^\/data\/atlas\/[^/]+\/manifest\.json$/.test(data.manifest_path)||!SHA.test(data.manifest_sha256))throw new Error('지도·부동산 자료의 고정 버전이 없습니다.');
  return {schema_version:2,platform:'cloudflare-pages',project:'korea-replay',release_id:runtime.release_id,
    artifact_sha256:runtime.artifact_sha256!,snapshot,data:{origin:pagesSnapshot(data.origin,'korea-replay-data').origin,manifest_path:data.manifest_path,manifest_sha256:data.manifest_sha256}};
}
export function assertPinnedDeploymentV2(requested:string|null,value:RuntimeV2,currentOrigin?:string):void {
  const runtime=validateRuntimeV2(value);if(requested===null)return;
  const match=/^pages:([a-f0-9]{8}):([a-f0-9]{64})$/.exec(requested);
  if(!match||!runtime.snapshot||match[1]!==runtime.snapshot.hash||match[2]!==runtime.artifact_sha256)throw new Error('이 링크의 배포 버전을 찾지 못했습니다. 최신 버전으로 바꾸지 않고 복원을 중단했습니다.');
  if(currentOrigin!==undefined&&pagesSnapshot(currentOrigin,'korea-replay').origin!==runtime.snapshot.origin)throw new Error('공유 링크와 현재 배포 주소가 다릅니다.');
}
export function versionedShareUrlV2(value:RuntimeV2,release:string,params:URLSearchParams):string {
  const runtime=validateRuntimeV2(value);
  if(!runtime.snapshot)throw new Error('고정 배포 주소가 확인된 뒤 공유할 수 있습니다.');
  if(runtime.release_id!==release)throw new Error('화면 자료와 배포 자료의 버전이 다릅니다.');
  const url=new URL(runtime.snapshot.origin);url.searchParams.set('deployment',`pages:${runtime.snapshot.hash}:${runtime.artifact_sha256}`);
  const hash=new URLSearchParams(params);hash.set('release',release);url.hash=hash.toString();return url.href;
}
