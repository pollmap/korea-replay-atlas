import {FrameWorkBudget,type WorkBudgetTask} from '../shared/map-performance';

const aborted=()=>new DOMException('Aborted','AbortError');

/** Motion can finish before React commits the new viewport's wanted assets. */
export class StaticWorkGate {
  private revision=0;
  private committedRevision=-1;
  private moving=false;
  private hidden=false;
  private disposed=true;
  private waiters=new Set<{resolve:()=>void;reject:()=>void}>();
  private listeners=new Set<()=>void>();

  get blocked(){return this.disposed||this.moving||this.hidden||this.committedRevision!==this.revision;}
  snapshot(){return {blocked:this.blocked,moving:this.moving,hidden:this.hidden,revision:this.revision,waiting:this.waiters.size};}
  startSession(hidden:boolean):void {
    this.rejectWaiters();this.revision++;this.committedRevision=-1;
    this.moving=false;this.hidden=hidden;this.disposed=false;this.changed();
  }
  beginMotion():void {
    if(this.disposed)return;
    this.moving=true;this.revision++;this.changed();
  }
  endMotion():void {if(!this.disposed){this.moving=false;this.changed();}}
  reserveView():number {this.revision++;this.changed();return this.revision;}
  /** Call only after assigning wanted and aborting jobs absent from this view. */
  commitView(revision:number):boolean {
    if(this.disposed||this.moving||revision!==this.revision)return false;
    this.committedRevision=revision;this.changed();return true;
  }
  setHidden(hidden:boolean):void {this.hidden=hidden;this.changed();}
  subscribe(listener:()=>void):()=>void {this.listeners.add(listener);return()=>{this.listeners.delete(listener);};}
  private changed():void {
    for(const listener of this.listeners)listener();
    if(!this.blocked)for(const waiter of [...this.waiters])waiter.resolve();
  }
  private rejectWaiters():void {for(const waiter of [...this.waiters])waiter.reject();}
  async wait(signal?:AbortSignal):Promise<void> {
    for(;;){
      if(signal?.aborted||this.disposed)throw aborted();
      if(!this.blocked)return;
      await new Promise<void>((resolve,reject)=>{
        let done=false;
        const finish=(error:boolean)=>{
          if(done)return;done=true;this.waiters.delete(waiter);signal?.removeEventListener('abort',abort);
          if(error)reject(aborted());else resolve();
        };
        const abort=()=>finish(true),waiter={resolve:()=>finish(false),reject:abort};
        this.waiters.add(waiter);signal?.addEventListener('abort',abort,{once:true});
      });
      // A newer motion may have started after a previous view released us.
    }
  }
  dispose():void {this.disposed=true;this.revision++;this.changed();this.rejectWaiters();}
}

const WAIT=Symbol('static-work-paused');

/** Geometry uses the same CPU budget, with cancellable admission at every yield. */
export class GatedFrameWorkBudget extends FrameWorkBudget {
  private segments=new Map<symbol,{finish?:WorkBudgetTask;waiting:number}>();
  private unsubscribe:(()=>void)|undefined;
  constructor(private readonly shared:FrameWorkBudget,private readonly gate:StaticWorkGate){super();}
  private synchronize=()=>{
    for(const segment of this.segments.values()){
      if(this.gate.blocked&&segment.finish){segment.finish();segment.finish=undefined;}
      else if(!this.gate.blocked&&!segment.finish&&segment.waiting===0)segment.finish=this.shared.startTask();
    }
  };
  override startTask():WorkBudgetTask {
    const key=Symbol();
    if(!this.unsubscribe)this.unsubscribe=this.gate.subscribe(this.synchronize);
    const segment={finish:this.gate.blocked?undefined:this.shared.startTask(),waiting:0};
    this.segments.set(key,segment);
    const finish:WorkBudgetTask=()=>{
      if(!this.segments.has(key))return;
      segment.finish?.();this.segments.delete(key);
      if(!this.segments.size){this.unsubscribe?.();this.unsubscribe=undefined;}
    };
    finish.waitFor=async<T>(promise:Promise<T>):Promise<T>=>{
      if(!this.segments.has(key))return promise;
      segment.waiting++;
      try{return await (segment.finish?segment.finish.waitFor(promise):promise);}
      finally{segment.waiting--;if(this.segments.has(key))this.synchronize();}
    };
    return finish;
  }
  override snapshot(){return this.shared.snapshot();}
  override measure<T>(phase:string,operation:()=>T):T {return this.shared.measure(phase,operation);}
  override async run<T>(phase:string,operation:()=>T,signal?:AbortSignal):Promise<T>{
    for(;;){
      await this.gate.wait(signal);
      // shared.run may itself await another frame; recheck immediately at execution.
      const result=await this.shared.run(phase,()=>this.gate.blocked?WAIT:operation(),signal);
      if(result!==WAIT)return result as T;
    }
  }
  override async defer(signal?:AbortSignal):Promise<void>{
    await this.gate.wait(signal);await this.shared.defer(signal);await this.gate.wait(signal);
  }
  override async checkpoint(signal?:AbortSignal):Promise<void>{
    await this.gate.wait(signal);await this.shared.checkpoint(signal);await this.gate.wait(signal);
  }
}
