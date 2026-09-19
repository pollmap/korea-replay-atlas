/** Scene-wide backpressure, including Cesium worker preparation and GPU upload. */
export interface PreparingPrimitive {readonly ready:boolean;isDestroyed():boolean;}
interface Ticket {signal:AbortSignal;resolve:(lease:PrimitiveLease)=>void;reject:(error:Error)=>void;abort:()=>void;}
export interface PrimitiveLease {track:(primitive:PreparingPrimitive)=>void;release:()=>void;}
export class PrimitiveAdmission {
  private active=new Set<{primitive?:PreparingPrimitive;release:()=>void}>();
  private waiting:Ticket[]=[];private disposed=false;private peak=0;
  constructor(readonly limit=2){if(!Number.isInteger(limit)||limit<1)throw new Error('Invalid primitive preparation limit');}
  get pending(){return this.active.size;}
  get queued(){return this.waiting.length;}
  get peakPending(){return this.peak;}
  acquire(signal:AbortSignal):Promise<PrimitiveLease>{
    if(this.disposed||signal.aborted)return Promise.reject(new DOMException('Aborted','AbortError'));
    return new Promise((resolve,reject)=>{
      const ticket:Ticket={signal,resolve,reject,abort:()=>{
        this.waiting=this.waiting.filter(item=>item!==ticket);
        signal.removeEventListener('abort',ticket.abort);reject(new DOMException('Aborted','AbortError'));
      }};
      signal.addEventListener('abort',ticket.abort,{once:true});this.waiting.push(ticket);this.drain();
    });
  }
  private drain(){
    while(!this.disposed&&this.active.size<this.limit&&this.waiting.length){
      const ticket=this.waiting.shift()!;ticket.signal.removeEventListener('abort',ticket.abort);
      if(ticket.signal.aborted){ticket.reject(new DOMException('Aborted','AbortError'));continue;}
      const entry:{primitive?:PreparingPrimitive;release:()=>void}={release:()=>{
        ticket.signal.removeEventListener('abort',entry.release);
        if(this.active.delete(entry))this.drain();
      }};
      this.active.add(entry);this.peak=Math.max(this.peak,this.pending);
      ticket.signal.addEventListener('abort',entry.release,{once:true});
      ticket.resolve({release:entry.release,track:primitive=>{
        if(!this.active.has(entry))throw new DOMException('Aborted','AbortError');
        entry.primitive=primitive;
      }});
    }
  }
  /** Called after render: ready is set only after Cesium finishes preparation. */
  poll(){for(const entry of [...this.active])if(entry.primitive&&(entry.primitive.isDestroyed()||entry.primitive.ready))entry.release();}
  dispose(){
    this.disposed=true;
    for(const ticket of [...this.waiting])ticket.abort();
    for(const entry of [...this.active])entry.release();
  }
}
