import {pagesSnapshot,validateRuntimeV2,type RuntimeV2} from '../shared/runtime-v2';
import type {TransitCatalogRef} from '../shared/transit-catalog';

export interface PagesPolicy {
  release_id:string;artifact_sha256:string;snapshot_origin:string|null;
  data:RuntimeV2['data'];bus_catalog?:TransitCatalogRef;
}
export interface PagesEnvironment {
  ASSETS:{fetch(request:Request):Promise<Response>};
  KOREA_API?:{fetch(request:Request):Promise<Response>};
}
interface StaticApplication {
  fetch(request:Request,environment:Record<string,unknown>,context?:unknown):Promise<Response>|Response;
}
function json(value:unknown,status=200):Response {
  return Response.json(value,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
}
function runtime(policy:PagesPolicy,origin:string):RuntimeV2 {
  let snapshot:RuntimeV2['snapshot']=null;
  if(origin==='https://korea-replay.pages.dev')snapshot=policy.snapshot_origin?pagesSnapshot(policy.snapshot_origin,'korea-replay'):null;
  else snapshot=pagesSnapshot(origin,'korea-replay');
  return validateRuntimeV2({schema_version:2,platform:'cloudflare-pages',project:'korea-replay',
    release_id:policy.release_id,artifact_sha256:policy.artifact_sha256,snapshot,data:policy.data});
}
async function boundedJson(response:Response):Promise<Record<string,unknown>> {
  const reader=response.body?.getReader();if(!reader)throw new Error('Missing target response');
  const chunks:Uint8Array[]=[];let size=0;
  try{while(true){const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>1024*1024)throw new Error('Target response exceeds limit');chunks.push(part.value);}}
  finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  const buffer=new Uint8Array(size);let offset=0;for(const part of chunks){buffer.set(part,offset);offset+=part.byteLength;}
  const value:unknown=JSON.parse(new TextDecoder().decode(buffer));
  if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Invalid target response');
  return value as Record<string,unknown>;
}
/** Static APIs use this deployment's ASSETS. Only current observations cross the service binding. */
export function createPagesAdapter(application:StaticApplication,policy:PagesPolicy) {
  // Validate all fixed references before any request can use a binding.
  runtime(policy,'https://korea-replay.pages.dev');
  return {async fetch(request:Request,env:PagesEnvironment,context?:unknown):Promise<Response>{
    const url=new URL(request.url);
    if(request.method!=='GET'&&request.method!=='HEAD')return json({error:'method_not_allowed'},405);
    if(!url.pathname.startsWith('/api/'))return env.ASSETS.fetch(request);
    if(url.pathname==='/api/v2/runtime'){
      try{const response=json(runtime(policy,url.origin));return request.method==='HEAD'?new Response(null,response):response;}
      catch{return json({error:'unverified_deployment_origin'},409);}
    }
    if(url.pathname.startsWith('/api/v1/live/')){
      if(!env.KOREA_API)return json({error:'live_service_unavailable'},503);
      try{
        const target=url.pathname==='/api/v1/live/transit/targets';
        const headers=new Headers();if(request.headers.has('Accept'))headers.set('Accept',request.headers.get('Accept')!);
        const upstream=await env.KOREA_API.fetch(new Request(`https://korea-replay.internal${url.pathname}${url.search}`,{
          method:target?'GET':request.method,headers,signal:AbortSignal.any([request.signal,AbortSignal.timeout(20000)]),redirect:'manual',
        }));
        if(upstream.status>=300&&upstream.status<400){await upstream.body?.cancel();return json({error:'live_service_redirect_refused'},502);}
        if(target&&upstream.ok){
          const value=await boundedJson(upstream),old=value.bus_catalog as {configured?:unknown}|undefined;
          // A future live-service catalog must never point outside this snapshot's asset closure.
          delete value.bus_catalog;
          if(policy.bus_catalog)value.bus_catalog={...policy.bus_catalog,configured:old?.configured===true};
          const response=json(value);return request.method==='HEAD'?new Response(null,response):response;
        }
        const output=new Headers({'X-Content-Type-Options':'nosniff'});
        for(const name of ['Content-Type','Cache-Control','ETag','Last-Modified','Retry-After'])if(upstream.headers.has(name))output.set(name,upstream.headers.get(name)!);
        if(!output.has('Cache-Control'))output.set('Cache-Control','no-store');
        return new Response(upstream.body,{status:upstream.status,headers:output});
      }catch{return json({error:'live_service_unavailable'},503);}
    }
    // No credentials, collector switch, database or broker binding enter the copied static handler.
    return application.fetch(request,{ASSETS:env.ASSETS,ENVIRONMENT:'production',DATA_STORAGE:'static',
      COLLECTORS_ENABLED:'false',STATIC_RELEASE_ID:policy.release_id,LIVE_TRANSIT_MODE:'broker'},context);
  }};
}
