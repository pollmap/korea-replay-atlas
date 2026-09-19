import type {Place} from '../shared/contracts';
import {createPublishedPlaceSearch,PublishedSearchError,type SearchDescriptor,type SearchErrorCode} from '../shared/search-v2';

export interface SearchRequest {id:number;query:string;index?:SearchDescriptor;}
export interface SearchResponse {id:number;places:Place[];indexed_count:number;index_limit:50000;error?:string;error_code?:SearchErrorCode;}

/** Query and immutable shard decoding stay off the application thread. */
export function createSearchRunner(fetcher:typeof fetch=fetch){
  const search=createPublishedPlaceSearch(fetcher);
  return async(request:SearchRequest,signal?:AbortSignal):Promise<SearchResponse>=>{
    const base={id:request.id,index_limit:50000 as const};
    try{
      const result=await search(request.index,request.query,{signal});
      return {...base,places:result.places,indexed_count:result.metadata.indexed_count};
    }catch(error){return {...base,places:[],indexed_count:0,error:error instanceof Error?error.message:'검색 자료를 처리하지 못했습니다.',error_code:error instanceof PublishedSearchError?error.code:'invalid-data'};}
  };
}

if(typeof self!=='undefined'&&typeof document==='undefined'){
  const run=createSearchRunner();let latestId=0,controller:AbortController|undefined;
  self.onmessage=(event:MessageEvent<SearchRequest>)=>{
    latestId=event.data.id;controller?.abort();controller=new AbortController();
    void run(event.data,controller.signal).then(result=>{if(result.id===latestId)self.postMessage(result);});
  };
}
