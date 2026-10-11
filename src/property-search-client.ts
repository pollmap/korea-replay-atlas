import {useEffect,useRef,useState,useMemo} from 'react';
import {fetchPinnedPropertySearchJson,fetchPinnedJson} from './atlas-client';
import type {PropertyRelease} from '../shared/property';
import {currentSearchReply,type SearchComplex} from './property-global-search';
const sources:Record<string,{url:string;sha256:string;bytes:number}>={
 'property-ceeff63959643461':{url:new URL('./data/property-search-index-ceeff63959643461.json',import.meta.url).href,sha256:'5472abd27fa4eb3c223892eab17a44397428873c7bccb37bb65e8a30dc42114c',bytes:2423276},
};
interface Result {release:string;query:string;rows:SearchComplex[];status:'loading'|'ready'|'failed';}
export function usePropertyGlobalSearch(manifest:PropertyRelease,origin:string,query:string,region:string,active:boolean){
 const release=manifest.release_id;
 const source=useMemo(()=>manifest.search_index??sources[release],[manifest.search_index,release]),enabled=active&&query.trim().length>=2&&!!source;
 const [attempt,retry]=useState(0),[result,setResult]=useState<Result>({release:'',query:'',rows:[],status:'loading'});
 const worker=useRef<Worker|null>(null),ready=useRef(false),sequence=useRef(0),latest=useRef({query,region});latest.current={query,region};
 useEffect(()=>{
  if(!enabled)return;const controller=new AbortController();let instance:Worker;
  const fail=()=>{if(!controller.signal.aborted){ready.current=false;setResult({release,query:latest.current.query,rows:[],status:'failed'});}};
  try{instance=new Worker(new URL('./property-search.worker.ts',import.meta.url),{type:'module'});}catch{fail();return()=>controller.abort();}
  worker.current=instance;ready.current=false;
  const send=()=>{const current=latest.current;instance.postMessage({kind:'query',...current,sequence:++sequence.current});};
  instance.onmessage=(event:MessageEvent)=>{
   if(controller.signal.aborted)return;const data=event.data;
   if(data.kind==='ready'){ready.current=true;send();}
   else if(data.kind==='error')fail();
   else if(data.kind==='result'&&currentSearchReply(data,sequence.current,latest.current.query))setResult({release,query:data.query,rows:data.rows,status:'ready'});
  };
  instance.onerror=fail;
  const request=manifest.search_index?fetchPinnedJson(source,origin,controller.signal):fetchPinnedPropertySearchJson(source.url,source,controller.signal);
  void request.then(value=>{if(!controller.signal.aborted)instance.postMessage({kind:'init',release,value});}).catch(fail);
  return()=>{controller.abort();instance.terminate();if(worker.current===instance)worker.current=null;ready.current=false;sequence.current++;};
 },[enabled,source,release,origin,manifest.search_index,attempt]);
 useEffect(()=>{if(!enabled)return;sequence.current++;const timer=setTimeout(()=>{if(ready.current)worker.current?.postMessage({kind:'query',query,region,sequence:++sequence.current});},100);return()=>clearTimeout(timer);},[query,region,enabled]);
 const current=result.release===release&&result.query===query;
 return {available:!!source,rows:enabled&&current&&result.status==='ready'?result.rows:[],status:!enabled?'idle':current?result.status:'loading',retry:()=>{setResult({release,query,rows:[],status:'loading'});retry(value=>value+1);}};
}
