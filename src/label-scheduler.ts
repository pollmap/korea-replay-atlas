import * as C from 'cesium';

export interface ScheduledLabel {
  position:C.Cartesian3;
  show:boolean;
  destroy:()=>void;
}
interface LabelEntry {
  options:C.Label.ConstructorOptions;
  codePoints:number;
  alive:boolean;
  failed:boolean;
  label?:C.Label;
  generation?:LabelGeneration;
}
interface LabelGeneration {
  collection:C.LabelCollection;
  owners:Set<LabelEntry>;
  codePoints:number;
}
export const LABEL_FRAME_LIMITS={labels:2,codePoints:16} as const;
export const LABEL_GENERATION_CODE_POINTS=2048;

/** Public collections share glyphs across bundles within bounded admission generations. */
export class SceneLabelScheduler {
  private generations=new Set<LabelGeneration>();
  private currentGeneration:LabelGeneration|undefined;
  private entries=new Set<LabelEntry>();
  private pending=new Set<LabelEntry>();
  private disposed=false;
  private inFrame=false;
  private awaitingRender=false;
  private failures=0;
  private created=0;
  private frameMax=0;
  private removePre:()=>void;
  private removePost:()=>void;
  constructor(private readonly scene:C.Scene,private readonly limits:{labels:number;codePoints:number}=LABEL_FRAME_LIMITS){
    if(!Number.isInteger(limits.labels)||limits.labels<1||!Number.isInteger(limits.codePoints)||limits.codePoints<1)throw new Error('라벨 프레임 한도가 올바르지 않습니다.');
    this.removePre=scene.preRender.addEventListener(this.beforeRender);
    this.removePost=scene.postRender.addEventListener(this.afterRender);
  }
  // This is an admission/render boundary, not private atlas/GPU readiness.
  snapshot(){
    let materialized=0,collections=0,generationCodePoints=0;
    for(const generation of this.generations)if(!generation.collection.isDestroyed()){
      materialized+=generation.collection.length;collections++;generationCodePoints+=generation.codePoints;
    }
    // Cumulative source code points admitted to live generations, NOT GPU bytes or unique glyphs.
    return {owned:this.entries.size,queued:this.pending.size,preparing:this.pending.size>0||this.awaitingRender,materialized,collections,generationCodePoints,created:this.created,failed:this.failures,maxPerFrame:this.frameMax};
  }
  private request():void {if(!this.disposed&&!this.scene.isDestroyed())this.scene.requestRender();}
  private enqueue(entry:LabelEntry):void {
    if(!entry.alive||entry.failed||entry.label||entry.options.show===false||this.pending.has(entry))return;
    this.pending.add(entry);this.request();
  }
  add(options:C.Label.ConstructorOptions):ScheduledLabel {
    if(this.disposed||this.scene.isDestroyed())throw new Error('라벨 스케줄러가 종료되었습니다.');
    const codePoints=Array.from(options.text??'').length;
    const entry:LabelEntry={options:{...options,position:C.Cartesian3.clone(options.position)},codePoints,alive:true,failed:false};
    this.entries.add(entry);this.enqueue(entry);
    const setPosition=(value:C.Cartesian3)=>{
      if(!entry.alive||this.disposed)return;
      C.Cartesian3.clone(value,entry.options.position);
      if(entry.label&&!entry.label.isDestroyed())entry.label.position=entry.options.position;
      this.request();
    };
    const setShow=(value:boolean)=>{
      if(!entry.alive||this.disposed||value===(entry.options.show!==false))return;
      entry.options.show=value;
      if(entry.label&&!entry.label.isDestroyed())entry.label.show=value;
      if(value)this.enqueue(entry);else this.pending.delete(entry);
      this.request();
    };
    return {
      get position(){return entry.options.position;},
      set position(value:C.Cartesian3){setPosition(value);},
      get show(){return entry.alive&&entry.options.show!==false;},
      set show(value:boolean){setShow(value);},
      destroy:()=>this.remove(entry),
    };
  }
  private remove(entry:LabelEntry):void {
    if(!entry.alive)return;
    entry.alive=false;this.pending.delete(entry);this.entries.delete(entry);
    const generation=entry.generation;
    if(generation){
      if(entry.label&&!generation.collection.isDestroyed())generation.collection.remove(entry.label);
      generation.owners.delete(entry);
      // Removed labels leave cached glyphs behind; never refund their admission count.
      if(!generation.owners.size)this.releaseGeneration(generation);
    }
    entry.label=undefined;entry.generation=undefined;
    if(!this.entries.size)this.awaitingRender=false;
    this.request();
  }
  private generationFor(codePoints:number):LabelGeneration {
    if(codePoints<=LABEL_GENERATION_CODE_POINTS&&this.currentGeneration&&this.currentGeneration.codePoints+codePoints<=LABEL_GENERATION_CODE_POINTS)return this.currentGeneration;
    const generation:LabelGeneration={collection:this.scene.primitives.add(new C.LabelCollection({scene:this.scene})),owners:new Set(),codePoints:0};
    this.generations.add(generation);
    // One indivisible over-cap name gets its own collection, never a reusable generation.
    if(codePoints<=LABEL_GENERATION_CODE_POINTS)this.currentGeneration=generation;
    return generation;
  }
  private beforeRender=()=>{
    if(this.disposed||this.inFrame||this.scene.isDestroyed())return;
    this.inFrame=true;
    if(!this.scene.primitives.show)return;
    let added=0,cost=0;
    for(const entry of this.pending){
      if(!entry.alive||entry.options.show===false){this.pending.delete(entry);continue;}
      // A long name remains complete and earns the frame by itself; it is not truncated.
      const entryCost=Math.max(1,entry.codePoints);
      if(added>=this.limits.labels||(added>0&&cost+entryCost>this.limits.codePoints))break;
      this.pending.delete(entry);
      let generation:LabelGeneration|undefined;
      try{
        generation=this.generationFor(entry.codePoints);
        entry.label=generation.collection.add(entry.options);entry.generation=generation;
        generation.owners.add(entry);generation.codePoints+=entry.codePoints;
        this.created++;this.awaitingRender=true;
      }catch{
        // Keep source geometry/picks intact and expose failure without a retry loop.
        entry.failed=true;this.failures++;
        if(generation&&!generation.owners.size)this.releaseGeneration(generation);
      }
      added++;cost+=entryCost;
    }
    this.frameMax=Math.max(this.frameMax,added);
  };
  private afterRender=()=>{
    if(this.disposed)return;
    this.inFrame=false;this.awaitingRender=false;
    if(this.pending.size&&this.scene.primitives.show)this.request();
  };
  private releaseGeneration(generation:LabelGeneration):void {
    this.generations.delete(generation);generation.owners.clear();
    if(this.currentGeneration===generation)this.currentGeneration=undefined;
    const collection=generation.collection;
    if(!this.scene.isDestroyed())this.scene.primitives.remove(collection);
    if(!collection.isDestroyed())collection.destroy();
  }
  destroy():void {
    if(this.disposed)return;
    this.disposed=true;this.awaitingRender=false;
    // Stop callbacks before releasing handles and the scene-owned collection.
    this.removePre();this.removePost();this.pending.clear();
    for(const entry of this.entries){entry.alive=false;entry.label=undefined;entry.generation=undefined;}
    this.entries.clear();
    for(const generation of this.generations)this.releaseGeneration(generation);
  }
}
