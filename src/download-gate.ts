declare const __DOWNLOAD_GATE_VERSION__:string;

export interface DownloadGateStatus {
  type:'korea-download-gate-status';protocol:1;version:string;instance:string;
  limit:4;maxBodyBytes:number;active:number;queued:number;peak:number;
  completed:number;failed:number;cancelled:number;bytes:number;
  retries?:number;
  lastFailure?:{pathname:string;phase:'queue'|'fetch'|'body'|'response'|'backoff';name:string}|null;
}
export type DownloadGateResult = {state:'controlled';status:DownloadGateStatus;registration:ServiceWorkerRegistration;updatePending:boolean}
  |{state:'unavailable';reason:string};
interface GateEnvironment {
  container?:ServiceWorkerContainer;origin:string;secure:boolean;expectedVersion:string;timeoutMs?:number;
}
const SCRIPT='/download-gate.js';
const managed=(worker:ServiceWorker|null|undefined,origin:string)=>{
  if(!worker)return false;
  try{const url=new URL(worker.scriptURL);return url.origin===origin&&url.pathname===SCRIPT;}catch{return false;}
};
function validStatus(value:unknown):value is DownloadGateStatus {
  if(!value||typeof value!=='object')return false;
  const status=value as DownloadGateStatus;
  return status.type==='korea-download-gate-status'&&status.protocol===1&&status.limit===4&&status.maxBodyBytes===24*1024*1024&&
    typeof status.version==='string'&&/^[a-f0-9]{16}$/.test(status.version)&&typeof status.instance==='string'&&
    ['active','queued','peak','completed','failed','cancelled','bytes'].every(key=>Number.isSafeInteger(status[key as keyof DownloadGateStatus])&&Number(status[key as keyof DownloadGateStatus])>=0)&&
    status.active<=4&&status.peak<=4&&
    (status.retries===undefined||Number.isSafeInteger(status.retries)&&status.retries>=0)&&
    (status.lastFailure===undefined||status.lastFailure===null||
      typeof status.lastFailure==='object'&&typeof status.lastFailure.pathname==='string'&&
      status.lastFailure.pathname.startsWith('/data/')&&!/[?#\\]/.test(status.lastFailure.pathname)&&
      Array.from(status.lastFailure.pathname).every(character=>character.charCodeAt(0)>=32)&&
      ['queue','fetch','body','response','backoff'].includes(status.lastFailure.phase)&&
      typeof status.lastFailure.name==='string'&&/^[A-Za-z][A-Za-z0-9]{0,63}$/.test(status.lastFailure.name));
}
export function publishDownloadGateMetrics(dataset:DOMStringMap,status:DownloadGateStatus):void{
  for(const key of ['version','instance','limit','active','queued','peak','completed','failed','cancelled','bytes'] as const){
    dataset[`downloadGate${key[0].toUpperCase()}${key.slice(1)}`]=String(status[key]);
  }
  // Older compatible workers have no retry diagnostics; absence must not imply zero.
  dataset.downloadGateRetries=status.retries===undefined?'unavailable':String(status.retries);
  dataset.downloadGateLastFailurePathname=status.lastFailure?.pathname??'';
  dataset.downloadGateLastFailurePhase=status.lastFailure?.phase??'';
  dataset.downloadGateLastFailureErrorName=status.lastFailure?.name??'';
}
export function readDownloadGateStatus(worker:ServiceWorker,timeoutMs=1500,claim=false):Promise<DownloadGateStatus>{
  return new Promise((resolve,reject)=>{
    const channel=new MessageChannel();
    const finish=(error?:Error,status?:DownloadGateStatus)=>{
      clearTimeout(timer);channel.port1.close();channel.port2.close();
      if(error)reject(error);else resolve(status!);
    };
    const timer=setTimeout(()=>finish(new Error('handshake_timeout')),timeoutMs);
    channel.port1.onmessage=(event:MessageEvent<unknown>)=>{
      if(validStatus(event.data))finish(undefined,event.data);
      else finish(new Error('invalid_gate_status'));
    };
    try{worker.postMessage({type:'korea-download-gate-status',claim},[channel.port2]);}
    catch(error){finish(error instanceof Error?error:new Error('handshake_failed'));}
  });
}
function changed(container:ServiceWorkerContainer,registration:ServiceWorkerRegistration,timeout:number):Promise<void>{
  return new Promise(resolve=>{
    const worker=registration.installing??registration.waiting;
    const finish=()=>{clearTimeout(timer);container.removeEventListener('controllerchange',finish);registration.removeEventListener('updatefound',finish);worker?.removeEventListener('statechange',finish);resolve();};
    const timer=setTimeout(finish,Math.max(1,Math.min(timeout,250)));
    container.addEventListener('controllerchange',finish);registration.addEventListener('updatefound',finish);worker?.addEventListener('statechange',finish);
  });
}

/** Never mount data consumers until control is proven, or the bounded fallback is explicit. */
export async function ensureDownloadGate(environment:GateEnvironment):Promise<DownloadGateResult>{
  const {container,origin,secure,expectedVersion}=environment;
  if(!secure||!container)return {state:'unavailable',reason:secure?'unsupported':'insecure_context'};
  const timeout=environment.timeoutMs??8000;
  const deadline=Date.now()+timeout;
  let timer:ReturnType<typeof setTimeout>|undefined;
  let expired=false;
  const startup=(async():Promise<DownloadGateResult>=>{
    const existing=await container.getRegistration('/');
    if(expired)return {state:'unavailable',reason:'startup_timeout'};
    if([existing?.active,existing?.waiting,existing?.installing,container.controller].some(worker=>worker&&!managed(worker,origin)))return {state:'unavailable',reason:'different_service_worker'};
    const registration=await container.register(SCRIPT,{scope:'/',updateViaCache:'none'});
    while(!expired&&Date.now()<deadline){
      const controller=container.controller;
      const candidate=controller??registration.active;
      if(candidate&&managed(candidate,origin)){
        try{
          const status=await readDownloadGateStatus(candidate,Math.min(1500,Math.max(1,deadline-Date.now())),!controller);
          if(expired)return {state:'unavailable',reason:'startup_timeout'};
          if(container.controller===candidate)return {state:'controlled',status,registration,updatePending:!!registration.waiting||status.version!==expectedVersion};
        }catch{if(Date.now()>=deadline)break;}
      }
      await changed(container,registration,deadline-Date.now());
    }
    return {state:'unavailable',reason:'startup_timeout'};
  })().catch(()=>({state:'unavailable',reason:'registration_failed'} as const));
  try{
    return await Promise.race([startup,new Promise<DownloadGateResult>(resolve=>{timer=setTimeout(()=>{expired=true;resolve({state:'unavailable',reason:'startup_timeout'});},timeout);})]);
  }finally{if(timer!==undefined)clearTimeout(timer);}
}

export async function startDownloadGate():Promise<()=>void>{
  const dataset=document.body.dataset;
  const expectedVersion=__DOWNLOAD_GATE_VERSION__;
  dataset.downloadGate='initializing';dataset.downloadGateExpectedVersion=expectedVersion;
  const root=document.getElementById('root');if(root)root.textContent='지도를 준비하고 있습니다…';
  const notice=(text:string)=>{
    let element=document.getElementById('download-gate-notice');
    if(!text){element?.remove();return;}
    if(!element){element=document.createElement('p');element.id='download-gate-notice';element.setAttribute('role','status');document.body.appendChild(element);}
    element.textContent=text;
  };
  const unavailable=(reason:string)=>{
    dataset.downloadGate='unavailable';dataset.downloadGateReason=reason;dataset.downloadGateLimit='unavailable';
    notice('이 브라우저에서는 지도 불러오기 최적화가 제한됩니다.');
  };
  const publish=(status:DownloadGateStatus,pending:boolean)=>{
    dataset.downloadGate='controlled';dataset.downloadGateReason='';
    publishDownloadGateMetrics(dataset,status);
    dataset.downloadGateUpdate=pending?'pending':'current';
    notice(pending?'지도 불러오기 개선이 준비되었습니다. 지도 탭을 모두 닫고 다시 열면 적용됩니다.':'');
  };
  let container:ServiceWorkerContainer|undefined;
  try{container=navigator.serviceWorker;}catch{unavailable('unsupported');return ()=>{};}
  const result=await ensureDownloadGate({container,origin:location.origin,secure:isSecureContext,expectedVersion});
  if(result.state==='unavailable'){unavailable(result.reason);return ()=>{};}
  publish(result.status,result.updatePending);
  let disposed=false,polling=false;
  const refresh=async()=>{
    if(disposed||polling||document.hidden)return;
    polling=true;
    try{
      const controller=container?.controller;
      if(!managed(controller,location.origin))throw new Error('controller_lost');
      const status=await readDownloadGateStatus(controller!);
      if(!disposed)publish(status,!!result.registration.waiting||status.version!==expectedVersion);
    }catch{if(!disposed)unavailable('controller_unconfirmed');}
    finally{polling=false;}
  };
  const interval=window.setInterval(()=>{void refresh();},1000);
  const update=()=>{void refresh();};
  container?.addEventListener('controllerchange',update);document.addEventListener('visibilitychange',update);
  return ()=>{disposed=true;clearInterval(interval);container?.removeEventListener('controllerchange',update);document.removeEventListener('visibilitychange',update);};
}
