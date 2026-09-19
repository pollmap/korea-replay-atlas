type Triple=readonly[number,number,number];
/** World position in metres and unit directions. Frustum is FOV/aspect or CSS canvas size, never resolution-scaled drawing buffer size. */
export interface CameraPose {position:Triple;direction:Triple;up:Triple;frustum:readonly[number,number];}
const POSITION_EPSILON=.01,ANGLE_EPSILON=1e-8;
const validPose=(pose:CameraPose)=>[pose.position,pose.direction,pose.up].every(vector=>vector.length===3&&vector.every(Number.isFinite))&&
  pose.frustum.length===2&&pose.frustum.every(value=>Number.isFinite(value)&&value>0)&&Math.hypot(...pose.direction)>0&&Math.hypot(...pose.up)>0;
const distance=(a:Triple,b:Triple)=>Math.hypot(a[0]-b[0],a[1]-b[1],a[2]-b[2]);
const changed=(a:CameraPose,b:CameraPose)=>distance(a.position,b.position)>POSITION_EPSILON||distance(a.direction,b.direction)>ANGLE_EPSILON||
  distance(a.up,b.up)>ANGLE_EPSILON||a.frustum.some((value,index)=>Math.abs(value-b.frustum[index])>ANGLE_EPSILON);
const copy=(pose:CameraPose):CameraPose=>({position:[...pose.position],direction:[...pose.direction],up:[...pose.up],frustum:[...pose.frustum]});

/** Hold coarse quality across a burst of wheel gestures before loading detail.
 * Native events remain supported; observe() also handles missing native moveEnd
 * or a later movement whose native moveStart was suppressed by numerical jitter.
 */
export function createCameraSettler(onSettled:()=>void,delayMs=350,onMoving?:()=>void){
  let timer:ReturnType<typeof setTimeout>|undefined,disposed=false,moving=false,settleDue=false,invalidObservation=false;
  let anchor:CameraPose|undefined;
  const cancel=()=>{clearTimeout(timer);timer=undefined;settleDue=false;};
  const finish=()=>{cancel();moving=false;onSettled();};
  const start=()=>{
    if(disposed)return;
    cancel();if(!moving){moving=true;onMoving?.();}
  };
  const end=()=>{
    cancel();
    if(disposed||(anchor&&!moving))return;
    timer=setTimeout(()=>{
      timer=undefined;if(disposed)return;
      // A paused/background render loop cannot prove that an unfinished flight
      // stopped. Confirm the deadline with the next valid observed pose.
      if(anchor)settleDue=true;else finish();
    },delayMs);
  };
  return {
    start,end,
    observe:(pose:CameraPose):boolean=>{
      if(disposed)return false;
      if(!validPose(pose)){cancel();invalidObservation=true;return false;}
      if(!anchor){anchor=copy(pose);invalidObservation=false;if(moving)end();return false;}
      // Keep this anchor until a meaningful change. Updating it for every tiny
      // sample would hide a slow camera that accumulates substantial movement.
      if(changed(anchor,pose)){anchor=copy(pose);invalidObservation=false;start();end();return true;}
      if(invalidObservation){invalidObservation=false;if(moving)end();return false;}
      if(settleDue&&moving)finish();
      return false;
    },
    dispose:()=>{disposed=true;cancel();anchor=undefined;},
  };
}
