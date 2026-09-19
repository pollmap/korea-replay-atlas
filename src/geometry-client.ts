import type {RenderGeometry} from '../shared/geometry';
/** The worker owns decoding; shared attribute dictionaries stay encoded until a pick is inspected. */
export class GeometryClient {
  private sequence=0;private worker:Worker|null=null;
  private pending=new Map<number,{resolve:(geometry:RenderGeometry)=>void;reject:(error:Error)=>void;detach:()=>void}>();
  load(url:string,omitRoads:boolean,signal:AbortSignal):Promise<RenderGeometry>{
    if(signal.aborted)return Promise.reject(new DOMException('Aborted','AbortError'));
    if(!this.worker){
      this.worker=new Worker(new URL('./geometry.worker.ts',import.meta.url),{type:'module'});
      this.worker.onmessage=(event:MessageEvent<{id:number;geometry?:RenderGeometry;error?:string}>)=>{
        const {id,geometry,error}=event.data,task=this.pending.get(id);if(!task)return;
        this.pending.delete(id);task.detach();if(error||!geometry)task.reject(new Error(error??'빈 공간 자료입니다.'));else task.resolve(geometry);
      };
      this.worker.onerror=()=>this.destroy(new Error('공간 처리 Worker를 시작하지 못했습니다.'));
    }
    const id=++this.sequence;
    return new Promise((resolve,reject)=>{
      const abort=()=>{this.pending.delete(id);signal.removeEventListener('abort',abort);this.worker?.postMessage({id,cancel:true});reject(new DOMException('Aborted','AbortError'));};
      signal.addEventListener('abort',abort,{once:true});
      this.pending.set(id,{resolve,reject,detach:()=>signal.removeEventListener('abort',abort)});
      this.worker!.postMessage({id,url,omitRoads});
    });
  }
  destroy(error:Error=new DOMException('Aborted','AbortError')):void{
    this.worker?.terminate();this.worker=null;
    for(const task of this.pending.values()){task.detach();task.reject(error);}this.pending.clear();
  }
}
