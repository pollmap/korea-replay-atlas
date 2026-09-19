import type {Asset} from './contracts';
import type {QualityLevel} from './map-performance';

export interface PrimitiveCacheCost {bytes:number;vertices:number;}
export interface PrimitiveCacheLimits extends PrimitiveCacheCost {files:number;}
const MIB=1024*1024;
// The measured Daejeon return/overhead working sets retain about 12 MiB and
// 66,645 source vertices together. The low tier leaves bounded reuse headroom.
export const PRIMITIVE_CACHE_LIMITS:Record<QualityLevel,PrimitiveCacheLimits>={
  low:{bytes:16*MIB,vertices:100000,files:12},
  balanced:{bytes:20*MIB,vertices:125000,files:16},
  high:{bytes:24*MIB,vertices:150000,files:24},
};
export function heapUnderPressure(memory?:{usedJSHeapSize?:number;jsHeapSizeLimit?:number}):boolean {
  const used=memory?.usedJSHeapSize,limit=memory?.jsHeapSizeLimit;
  return typeof used==='number'&&typeof limit==='number'&&Number.isFinite(used)&&Number.isFinite(limit)&&limit>0&&used/limit>=.75;
}
export const primitiveCacheKey=(release:string,asset:Pick<Asset,'id'|'sha256'|'layer'>,distantOverview:boolean)=>JSON.stringify([release,asset.id,asset.sha256,asset.layer==='infrastructure'&&distantOverview]);
/** Estimated retained payload, not a claim about browser/driver heap allocation. */
export function primitiveCacheCost(asset:Asset,primitive:{vertexCount:number;featureCount:number;pointCount:number}):PrimitiveCacheCost {
  return {bytes:Math.max(asset.byte_length??0,primitive.featureCount*400)+primitive.vertexCount*48+primitive.pointCount*256,vertices:primitive.vertexCount};
}

/** Owns only dormant objects. take() transfers ownership back to the active scene. */
export class PrimitiveReuseCache<T> {
  private entries=new Map<string,{value:T;cost:PrimitiveCacheCost}>();
  private bytes=0;private vertices=0;private hits=0;private misses=0;private evictions=0;
  constructor(private limits:PrimitiveCacheLimits,private readonly dispose:(value:T)=>void){}
  snapshot(){return {files:this.entries.size,bytes:this.bytes,vertices:this.vertices,hits:this.hits,misses:this.misses,evictions:this.evictions,limitBytes:this.limits.bytes,limitVertices:this.limits.vertices,limitFiles:this.limits.files};}
  private remove(key:string,dispose:boolean):T|undefined {
    const entry=this.entries.get(key);if(!entry)return;
    this.entries.delete(key);this.bytes-=entry.cost.bytes;this.vertices-=entry.cost.vertices;
    if(dispose){this.evictions++;this.dispose(entry.value);}return entry.value;
  }
  take(key:string):T|undefined {
    const value=this.remove(key,false);if(value===undefined)this.misses++;else this.hits++;return value;
  }
  put(key:string,value:T,cost:PrimitiveCacheCost):void {
    this.remove(key,true);
    if(!Number.isFinite(cost.bytes)||!Number.isFinite(cost.vertices)||cost.bytes<0||cost.vertices<0||this.limits.files<1||cost.bytes>this.limits.bytes||cost.vertices>this.limits.vertices){this.evictions++;this.dispose(value);return;}
    this.entries.set(key,{value,cost});this.bytes+=cost.bytes;this.vertices+=cost.vertices;this.trim();
  }
  setLimits(limits:PrimitiveCacheLimits):void {this.limits=limits;this.trim();}
  private trim():void {
    while(this.entries.size&&(this.entries.size>this.limits.files||this.bytes>this.limits.bytes||this.vertices>this.limits.vertices))this.remove(this.entries.keys().next().value!,true);
  }
  retain(predicate:(value:T)=>boolean):void {for(const [key,entry] of this.entries)if(!predicate(entry.value))this.remove(key,true);}
  clear():void {for(const key of this.entries.keys())this.remove(key,true);}
}
