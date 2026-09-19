interface Job<T> {
  key:string;run:(signal:AbortSignal)=>Promise<T>;controller:AbortController;
  promise:Promise<T|undefined>;resolve:(value:T|undefined)=>void;reject:(error:unknown)=>void;
  state:'queued'|'running'|'done';detach:()=>void;
}

/** A component-lifetime queue. Aborted non-cancellable work retains its slot
 * until it settles, and a replacement with the same key waits behind it. */
export class AssetLoadQueue<T> {
  private queued:Job<T>[]=[];
  private active=new Map<string,Job<T>>();
  private pending=new Map<string,Job<T>>();
  private scheduled=false;
  constructor(private readonly concurrency=4){
    if(!Number.isInteger(concurrency)||concurrency<1)throw new Error('Invalid asset concurrency');
  }
  enqueue(key:string,run:(signal:AbortSignal)=>Promise<T>,signal:AbortSignal):Promise<T|undefined>{
    if(signal.aborted)return Promise.resolve(undefined);
    const existing=this.pending.get(key);
    if(existing&&!existing.controller.signal.aborted)return existing.promise;
    let resolve!:(value:T|undefined)=>void,reject!:(error:unknown)=>void;
    const promise=new Promise<T|undefined>((yes,no)=>{resolve=yes;reject=no;});
    const controller=new AbortController();
    const abort=()=>controller.abort();
    const job:Job<T>={key,run,controller,promise,resolve,reject,state:'queued',detach:()=>signal.removeEventListener('abort',abort)};
    signal.addEventListener('abort',abort,{once:true});
    controller.signal.addEventListener('abort',()=>{
      if(job.state!=='queued')return;
      this.queued=this.queued.filter(item=>item!==job);
      this.finish(job);job.resolve(undefined);this.schedule();
    },{once:true});
    this.pending.set(key,job);this.queued.push(job);this.schedule();
    return promise;
  }
  cancelAll():void{
    for(const job of [...this.queued,...this.active.values()])job.controller.abort();
  }
  private finish(job:Job<T>):void{
    job.state='done';job.detach();
    if(this.pending.get(job.key)===job)this.pending.delete(job.key);
  }
  private schedule():void{
    if(this.scheduled)return;
    this.scheduled=true;
    queueMicrotask(()=>{this.scheduled=false;this.pump();});
  }
  private pump():void{
    while(this.active.size<this.concurrency){
      const index=this.queued.findIndex(job=>!this.active.has(job.key));
      if(index===-1)return;
      const [job]=this.queued.splice(index,1);
      if(job.controller.signal.aborted){this.finish(job);job.resolve(undefined);continue;}
      job.state='running';this.active.set(job.key,job);
      void Promise.resolve().then(()=>job.controller.signal.aborted?undefined:job.run(job.controller.signal))
        .then(job.resolve,job.reject).finally(()=>{
          this.active.delete(job.key);this.finish(job);this.schedule();
        });
    }
  }
}

export function tilesetBudget(count:number,lightweight:boolean):{cacheBytes:number;maximumCacheOverflowBytes:number}{
  const divisor=Math.max(1,count);
  return {cacheBytes:Math.floor((lightweight?128:256)*1024*1024/divisor),
    maximumCacheOverflowBytes:Math.floor((lightweight?64:128)*1024*1024/divisor)};
}

/** Cesium's SSE setter also resets its adaptive memory limit, even for an equal value. */
export function setTilesetScreenSpaceError(tileset:{maximumScreenSpaceError:number},value:number):void {
  if(tileset.maximumScreenSpaceError!==value)tileset.maximumScreenSpaceError=value;
}

/** Coalesce commits without waiting for unrelated downloads to finish. */
export function frameBatch(callback:()=>void,request:(callback:()=>void)=>number,cancel:(id:number)=>void){
  let frame:number|null=null;
  return {notify(){if(frame===null)frame=request(()=>{frame=null;callback();});},
    cancel(){if(frame!==null){cancel(frame);frame=null;}}};
}
