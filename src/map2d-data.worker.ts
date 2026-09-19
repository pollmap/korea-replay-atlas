import type {Asset} from '../shared/contracts';
import {prepareMap2DData,selectMap2DFeature,type Map2DPrepared} from '../shared/map2d';
import type {MapSelection} from '../shared/selection';

export type Map2DWorkerRequest={type:'load';id:number;asset:Asset;bytes:ArrayBuffer}|{type:'pick';id:number;record:number;index:number}|{type:'release';id:number}|{type:'cancel';id:number};
export type Map2DWorkerResponse={id:number;type:'loaded';bytes:ArrayBuffer;featureCount:number;vertexCount:number}|{id:number;type:'picked';selection:MapSelection|null}|{id:number;type:'error';error:string};
const records=new Map<number,{asset:Asset;data:Pick<Map2DPrepared,'records'|'metadata'>}>();
const pending=new Map<number,AbortController>();
self.onmessage=(event:MessageEvent<Map2DWorkerRequest>)=>{
  const request=event.data;
  if(request.type==='release'||request.type==='cancel'){pending.get(request.id)?.abort();records.delete(request.id);return;}
  if(request.type==='pick'){
    const record=records.get(request.record);
    self.postMessage({id:request.id,type:'picked',selection:record?selectMap2DFeature(record.asset,record.data,request.index):null} satisfies Map2DWorkerResponse);return;
  }
  const controller=new AbortController();pending.set(request.id,controller);
  void prepareMap2DData(request.asset,request.bytes,controller.signal).then(data=>{
    if(controller.signal.aborted)return;
    records.set(request.id,{asset:request.asset,data:{records:data.records,metadata:data.metadata}});
    self.postMessage({id:request.id,type:'loaded',bytes:data.bytes,featureCount:data.featureCount,vertexCount:data.vertexCount} satisfies Map2DWorkerResponse,{transfer:[data.bytes]});
  }).catch(error=>{if(!controller.signal.aborted)self.postMessage({id:request.id,type:'error',error:error instanceof Error?error.message:'2D 공간 자료 처리 실패'} satisfies Map2DWorkerResponse);}).finally(()=>pending.delete(request.id));
};
