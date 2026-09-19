import {compileGeometry,type GeoCollection} from '../shared/geometry';
export interface GeometryRequest {id:number;url:string;omitRoads:boolean;cancel?:boolean;}
const controllers=new Map<number,AbortController>();
self.onmessage=(event:MessageEvent<GeometryRequest>)=>{
  const request=event.data;
  if(request.cancel){controllers.get(request.id)?.abort();return;}
  const controller=new AbortController();controllers.set(request.id,controller);
  void(async()=>{
    try{
      if(!request.url.startsWith('/data/')||request.url.includes('..')||request.url.includes('\\'))throw new Error('공간 자료 경로가 올바르지 않습니다.');
      const response=await fetch(request.url,{signal:controller.signal});if(!response.ok)throw new Error(`HTTP ${response.status}`);
      const data=await response.json() as GeoCollection;
      if(controller.signal.aborted)return;
      // Keep shared source metadata shared through structured clone; picks restore it on demand.
      const geometry=compileGeometry(data,request.omitRoads,{preserveMetadata:true});
      if(!controller.signal.aborted)self.postMessage({id:request.id,geometry},{transfer:[geometry.coordinates.buffer as ArrayBuffer]});
    }catch(error){if(!controller.signal.aborted)self.postMessage({id:request.id,error:error instanceof Error?error.message:'공간 자료 처리 실패'});}
    finally{controllers.delete(request.id);}
  })();
};
