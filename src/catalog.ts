import type {Asset,BBox,Catalog,CatalogV2,LayerId,SpatialAssetIndex} from '../shared/contracts';
import {EMPTY_CATALOG} from '../shared/sources';

type Fetcher=typeof fetch;
const catalogCaches=new WeakMap<Fetcher,Map<string,{etag:string;catalog:Catalog}>>();
type IndexEntry={promise:Promise<Asset[]>;bytes:number;pending:boolean;controller:AbortController;users:Set<symbol>;abortTimer?:ReturnType<typeof setTimeout>};
const indexCaches=new WeakMap<Fetcher,Map<string,IndexEntry>>();
interface IndexWaiter {resolve:()=>void;reject:(error:unknown)=>void;signal:AbortSignal;abort:()=>void;}
const indexQueues=new WeakMap<Fetcher,{active:number;waiting:IndexWaiter[]}>();
const safePath=(url:string)=>url.startsWith('/data/')&&!url.includes('..')&&!url.includes('\\')&&!/[?#]/.test(url);
const intersects=(a:BBox,b:BBox)=>a[0]<=b[2]&&a[2]>=b[0]&&a[1]<=b[3]&&a[3]>=b[1];
const cancelled=()=>new DOMException('Aborted','AbortError');
async function indexSlot(fetcher:Fetcher,signal:AbortSignal){
  let queue=indexQueues.get(fetcher);if(!queue){queue={active:0,waiting:[]};indexQueues.set(fetcher,queue);}
  if(signal.aborted)throw cancelled();
  if(queue.active>=4)await new Promise<void>((resolve,reject)=>{
    const waiter:IndexWaiter={resolve,reject,signal,abort:()=>{queue!.waiting=queue!.waiting.filter(row=>row!==waiter);signal.removeEventListener('abort',waiter.abort);reject(cancelled());}};
    queue!.waiting.push(waiter);signal.addEventListener('abort',waiter.abort,{once:true});
  });
  else queue.active++;
  return ()=>{const next=queue.waiting.shift();if(next){next.signal.removeEventListener('abort',next.abort);next.resolve();}else queue.active--;};
}

export function normalizePublicCatalog(value:unknown,release:string|null):Catalog {
  const data=value as Catalog|CatalogV2;
  if(!data||![1,2].includes(data.schema_version)||!Array.isArray(data.assets)||!Array.isArray(data.layers)||typeof data.release_id!=='string'||(release&&data.release_id!==release))throw new Error('공개 데이터 목록 형식이 올바르지 않습니다.');
  if(data.schema_version===1)return data;
  if(!Array.isArray(data.indexes)||data.indexes.some(a=>a.format!=='asset-index'||!safePath(a.url)||!Array.isArray(a.bbox)||a.bbox.length!==4||!a.bbox.every(Number.isFinite)))throw new Error('지역별 데이터 목록 형식이 올바르지 않습니다.');
  return {...data,schema_version:1,assets:[...data.assets,...data.indexes]};
}

/** Parse public catalog bytes in the browser, outside the edge CPU budget. */
export async function fetchPublicCatalog(release:string|null,signal?:AbortSignal):Promise<Catalog>{
  if(release&&!/^pub-[a-f0-9]{16}$/.test(release))throw new Error('공유한 공개 버전 형식이 올바르지 않습니다.');
  const path=release?`/data/releases/${release}.json`:'/data/catalog.json';
  const fetcher=fetch;
  let cache=catalogCaches.get(fetcher);if(!cache){cache=new Map();catalogCaches.set(fetcher,cache);}
  const previous=cache.get(path);
  const response=await fetcher(path,previous?.etag?{signal,headers:{'If-None-Match':previous.etag}}:{signal});
  if(response.status===304&&previous)return previous.catalog;
  if(response.status===404&&!release)return EMPTY_CATALOG;
  if(!response.ok)throw new Error(release?'공유한 공개 버전을 불러오지 못했습니다.':'데이터 목록을 불러오지 못했습니다.');
  const data=normalizePublicCatalog(await response.json(),release);
  cache.set(path,{etag:response.headers.get('etag')??'',catalog:data});
  while(cache.size>8)cache.delete(cache.keys().next().value!);
  return data;
}

function consumeIndex(entry:IndexEntry,signal?:AbortSignal):Promise<Asset[]>{
  if(signal?.aborted)return Promise.reject(cancelled());
  clearTimeout(entry.abortTimer);const user=Symbol();entry.users.add(user);
  return new Promise((resolve,reject)=>{
    let done=false;
    const finish=(error?:unknown,value?:Asset[])=>{
      if(done)return;done=true;signal?.removeEventListener('abort',abort);entry.users.delete(user);
      // Effect cleanup and the replacement camera view can share this request.
      // Abort abandoned fetches only after the next view has had a chance to join.
      if(entry.pending&&!entry.users.size)entry.abortTimer=setTimeout(()=>{if(!entry.users.size&&entry.pending)entry.controller.abort();},0);
      if(error)reject(error);else resolve(value!);
    };
    const abort=()=>finish(cancelled());signal?.addEventListener('abort',abort,{once:true});
    void entry.promise.then(value=>finish(undefined,value),error=>finish(error));
  });
}
async function readIndex(asset:Asset,fetcher:Fetcher,signal?:AbortSignal):Promise<Asset[]> {
  if(signal?.aborted)throw cancelled();
  let cache=indexCaches.get(fetcher);if(!cache){cache=new Map();indexCaches.set(fetcher,cache);}
  const key=`${asset.url}:${asset.sha256}`;
  const existing=cache.get(key);if(existing&&!existing.controller.signal.aborted){cache.delete(key);cache.set(key,existing);return consumeIndex(existing,signal);}
  // The nationwide overview has more than 64 tiny manifests. A byte budget
  // bounds memory without evicting the start of that view before it finishes.
  const trim=()=>{
    let bytes=Array.from(cache!.values()).reduce((n,v)=>n+v.bytes,0);
    for(const [oldKey,value] of cache!){
      if(cache!.size<=512&&bytes<=8*1024*1024)break;
      if(value.pending)continue;
      cache!.delete(oldKey);bytes-=value.bytes;
    }
  };
  const entry:IndexEntry={bytes:asset.byte_length??65536,promise:Promise.resolve([] as Asset[]),pending:true,controller:new AbortController(),users:new Set()};
  entry.promise=(async()=>{
    if(!safePath(asset.url))throw new Error('지역 자료 경로가 올바르지 않습니다.');
    const release=await indexSlot(fetcher,entry.controller.signal);
    try{
    const response=await fetcher(asset.url,{signal:entry.controller.signal});
    if(!response.ok)throw new Error(`지역 자료 목록 HTTP ${response.status}`);
    const bytes=await response.arrayBuffer();
    if(bytes.byteLength>1024*1024)throw new Error('지역 자료 목록 크기 초과');
    if(/^[a-f0-9]{64}$/.test(asset.sha256)){
      const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),v=>v.toString(16).padStart(2,'0')).join('');
      if(hash!==asset.sha256)throw new Error('지역 자료 목록 해시 불일치');
    }
    const data=JSON.parse(new TextDecoder().decode(bytes)) as SpatialAssetIndex;
    if(data.schema_version!==1||!Array.isArray(data.assets)||data.assets.length>4096||data.assets.some(a=>a.format==='asset-index'||!safePath(a.url)))throw new Error('지역 자료 목록 형식 오류');
    entry.bytes=bytes.byteLength;
    return data.assets;
    }finally{entry.pending=false;clearTimeout(entry.abortTimer);release();trim();}
  })().catch(error=>{entry.pending=false;if(cache!.get(key)===entry)cache!.delete(key);throw error;});
  cache.set(key,entry);
  trim();
  return consumeIndex(entry,signal);
}

