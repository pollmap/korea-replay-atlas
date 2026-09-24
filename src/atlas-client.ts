import {AssetLoadQueue} from '../shared/asset-loader';
import {safeDataPath,validateAtlasManifest,type AtlasReleaseManifest,type RuntimeV2} from '../shared/runtime-v2';

const requests=new AssetLoadQueue<Response>(4);
let sequence=0,active=0,peak=0,bytes=0;
const jsonCache=new Map<string,{data:unknown;bytes:number}>();let jsonBytes=0;
interface JsonResult {data:unknown;bytes:number;}
interface JsonRequest {controller:AbortController;promise:Promise<JsonResult>;subscribers:number;}
const jsonRequests=new Map<string,JsonRequest>();
let sharedJsonHits=0;
export const atlasNetworkStats=()=>({active,peak,bytes,jsonCacheBytes:jsonBytes,jsonCacheEntries:jsonCache.size,pendingJsonRequests:jsonRequests.size,sharedJsonHits});
/** A shared, body-lifetime queue for map archives, property data and their manifests. */
export const atlasFetch:typeof fetch=async(input,init)=>{
  const controller=new AbortController(),external=init?.signal,abort=()=>controller.abort();
  external?.addEventListener('abort',abort,{once:true});if(external?.aborted)abort();
  const timer=setTimeout(abort,30000);
  try{
    const response=await requests.enqueue(String(++sequence),async signal=>{
      active++;peak=Math.max(peak,active);
      try{
        const result=await fetch(input,{...init,signal,credentials:'omit',redirect:'error'});
        if(!result.ok){await result.body?.cancel();return new Response(null,{status:result.status,headers:result.headers});}
        if(Number(result.headers.get('content-length')??0)>24*1024*1024){await result.body?.cancel();throw new Error('자료 크기 제한을 초과했습니다.');}
        const reader=result.body?.getReader(),parts:Uint8Array[]=[];let size=0;
        const cancel=()=>{void reader?.cancel().catch(()=>undefined);};signal.addEventListener('abort',cancel,{once:true});
        try{if(reader)for(;;){const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>24*1024*1024)throw new Error('자료 크기 제한을 초과했습니다.');parts.push(part.value);}if(signal.aborted)throw new DOMException('Aborted','AbortError');}
        catch(error){await reader?.cancel().catch(()=>undefined);throw error;}
        finally{signal.removeEventListener('abort',cancel);reader?.releaseLock();}
        bytes+=size;const body=new Uint8Array(size);let offset=0;for(const part of parts){body.set(part,offset);offset+=part.byteLength;}
        const headers=new Headers(result.headers);headers.delete('content-encoding');headers.delete('content-length');
        return new Response(body,{status:result.status,headers});
      }finally{active--;}
    },controller.signal);
    if(!response||controller.signal.aborted)throw new DOMException('Aborted','AbortError');return response;
  }finally{clearTimeout(timer);external?.removeEventListener('abort',abort);}
};
export interface PinnedJson {path?:string;url?:string;sha256:string;bytes?:number;byte_length?:number;}
export async function fetchPinnedJson(reference:PinnedJson,origin:string,signal:AbortSignal):Promise<unknown>{
  const path=reference.path??reference.url;
  if(!safeDataPath(path)||!/^[a-f0-9]{64}$/.test(reference.sha256))throw new Error('자료의 고정 참조를 확인하지 못했습니다.');
  if(signal.aborted)throw new DOMException('Aborted','AbortError');
  const key=new URL(path,origin).href+':'+reference.sha256,expected=reference.byte_length??reference.bytes,cached=jsonCache.get(key);
  if(cached){if(expected!==undefined&&cached.bytes!==expected)throw new Error('자료의 크기가 검증된 목록과 다릅니다.');jsonCache.delete(key);jsonCache.set(key,cached);return cached.data;}
  let request=jsonRequests.get(key);
  if(request?.controller.signal.aborted){jsonRequests.delete(key);request=undefined;}
  if(!request){
    if(jsonRequests.size>=128)throw new Error('자료 요청이 많습니다. 잠시 후 다시 시도해 주세요.');
    const controller=new AbortController();
    request={controller,promise:loadPinnedJson(new URL(path,origin).href,reference.sha256,key,controller.signal),subscribers:0};
    jsonRequests.set(key,request);
    const current=request;
    const settled=()=>{if(jsonRequests.get(key)===current)jsonRequests.delete(key);};
    void request.promise.then(settled,settled);
  }else sharedJsonHits++;
  const current=request;current.subscribers++;
  return new Promise((resolve,reject)=>{
    let finished=false;
    const finish=()=>{if(finished)return false;finished=true;signal.removeEventListener('abort',abort);current.subscribers--;if(current.subscribers===0)current.controller.abort();return true;};
    const abort=()=>{if(finish())reject(new DOMException('Aborted','AbortError'));};
    signal.addEventListener('abort',abort,{once:true});
    if(signal.aborted){abort();return;}
    void current.promise.then(result=>{if(!finish())return;if(expected!==undefined&&result.bytes!==expected)reject(new Error('자료의 크기가 검증된 목록과 다릅니다.'));else resolve(result.data);},error=>{if(finish())reject(error);});
  });
}
async function loadPinnedJson(url:string,expectedHash:string,key:string,signal:AbortSignal):Promise<JsonResult>{
  const response=await atlasFetch(url,{signal});
  if(!response.ok)throw new Error(`자료를 불러오지 못했습니다. (${response.status})`);
  const body=await response.arrayBuffer();
  const sha=[...new Uint8Array(await crypto.subtle.digest('SHA-256',body))].map(v=>v.toString(16).padStart(2,'0')).join('');
  if(sha!==expectedHash)throw new Error('자료의 내용이 검증된 버전과 다릅니다.');
  if(signal.aborted)throw new DOMException('Aborted','AbortError');
  const data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(body));
  // Bounded by source bytes, not an assertion about measured JS heap size.
  const limit=(typeof matchMedia==='function'&&matchMedia('(max-width:780px)').matches?4:8)*1024*1024;
  if(body.byteLength<=limit){
    while(jsonCache.size&&(jsonBytes+body.byteLength>limit||jsonCache.size>=64)){const [oldKey,old]=jsonCache.entries().next().value!;jsonCache.delete(oldKey);jsonBytes-=old.bytes;}
    const concurrent=jsonCache.get(key);if(concurrent)jsonBytes-=concurrent.bytes;
    jsonCache.set(key,{data,bytes:body.byteLength});jsonBytes+=body.byteLength;
  }
  return {data,bytes:body.byteLength};
}
export interface AtlasDescriptor {origin:string;manifest:AtlasReleaseManifest;}
export async function fetchAtlas(runtime:RuntimeV2|null,signal:AbortSignal):Promise<AtlasDescriptor|null>{
  if(runtime){const {origin,manifest_path,manifest_sha256}=runtime.data;return {origin,manifest:validateAtlasManifest(await fetchPinnedJson({path:manifest_path,sha256:manifest_sha256},origin,signal))};}
  if(!['localhost','127.0.0.1'].includes(location.hostname))return null;
  // Development pointer is generated from audited candidates; it is never used on production.
  const result=await atlasFetch('/data/atlas/development.json',{signal,cache:'no-store'});
  if(result.status===404)return null;if(!result.ok)throw new Error('개발 자료 목록을 불러오지 못했습니다.');
  return {origin:location.origin,manifest:validateAtlasManifest(await result.json())};
}
