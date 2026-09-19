import {describe,expect,it,vi} from 'vitest';
import {ensureDownloadGate,publishDownloadGateMetrics,readDownloadGateStatus,type DownloadGateStatus} from '../src/download-gate';
const origin='https://korea-replay.example.workers.dev',version='1234567890abcdef';
const valid:DownloadGateStatus={type:'korea-download-gate-status',protocol:1,version,instance:'worker-instance',limit:4,maxBodyBytes:24*1024*1024,active:0,queued:0,peak:0,completed:0,failed:0,cancelled:0,bytes:0};
function worker(status:unknown=valid,claim?:()=>void){
  return Object.assign(new EventTarget(),{scriptURL:`${origin}/download-gate.js`,state:'activated',postMessage:vi.fn((data:{claim:boolean},ports:MessagePort[])=>{if(data.claim)claim?.();ports[0].postMessage(status);})}) as unknown as ServiceWorker;
}
function setup(){
  const current=worker();
  const registration=Object.assign(new EventTarget(),{active:current,waiting:null,installing:null}) as unknown as ServiceWorkerRegistration;
  const container=Object.assign(new EventTarget(),{controller:current,getRegistration:vi.fn(async()=>registration),register:vi.fn(async()=>registration)}) as unknown as ServiceWorkerContainer;
  const environment={container,origin,secure:true,expectedVersion:version,timeoutMs:300};
  return {current,registration,container,environment};
}
describe('map download gate bootstrap',()=>{
  it('requires a matching controller handshake and uses no-cache script update checks',async()=>{
    const {container,environment}=setup(),result=await ensureDownloadGate(environment);
    expect(result).toMatchObject({state:'controlled',status:{limit:4,version},updatePending:false});
    expect(container.register).toHaveBeenCalledWith('/download-gate.js',{scope:'/',updateViaCache:'none'});
  });
  it('claims the first document before allowing data consumers to mount',async()=>{
    const {container,registration,environment}=setup();
    Object.assign(container,{controller:null});
    const active=worker(valid,()=>{Object.assign(container,{controller:active});container.dispatchEvent(new Event('controllerchange'));});
    Object.assign(registration,{active});
    expect((await ensureDownloadGate(environment)).state).toBe('controlled');
    expect(active.postMessage).toHaveBeenCalledWith({type:'korea-download-gate-status',claim:true},expect.any(Array));
  });
  it('waits for activation instead of reporting control before the worker is ready',async()=>{
    const {container,registration,environment,current}=setup();
    Object.assign(container,{controller:null});Object.assign(registration,{active:null,installing:current});
    let ready=false;const startup=ensureDownloadGate(environment).then(value=>{ready=true;return value;});
    await new Promise(resolve=>setTimeout(resolve,20));expect(ready).toBe(false);
    Object.assign(container,{controller:current});Object.assign(registration,{active:current,installing:null});container.dispatchEvent(new Event('controllerchange'));
    expect((await startup).state).toBe('controlled');
  });
  it('keeps a compatible active gate while an update waits for old clients to close',async()=>{
    const {container,registration,environment}=setup();
    const previous=worker({...valid,version:'aaaaaaaaaaaaaaaa'}),waiting=worker();
    Object.assign(container,{controller:previous});Object.assign(registration,{active:previous,waiting});
    expect(await ensureDownloadGate(environment)).toMatchObject({state:'controlled',updatePending:true,status:{version:'aaaaaaaaaaaaaaaa'}});
    expect(waiting.postMessage).not.toHaveBeenCalled();
  });
  it('falls back promptly on unsupported or insecure browsers',async()=>{
    const {container,environment}=setup();
    expect(await ensureDownloadGate({...environment,container:undefined})).toEqual({state:'unavailable',reason:'unsupported'});
    expect(await ensureDownloadGate({...environment,secure:false})).toEqual({state:'unavailable',reason:'insecure_context'});
    expect(container.register).not.toHaveBeenCalled();
  });
  it('does not replace an unrelated service worker',async()=>{
    const {current,container,environment}=setup();Object.assign(current,{scriptURL:`${origin}/other-application.js`});
    expect(await ensureDownloadGate(environment)).toEqual({state:'unavailable',reason:'different_service_worker'});
    expect(container.register).not.toHaveBeenCalled();
  });
  it('bounds a hung registration and handles a rejected registration',async()=>{
    const {container,environment}=setup();vi.mocked(container.register).mockImplementation(()=>new Promise(()=>{}));
    expect(await ensureDownloadGate({...environment,timeoutMs:20})).toEqual({state:'unavailable',reason:'startup_timeout'});
    vi.mocked(container.register).mockRejectedValue(new Error('blocked'));
    expect(await ensureDownloadGate(environment)).toEqual({state:'unavailable',reason:'registration_failed'});
  });
  it('refuses invalid gate limits/protocol and times out an unresponsive controller',async()=>{
    await expect(readDownloadGateStatus(worker({...valid,limit:8}))).rejects.toThrow('invalid_gate_status');
    await expect(readDownloadGateStatus(worker({...valid,peak:5}))).rejects.toThrow('invalid_gate_status');
    await expect(readDownloadGateStatus(worker({...valid,protocol:2}))).rejects.toThrow('invalid_gate_status');
    const silent=worker();vi.mocked(silent.postMessage).mockImplementation(()=>{});
    await expect(readDownloadGateStatus(silent,20)).rejects.toThrow('handshake_timeout');
  });
  it('accepts optional retry diagnostics and publishes only bounded fields while keeping old workers compatible',async()=>{
    const status:DownloadGateStatus={...valid,retries:2,lastFailure:{pathname:'/data/terrain/tile.terrain',phase:'body',name:'TypeError'}};
    expect(await readDownloadGateStatus(worker(status))).toEqual(status);
    const dataset:DOMStringMap={};publishDownloadGateMetrics(dataset,status);
    expect(dataset).toMatchObject({downloadGateLimit:'4',downloadGateRetries:'2',
      downloadGateLastFailurePathname:'/data/terrain/tile.terrain',downloadGateLastFailurePhase:'body',downloadGateLastFailureErrorName:'TypeError'});
    publishDownloadGateMetrics(dataset,await readDownloadGateStatus(worker(valid)));
    expect(dataset).toMatchObject({downloadGateRetries:'unavailable',downloadGateLastFailurePathname:'',downloadGateLastFailurePhase:'',downloadGateLastFailureErrorName:''});
    publishDownloadGateMetrics(dataset,{...valid,retries:0,lastFailure:null});expect(dataset.downloadGateRetries).toBe('0');
  });
  it.each([
    {retries:-1},{retries:0.5},{retries:'1'},
    {lastFailure:{pathname:'/data/tile?private=hidden',phase:'fetch',name:'TypeError'}},
    {lastFailure:{pathname:'/data/tile',phase:'fetch',name:'private error message'}},
    {lastFailure:{pathname:'/data/tile',phase:'unknown',name:'Error'}},
    {lastFailure:{pathname:'https://external.example/data/tile',phase:'fetch',name:'Error'}},
  ])('rejects malformed or sensitive diagnostic fields %j',async extra=>{
    await expect(readDownloadGateStatus(worker({...valid,...extra}))).rejects.toThrow('invalid_gate_status');
  });
});
