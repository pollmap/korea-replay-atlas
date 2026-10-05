import {parseSearchIndex,searchComplexIndex,type SearchEntry} from './property-global-search';
let index:SearchEntry[]=[];
self.onmessage=(event:MessageEvent)=>{
 const data=event.data;
 try{
  if(data.kind==='init'){index=parseSearchIndex(data.value,data.release);self.postMessage({kind:'ready'});}
  else if(data.kind==='query'){self.postMessage({kind:'result',sequence:data.sequence,query:data.query,rows:searchComplexIndex(index,data.query,data.region)});}
 }catch{self.postMessage({kind:'error'});}
};
