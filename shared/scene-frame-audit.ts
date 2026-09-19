/** Opt-in QA only: retain actual frame samples without governor resets or idle-gap FPS. */
export interface SceneFrameSample {
  sequence:number;at:number;sceneMs:number;renderMs:number;frameMs:number|null;
  moving:boolean;loading:boolean;
}
type Phase='movingLoading'|'movingReady'|'settledLoading'|'settledReady';
const phase=(sample:SceneFrameSample):Phase=>sample.moving?(sample.loading?'movingLoading':'movingReady'):(sample.loading?'settledLoading':'settledReady');
const p95=(values:number[])=>values.length?[...values].sort((a,b)=>a-b)[Math.min(values.length-1,Math.floor(values.length*.95))]:null;
export class SceneFrameAudit {
  private sequence=0;private samples:SceneFrameSample[]=[];
  constructor(private readonly limit=512){if(!Number.isInteger(limit)||limit<1)throw new Error('Invalid frame sample limit');}
  record(sample:Omit<SceneFrameSample,'sequence'>):void {
    if(![sample.at,sample.sceneMs,sample.renderMs].every(value=>Number.isFinite(value)&&value>=0)||
      (sample.frameMs!==null&&(!Number.isFinite(sample.frameMs)||sample.frameMs<=0)))return;
    this.samples.push({...sample,sequence:++this.sequence});if(this.samples.length>this.limit)this.samples.shift();
  }
  snapshot(){
    const groups={} as Record<Phase,{count:number;sceneP95Ms:number|null;renderP95Ms:number|null;frameP95Ms:number|null}>;
    for(const key of ['movingLoading','movingReady','settledLoading','settledReady'] as const){
      const chosen=this.samples.filter(sample=>phase(sample)===key);
      groups[key]={count:chosen.length,sceneP95Ms:p95(chosen.map(s=>s.sceneMs)),renderP95Ms:p95(chosen.map(s=>s.renderMs)),frameP95Ms:p95(chosen.flatMap(s=>s.frameMs===null?[]:[s.frameMs]))};
    }
    return {count:this.sequence,samples:this.samples.map(sample=>({...sample})),groups};
  }
}