/** Spatial manifests are immutable; reuse in-flight reads across camera moves. */
export async function resolveSceneAssets(catalog:Catalog,bbox:BBox,height:number,signal?:AbortSignal,fetcher:Fetcher=fetch,onProgress?:(assets:Asset[])=>void,layers?:Record<LayerId,boolean>):Promise<Asset[]> {
  if(signal?.aborted)throw cancelled();
  const core=catalog.assets.filter(a=>a.format!=='asset-index');
  const selected=catalog.assets.filter(a=>a.format==='asset-index'&&(!layers||a.layer==='terrain'||layers[a.layer])&&intersects(a.bbox,bbox)
    &&(a.max_camera_height===undefined||height<a.max_camera_height)&&(a.detail_level==='overview'||a.min_camera_height===undefined||height>=a.min_camera_height));
  const distance=(a:Asset)=>((a.bbox[0]+a.bbox[2]-bbox[0]-bbox[2])*Math.cos((bbox[1]+bbox[3])*Math.PI/360))**2+(a.bbox[1]+a.bbox[3]-bbox[1]-bbox[3])**2;
  // Near-center metadata wins; a later batch never blocks the core globe.
  selected.sort((a,b)=>distance(a)-distance(b)||a.id.localeCompare(b.id));
  let cursor=0;
  const result:Asset[][]=[];
  const combined=()=>[...new Map([...core,...result.flat()].map(a=>[a.id,a])).values()];
  onProgress?.(core);
  await Promise.all(Array.from({length:Math.min(4,selected.length)},async()=>{
    while(cursor<selected.length){
      if(signal?.aborted)throw new DOMException('Aborted','AbortError');
      const index=cursor++;
      result[index]=await readIndex(selected[index],fetcher,signal);
      if(signal?.aborted)throw cancelled();
      onProgress?.(combined());
    }
  }));
  if(signal?.aborted)throw new DOMException('Aborted','AbortError');
  return combined();
}
