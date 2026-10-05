import {useEffect,useRef,useState} from 'react';
import {fetchPinnedPropertySearchJson} from './atlas-client';
import {currentSearchReply,type SearchComplex} from './property-global-search';
const sources:Record<string,{url:string;sha256:string;bytes:number}>={
 'property-ceeff63959643461':{url:new URL('./data/property-search-index-ceeff63959643461.json',import.meta.url).href,sha256:'5472abd27fa4eb3c223892eab17a44397428873c7bccb37bb65e8a30dc42114c',bytes:2423276},
};
interface Result {query:string;rows:SearchComplex[];status:'loading'|'ready'|'failed';}
export function usePropertyGlobalSearch(release:string,query:string,region:string,active:boolean){
 const source=sources[release],enabled=active&&query.trim().length>=2&&!!source;
 const [attempt,retry]=useState(0),[result,setResult]=useState<Result>({query:'',rows:[],status:'loading'});
 const worker=useRef<Worker|null>(null),ready=useRef(false),sequence=useRef(0),latest=useRef({query,region});latest.current={query,region};
 useEffect(()=>{
  if(!enabled)return;const controller=new AbortController();let instance:Worker;
  const fail=()=>{if(!controller.signal.aborted){ready.current=false;setResult({query:latest.current.query,rows:[],status:'failed'});}};
  try{instance=new Worker(new URL('./property-search.worker.ts',import.meta.url),{type:'module'});}catch{fail();return()=>controller.abort();}
  worker.current=instance;ready.current=false;
  const send=()=>{const current=latest.current;instance.postMessage({kind:'query',...current,sequence:++sequence.current});};
  instance.onmessage=(event:MessageEvent)=>{
   if(controller.signal.aborted)return;const data=event.data;
   if(data.kind==='ready'){ready.current=true;send();}
   else if(data.kind==='error')fail();
   else if(data.kind==='result'&&currentSearchReply(data,sequence.current,latest.current.query))setResult({query:data.query,rows:data.rows,status:'ready'});
  };
  instance.onerror=fail;
  void fetchPinnedPropertySearchJson(source.url,source,controller.signal).then(value=>{if(!controller.signal.aborted)instance.postMessage({kind:'init',release,value});}).catch(fail);
  return()=>{controller.abort();instance.terminate();if(worker.current===instance)worker.current=null;ready.current=false;sequence.current++;};
 },[enabled,source,release,attempt]);
 useEffect(()=>{if(!enabled)return;sequence.current++;const timer=setTimeout(()=>{if(ready.current)worker.current?.postMessage({kind:'query',query,region,sequence:++sequence.current});},100);return()=>clearTimeout(timer);},[query,region,enabled]);
 return {available:!!source,rows:enabled&&result.query===query&&result.status==='ready'?result.rows:[],status:!enabled?'idle':result.query===query?result.status:'loading',retry:()=>{setResult({query,rows:[],status:'loading'});retry(value=>value+1);}};
}
