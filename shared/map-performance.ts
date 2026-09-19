import type {Asset,BBox,LayerId} from './contracts';
import {geometryVertexEstimate} from './geometry';
export type QualityLevel='high'|'balanced'|'low';
export const QUALITY={high:{resolution:1,sse:16,bytes:48*1024*1024,vertices:500000,files:96,labels:400},balanced:{resolution:.85,sse:24,bytes:28*1024*1024,vertices:280000,files:64,labels:250},low:{resolution:.7,sse:40,bytes:16*1024*1024,vertices:150000,files:36,labels:120}} as const;
export function sceneQuality(quality:QualityLevel,moving:boolean,sun:boolean,cameraHeight=0){
  const settled=QUALITY[quality];
  return {resolution:moving?Math.min(settled.resolution,.75):settled.resolution,
    msaaSamples:{high:4,balanced:2,low:1}[quality],
    sse:moving?1024:settled.sse,
    terrainSse:moving?{high:4,balanced:6,low:8}[quality]:{high:2,balanced:3,low:4}[quality],shadows:sun&&!moving&&cameraHeight<25000,
    shadowSize:{high:2048,balanced:1024,low:512}[quality],
    shadowDistance:Math.min(15000,Math.max(5000,(Number.isFinite(cameraHeight)?cameraHeight:0)*1.8))};
}
export interface PerformanceSnapshot {quality:QualityLevel;renderP95Ms:number;frameP95Ms:number;sampleCount:number;}
/** Globe load events also include imagery; only an invalidated terrain view earns one refresh. */
export class TerrainRevisionGate {
  private requested=1;private applied=0;
  get revision(){return this.applied;}
  get pending(){return this.requested!==this.applied;}
  invalidate():void {this.requested++;}
  settle(tilesLoaded:boolean,moving:boolean):number|undefined {
    if(!tilesLoaded||moving||!this.pending)return;
    this.applied=this.requested;return this.applied;
  }
}
const percentile=(values:number[],q:number)=>values.length?[...values].sort((a,b)=>a-b)[Math.min(values.length-1,Math.floor(values.length*q))]:0;
/** Render cost and moving-camera cadence are measured separately from idle gaps. */
export class QualityGovernor {
  private renders:number[]=[];private frames:number[]=[];private lastDecision=0;private healthySince:number|null=null;
  constructor(public quality:QualityLevel='high'){}
  observe(renderMs:number,frameMs:number|null,now:number):PerformanceSnapshot {
    if(Number.isFinite(renderMs)&&renderMs>=0){this.renders.push(renderMs);if(this.renders.length>90)this.renders.shift();}
    if(frameMs!==null&&Number.isFinite(frameMs)&&frameMs>0){this.frames.push(frameMs);if(this.frames.length>90)this.frames.shift();}
    const renderP95Ms=percentile(this.renders,.95),frameP95Ms=percentile(this.frames,.95);
    if(this.renders.length>=30&&now-this.lastDecision>=3000){
      const slow=renderP95Ms>22||(this.frames.length>=30&&frameP95Ms>40);
      const fast=renderP95Ms<10&&this.frames.length>=30&&frameP95Ms<24;
      if(slow){
        const next=this.quality==='high'?'balanced':'low';
        if(next!==this.quality){this.quality=next;this.renders=[];this.frames=[];}
        this.lastDecision=now;this.healthySince=null;
      }
      else if(fast){this.healthySince??=now;if(now-this.healthySince>=20000){
        const next=this.quality==='low'?'balanced':'high';
        if(next!==this.quality){this.quality=next;this.renders=[];this.frames=[];}
        this.lastDecision=now;this.healthySince=null;
      }}
      else this.healthySince=null;
    }
    return {quality:this.quality,renderP95Ms,frameP95Ms,sampleCount:this.renders.length};
  }
}
type SizedAsset=Asset&{byte_length?:number;bytes?:number;vertex_count?:number};
export interface ViewSelection {assets:Asset[];deferred:number;bytes:number;vertices:number;fallback:boolean;}
export function selectViewAssets(assets:Asset[],options:{bbox:BBox;height:number;layers:Record<LayerId,boolean>;mode:'replay'|'sun';dayStart:number;dayEnd:number;quality:QualityLevel}):ViewSelection {
  const {bbox,height,layers,mode,dayStart,dayEnd,quality}=options,budget=QUALITY[quality];
  const overlap=(a:Asset)=>a.bbox[0]<=bbox[2]&&a.bbox[2]>=bbox[0]&&a.bbox[1]<=bbox[3]&&a.bbox[3]>=bbox[1];
  const candidates=assets.filter(a=>['geojson','3d-tiles','quantized-mesh','replay','imagery'].includes(a.format)&&!(a.id==='osm-stations-korea'&&layers.infrastructure)&&(a.layer==='terrain'||layers[a.layer])&&overlap(a)&&(!['imagery','replay'].includes(a.format)||mode==='replay')&&(!a.from||!a.to||(Date.parse(a.from)<dayEnd&&Date.parse(a.to)>=dayStart))&&!(a.layer==='buildings'&&height>60000&&!(a.format==='3d-tiles'&&a.detail_level==='overview')));
  // Do not download/construct a hidden duplicate road layer.
  const hasInfrastructure=layers.infrastructure&&candidates.some(a=>a.layer==='infrastructure');
  const filtered=candidates.filter(a=>!(hasInfrastructure&&a.source_id==='overture-transportation'&&a.layer==='terrain'));
  const priority=(a:Asset)=>a.format==='quantized-mesh'?0:a.source_id==='natural-earth'?1:a.format==='replay'||a.layer==='depth'?2:a.format==='imagery'?3:a.detail_level==='overview'?4:a.format==='3d-tiles'?6:5;
  const distance=(a:Asset)=>((a.bbox[0]+a.bbox[2]-bbox[0]-bbox[2])*Math.cos((bbox[1]+bbox[3])*Math.PI/360))**2+(a.bbox[1]+a.bbox[3]-bbox[1]-bbox[3])**2;
  const visible=filtered.filter(a=>(a.min_camera_height===undefined||height>=a.min_camera_height)&&(a.max_camera_height===undefined||height<a.max_camera_height)).sort((a,b)=>priority(a)-priority(b)||distance(a)-distance(b)||a.id.localeCompare(b.id));
  let bytes=0,vertices=0,deferred=0,geoFiles=0;const selected:Asset[]=[];
  const admit=(a:SizedAsset)=>{
    const geometry=a.format==='geojson'&&a.source_id!=='natural-earth';
    const cost=geometry?(a.byte_length??a.bytes??a.count*600):0,count=geometry?geometryVertexEstimate(a):0;
    if(geometry&&(geoFiles>=budget.files||bytes+cost>budget.bytes||vertices+count>budget.vertices))return false;
    selected.push(a);bytes+=cost;vertices+=count;if(geometry)geoFiles++;return true;
  };
  const geometry=visible.filter(a=>a.format==='geojson'&&a.source_id!=='natural-earth') as SizedAsset[];
  const overloaded=geometry.length>budget.files||geometry.reduce((sum,a)=>sum+(a.byte_length??a.bytes??a.count*600),0)>budget.bytes||geometry.reduce((sum,a)=>sum+geometryVertexEstimate(a),0)>budget.vertices;
  let fallback=false;
  // A selected underground/recorded layer must not lose every slot to roads or
  // a coarse fallback. These small special layers still use the same budget.
  for(const a of visible)if(priority(a)<4&&!admit(a))deferred++;
  if(overloaded){
    // Reserve coarse coverage before admitting detail; never exceed the same budget.
    const overview=filtered.filter(a=>a.detail_level==='overview'&&!visible.includes(a)&&(a.max_camera_height===undefined||height<a.max_camera_height)).sort((a,b)=>distance(a)-distance(b));
    for(const a of overview){if(admit(a))fallback=true;}
  }
  for(const a of visible)if(priority(a)>=4&&!admit(a))deferred++;
  return {assets:selected,deferred,bytes,vertices,fallback};
}

