import {Compression,PMTiles,ResolvedValueCache,zxyToTileId,type Source as PMSource} from 'pmtiles';
import {findDetailChunk,findTileChunk,MAP_TILE_LIMIT,validateMapCatalog2D,type MapCatalog2D,type MapTileFile,type MapTileRecord} from '../shared/map-tiles';
import {selectedProperties,type MapSelection} from '../shared/selection';

const aborted=()=>new DOMException('Aborted','AbortError');
const digest=async(data:ArrayBuffer)=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',data)),x=>x.toString(16).padStart(2,'0')).join('');
type Subscriber={resolve:(data:ArrayBuffer)=>void;reject:(reason:unknown)=>void;detach:()=>void};
interface Job {key:string;ref:MapTileFile;controller:AbortController;subscribers:Set<Subscriber>;started:boolean;}

/** The only owner of archive bodies. Source and PMTiles instances keep keys, never buffers. */
export class VerifiedMapTileStore {
  private readonly cache=new Map<string,ArrayBuffer>();private readonly jobs=new Map<string,Job>();
  private active=0;private peak=0;private bytes=0;private disposed=false;private downloads=0;
  constructor(private readonly origin:string,private readonly fetcher:typeof fetch=fetch,private readonly limit=64*1024*1024,allowedOrigins:string[]=[origin]){
    const u=new URL(origin);if(u.origin!==origin||!allowedOrigins.includes(origin)||u.protocol!=='https:'&&!(['localhost','127.0.0.1'].includes(u.hostname)&&u.protocol==='http:'))throw new Error('Unapproved 2D data origin');
    if(!Number.isSafeInteger(limit)||limit<MAP_TILE_LIMIT||limit>64*1024*1024)throw new Error('Invalid 2D memory budget');
  }
  get(ref:MapTileFile,signal?:AbortSignal):Promise<ArrayBuffer>{
    if(this.disposed||signal?.aborted)return Promise.reject(aborted());
    if(ref.byte_length>MAP_TILE_LIMIT||ref.byte_length<=0||!/^\/data\/[\w./-]+$/.test(ref.url)||ref.url.split('/').some(x=>x==='.'||x==='..')||!/^[a-f0-9]{64}$/.test(ref.sha256))return Promise.reject(new Error('Invalid 2D file reference'));
    const key=`${ref.url}:${ref.sha256}`,cached=this.cache.get(key);
    if(cached){this.cache.delete(key);this.cache.set(key,cached);return Promise.resolve(cached);}
    let job=this.jobs.get(key);
    if(!job||job.controller.signal.aborted){job={key,ref,controller:new AbortController(),subscribers:new Set(),started:false};this.jobs.set(key,job);}
    const owned=job;
    return new Promise((resolve,reject)=>{
      const abort=()=>{owned.subscribers.delete(subscriber);subscriber.detach();reject(aborted());if(!owned.subscribers.size){owned.controller.abort();if(!owned.started&&this.jobs.get(key)===owned)this.jobs.delete(key);}this.pump();};
      const subscriber:Subscriber={resolve,reject,detach:()=>signal?.removeEventListener('abort',abort)};
      owned.subscribers.add(subscriber);signal?.addEventListener('abort',abort,{once:true});this.pump();
    });
  }
  private pump():void {
    if(this.disposed)return;
    for(const job of this.jobs.values()){
      if(this.active>=4)break;if(job.started||job.controller.signal.aborted||!job.subscribers.size)continue;
      job.started=true;this.active++;this.peak=Math.max(this.peak,this.active);
      void this.load(job).then(data=>{if(this.disposed||job.controller.signal.aborted)throw aborted();
        while(this.bytes+data.byteLength>this.limit&&this.cache.size){const [key,old]=this.cache.entries().next().value!;this.cache.delete(key);this.bytes-=old.byteLength;}
        this.cache.set(job.key,data);this.bytes+=data.byteLength;
        for(const sub of job.subscribers){sub.detach();sub.resolve(data);}job.subscribers.clear();
      }).catch(error=>{for(const sub of job.subscribers){sub.detach();sub.reject(error);}job.subscribers.clear();}).finally(()=>{this.active--;if(this.jobs.get(job.key)===job)this.jobs.delete(job.key);this.pump();});
    }
  }
  private async load(job:Job):Promise<ArrayBuffer>{
    const timeout=setTimeout(()=>job.controller.abort(),20000);const chunks:Uint8Array[]=[];let size=0;
    try{
      // Full GET is intentional: Static Assets need not support Range. Redirects cannot escape the pinned origin.
      const response=await this.fetcher(this.origin+job.ref.url,{signal:job.controller.signal,redirect:'error',credentials:'omit'});this.downloads++;
      if(response.status!==200){await response.body?.cancel();throw new Error(`2D asset HTTP ${response.status}`);}
      const reader=response.body?.getReader();if(!reader)throw new Error('Empty 2D asset body');
      try{while(true){const next=await reader.read();if(next.done)break;size+=next.value.byteLength;if(size>job.ref.byte_length||size>MAP_TILE_LIMIT)throw new Error('2D asset body exceeded declared size');chunks.push(next.value);}}
      catch(error){await reader.cancel().catch(()=>undefined);throw error;}finally{reader.releaseLock();}
      if(size!==job.ref.byte_length)throw new Error('2D asset size mismatch');
      const data=new Uint8Array(size);let at=0;for(const chunk of chunks){data.set(chunk,at);at+=chunk.byteLength;}
      if(await digest(data.buffer)!==job.ref.sha256)throw new Error('2D asset SHA mismatch');
      return data.buffer;
    }finally{clearTimeout(timeout);}
  }
  snapshot(){return {cachedBytes:this.bytes,cachedArchives:this.cache.size,active:this.active,peakActive:this.peak,pending:this.jobs.size,downloads:this.downloads,limitBytes:this.limit};}
  dispose(){if(this.disposed)return;this.disposed=true;for(const job of this.jobs.values()){job.controller.abort();for(const sub of job.subscribers){sub.detach();sub.reject(aborted());}job.subscribers.clear();}this.jobs.clear();this.cache.clear();this.bytes=0;}
}
export class VerifiedArchiveSource implements PMSource {
  constructor(private readonly ref:MapTileFile,private readonly store:VerifiedMapTileStore){}
  getKey(){return `${this.ref.url}:${this.ref.sha256}`;}
  async getBytes(offset:number,length:number,signal?:AbortSignal){
    if(!Number.isSafeInteger(offset)||!Number.isSafeInteger(length)||offset<0||length<=0||offset>=this.ref.byte_length)throw new Error('Invalid PMTiles byte range');
    const full=await this.store.get(this.ref,signal);if(signal?.aborted)throw aborted();
    return {data:full.slice(offset,Math.min(offset+length,full.byteLength)),etag:this.ref.sha256};
  }
}
export async function decompressMapTile(data:ArrayBuffer,compression:Compression,maximum=8*1024*1024):Promise<ArrayBuffer>{
  if(compression===Compression.None){if(data.byteLength>maximum)throw new Error('Decoded tile budget exceeded');return data;}
  if(compression!==Compression.Gzip)throw new Error('Unsupported map tile compression');
  const reader=new Blob([data]).stream().pipeThrough(new DecompressionStream('gzip')).getReader(),parts:Uint8Array[]=[];let count=0;
  try{while(true){const part=await reader.read();if(part.done)break;count+=part.value.byteLength;if(count>maximum)throw new Error('Decoded tile budget exceeded');parts.push(part.value);}}
  catch(error){await reader.cancel().catch(()=>undefined);throw error;}finally{reader.releaseLock();}
  const out=new Uint8Array(count);let at=0;for(const part of parts){out.set(part,at);at+=part.byteLength;}return out.buffer;
}
/** Public cache API; conservative allocation estimate, explicitly not measured heap bytes. */
class BoundedDirectoryCache extends ResolvedValueCache {
  private disposed=false;
  constructor(){super(64,true,(data,c)=>decompressMapTile(data,c,4*1024*1024));}
  allocation(){let bytes=0;for(const row of this.cache.values())bytes+=Array.isArray(row.data)?row.data.length*64:row.data instanceof ArrayBuffer?row.data.byteLength:512;return bytes;}
  override prune(){if(this.disposed){this.cache.clear();return;}while(this.cache.size>64||this.allocation()>4*1024*1024){let key:string|undefined,min=Infinity;for(const [k,v] of this.cache)if(v.lastUsed<min){min=v.lastUsed;key=k;}if(key===undefined)break;this.cache.delete(key);}}
  dispose(){this.disposed=true;this.cache.clear();}
}
export interface MapTilesProtocolOptions {catalog:MapCatalog2D;origin:string;allowedOrigins:string[];mobile?:boolean;fetcher?:typeof fetch;}
export function createMapTilesProtocol(options:MapTilesProtocolOptions){
  const catalog=validateMapCatalog2D(options.catalog),store=new VerifiedMapTileStore(options.origin,options.fetcher, (options.mobile?32:64)*1024*1024,options.allowedOrigins),directories=new BoundedDirectoryCache();
  const topics=new Map(catalog.topics.map(t=>[t.id,t])),archives=new Map<string,PMTiles>();let disposed=false;
  const tileUrl=(topic:string)=>{if(!topics.has(topic))throw new Error('Unknown 2D topic');return `krtile://${catalog.release_id}/${topic}/{z}/{x}/{y}`;};
  const protocol=async(params:{url:string},controller:AbortController):Promise<{data:ArrayBuffer}>=>{
    if(disposed||controller.signal.aborted)throw aborted();
    const m=/^krtile:\/\/(map2d-[a-f0-9]{20,64})\/([a-z][a-z0-9-]{0,47})\/(\d+)\/(\d+)\/(\d+)$/.exec(params.url);
    if(!m||m[1]!==catalog.release_id)throw new Error('Invalid 2D protocol URL');const topic=topics.get(m[2]);
    const z=Number(m[3]),x=Number(m[4]),y=Number(m[5]);if(!topic||z<topic.minzoom||z>topic.maxzoom||x>=2**z||y>=2**z)throw new Error('Invalid 2D tile coordinates');
    const chunk=findTileChunk(topic,zxyToTileId(z,x,y));if(!chunk)return {data:new ArrayBuffer(0)};
    // Attach caller cancellation before PMTiles' header path, whose API has no signal parameter.
    await store.get(chunk,controller.signal);if(disposed||controller.signal.aborted)throw aborted();
    let archive=archives.get(chunk.sha256);if(!archive){archive=new PMTiles(new VerifiedArchiveSource(chunk,store),directories,(data,c)=>decompressMapTile(data,c,MAP_TILE_LIMIT));archives.set(chunk.sha256,archive);if(archives.size>128)archives.delete(archives.keys().next().value!);}
    const result=await archive.getZxy(z,x,y,controller.signal);if(disposed||controller.signal.aborted)throw aborted();return {data:result?.data??new ArrayBuffer(0)};
  };
  const pick=async(topicId:string,id:string,signal?:AbortSignal):Promise<MapSelection|null>=>{
    if(disposed||signal?.aborted)throw aborted();const topic=topics.get(topicId);if(!topic)return null;const ref=findDetailChunk(topic,id);if(!ref)return null;
    const body=await decompressMapTile(await store.get(ref,signal),Compression.Gzip);if(disposed||signal?.aborted)throw aborted();
    const document=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(body)) as {schema_version:number;records:MapTileRecord[]};
    if(document.schema_version!==1||!Array.isArray(document.records)||document.records.length!==ref.record_count)throw new Error('Invalid 2D selection records');
    const matches=document.records.filter(r=>typeof r.stable_id==='string'&&(id.length===64?r.stable_id===id:r.stable_id.startsWith(id)));
    if(matches.length>1)throw new Error('Ambiguous display identity');const row=matches[0];if(!row)return null;
    if(!row.properties||typeof row.properties!=='object'||typeof row.source_record_id!=='string'||typeof row.source_id!=='string'||typeof row.version!=='string')throw new Error('Invalid 2D selection provenance');
    return selectedProperties(row.source_record_id,row.properties,{source_id:row.source_id,version:row.version});
  };
  return {catalog,tileUrl,protocol,pick,snapshot:()=>({...store.snapshot(),directoryEntries:directories.cache.size,directoryAllocationEstimateBytes:directories.allocation(),archiveHandles:archives.size}),dispose(){if(disposed)return;disposed=true;store.dispose();directories.dispose();archives.clear();}};
}
