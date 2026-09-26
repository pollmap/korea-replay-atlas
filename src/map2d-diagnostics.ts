interface DiagnosticsClock {
  now:()=>number;
  schedule:(callback:()=>void,delay:number)=>ReturnType<typeof setTimeout>;
  cancel:(timer:ReturnType<typeof setTimeout>)=>void;
}
const clock:DiagnosticsClock={now:()=>performance.now(),schedule:(callback,delay)=>setTimeout(callback,delay),cancel:timer=>clearTimeout(timer)};

/** Diagnostic aggregation must not compete with tile arrivals on every rendered frame. */
export function createMap2DDiagnostics(publish:()=>void,interval=250,timing:DiagnosticsClock=clock){
  let timer:ReturnType<typeof setTimeout>|undefined,last=-Infinity,disposed=false;
  const flush=()=>{
    if(disposed)return;
    if(timer!==undefined){timing.cancel(timer);timer=undefined;}
    last=timing.now();publish();
  };
  return {
    request(){
      if(disposed||timer!==undefined)return;
      const wait=interval-(timing.now()-last);
      if(wait<=0){flush();return;}
      timer=timing.schedule(()=>{timer=undefined;flush();},wait);
    },
    flush,
    flushPending(){if(timer!==undefined)flush();},
    dispose(){disposed=true;if(timer!==undefined)timing.cancel(timer);timer=undefined;},
  };
}

/** Avoid invalidating DOM attributes when only unrelated tile state changed. */
export function writeMap2DDiagnostics(target:Record<string,string|undefined>,values:Record<string,string>):void {
  for(const [key,value] of Object.entries(values))if(target[key]!==value)target[key]=value;
}