export interface WorkBudgetTask {
  ():void;
  /** Exclude an explicitly asynchronous wait without resetting the shared deadline. */
  waitFor<T>(promise:Promise<T>):Promise<T>;
}

/** Share one cooperative CPU budget across concurrent geometry jobs. */
export class FrameWorkBudget {
  private start:number|null=null;private pending:Promise<void>|null=null;private tasks=0;
  // The scheduling deadline uses wall time. Telemetry counts the union of active
  // task intervals, so one waiting job cannot hide another job's synchronous work.
  private activeTasks=0;private cpuStart:number|null=null;private cpuElapsed=0;
  private chunks:number[]=[];private totalChunks=0;private maxChunk=0;
  private maxOperation=0;private maxOperationPhase='';
  // Reserve the remaining part of an 8 ms target for one final geometry call,
  // promise bookkeeping and occasional allocation overhead.
  constructor(private readonly now=()=>performance.now(),private readonly nextFrame=()=>new Promise<void>(resolve=>requestAnimationFrame(()=>setTimeout(resolve,0))),private readonly milliseconds=4){}
  /** Call after fetch/decode so idle network time is excluded from CPU chunks. */
  startTask():WorkBudgetTask {
    if(this.tasks++===0&&!this.pending){this.start=this.now();this.cpuStart=null;this.cpuElapsed=0;}
    this.activeTasks++;this.resumeCpuClock();
    let done=false,waiting=0;
    const finish:WorkBudgetTask=()=>{
      if(done)return;done=true;
      if(waiting===0&&--this.activeTasks===0)this.pauseCpuClock();
      if(--this.tasks===0&&!this.pending){this.record();this.start=null;}
    };
    finish.waitFor=async<T>(promise:Promise<T>):Promise<T>=>{
      if(done)return promise;
      if(waiting++===0&&--this.activeTasks===0)this.pauseCpuClock();
      try{return await promise;}
      finally{if(--waiting===0&&!done){this.activeTasks++;this.resumeCpuClock();}}
    };
    return finish;
  }
  private pauseCpuClock():void {
    if(this.cpuStart===null)return;
    this.cpuElapsed+=Math.max(0,this.now()-this.cpuStart);this.cpuStart=null;
  }
  private resumeCpuClock(unscopedOperation=false):void {
    if(this.cpuStart===null&&this.start!==null&&!this.pending&&(this.activeTasks>0||unscopedOperation))this.cpuStart=this.now();
  }
  private record():void {
    if(this.start===null)return;
    this.pauseCpuClock();const elapsed=this.cpuElapsed;this.cpuElapsed=0;
    this.chunks.push(elapsed);this.totalChunks++;this.maxChunk=Math.max(this.maxChunk,elapsed);
    if(this.chunks.length>512)this.chunks.shift();
  }
  snapshot(){return {chunkP95Ms:percentile(this.chunks,.95),chunkMaxMs:this.maxChunk,chunkCount:this.totalChunks,operationMaxMs:this.maxOperation,operationMaxPhase:this.maxOperationPhase};}
  /** A single synchronous Cesium call can exceed a deadline; report it without truncation. */
  measure<T>(phase:string,operation:()=>T):T {
    const start=this.now();
    try{return operation();}finally{const elapsed=Math.max(0,this.now()-start);if(elapsed>this.maxOperation){this.maxOperation=elapsed;this.maxOperationPhase=phase;}}
  }
  /** Check immediately before execution, not before another job's queued continuation. */
  async run<T>(phase:string,operation:()=>T,signal?:AbortSignal):Promise<T>{
    for(;;){
      if(signal?.aborted)throw new DOMException('Aborted','AbortError');
      if(this.pending||(this.start!==null&&this.now()-this.start>=this.milliseconds)){await this.checkpoint(signal);continue;}
      this.start??=this.now();
      this.resumeCpuClock(true);
      try{return this.measure(phase,operation);}finally{if(this.activeTasks===0)this.pauseCpuClock();}
    }
  }
  private waitForFrame(frame:Promise<void>,signal?:AbortSignal):Promise<void>{
    if(!signal)return frame;
    if(signal.aborted)return Promise.reject(new DOMException('Aborted','AbortError'));
    // rAF can be suspended in a hidden tab. Cancel this waiter, never the shared frame.
    return new Promise<void>((resolve,reject)=>{
      let settled=false;
      const finish=()=>{if(settled)return false;settled=true;signal.removeEventListener('abort',onAbort);return true;};
      const onAbort=()=>{if(finish())reject(new DOMException('Aborted','AbortError'));};
      signal.addEventListener('abort',onAbort,{once:true});
      frame.then(()=>{if(finish())resolve();},error=>{if(finish())reject(error);});
    });
  }
  /** Terrain events run inside Cesium's render callback. Start refresh work after that frame. */
  async defer(signal?:AbortSignal):Promise<void>{
    if(signal?.aborted)throw new DOMException('Aborted','AbortError');
    if(!this.pending){
      if(this.tasks>0)this.record();else{this.pauseCpuClock();this.cpuElapsed=0;}this.start=null;
      this.pending=this.nextFrame().then(()=>{this.start=this.tasks>0?this.now():null;this.pending=null;this.resumeCpuClock();});
    }
    await this.waitForFrame(this.pending,signal);
    if(signal?.aborted)throw new DOMException('Aborted','AbortError');
  }
  async checkpoint(signal?:AbortSignal):Promise<void>{
    if(signal?.aborted)throw new DOMException('Aborted','AbortError');
    if(this.start===null){this.start=this.now();this.resumeCpuClock();}
    if(this.pending||this.now()-this.start>=this.milliseconds){
      if(!this.pending){this.record();this.start=null;this.pending=this.nextFrame().then(()=>{this.start=this.now();this.pending=null;this.resumeCpuClock();});}
      await this.waitForFrame(this.pending,signal);
      if(signal?.aborted)throw new DOMException('Aborted','AbortError');
    }
  }
}
