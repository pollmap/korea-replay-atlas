import {atlasFetch} from './atlas-client';
import {parsePropertyMapPoints,type PropertyMapPoint} from '../shared/property-map-point';
import manifest from './data/property-navigation-manifest.json';
import historyManifest from './data/property-navigation-manifest-54bf1817fdcc7bd9.json';

interface NavigationSource {
  manifest:{release_id:string;sha256:string;bytes:number;source_sha256:string};
  url:string;
}

/** Each audited release owns its download and index; IDs never bridge releases. */
export function createPropertyMapPointLookup(sources:readonly NavigationSource[],request:typeof atlasFetch=atlasFetch) {
  const registered=new Map<string,NavigationSource>(),pending=new Map<string,Promise<Map<string,PropertyMapPoint>>>();
  if(sources.length>32)throw new Error('단지 위치 자료 버전 수가 제한을 초과했습니다.');
  for(const entry of sources){
    const meta=entry.manifest;
    if(!/^property-[a-f0-9]{16}$/.test(meta.release_id)||registered.has(meta.release_id)||!Number.isSafeInteger(meta.bytes)||meta.bytes<=0||meta.bytes>256*1024||!/^[a-f0-9]{64}$/.test(meta.sha256)||!/^[a-f0-9]{64}$/.test(meta.source_sha256)||!entry.url)throw new Error('단지 위치 자료 목록이 올바르지 않습니다.');
    registered.set(meta.release_id,entry);
  }
  const supports=(id:string,release:string)=>registered.has(release)&&/^molit-apt:11\d{3}:[A-Za-z0-9_-]{1,64}$/.test(id);
  const find=async(id:string,release:string):Promise<PropertyMapPoint|null>=>{
    if(!supports(id,release))return null;
    let job=pending.get(release);
    if(!job){
      const entry=registered.get(release)!,controller=new AbortController();
      let timer:ReturnType<typeof setTimeout>|undefined;
      const timeout=new Promise<never>((_,reject)=>{timer=setTimeout(()=>{controller.abort();reject(new Error('단지 위치 조회 시간이 초과됐습니다. 다시 시도해 주세요.'));},30000);});
      const load=(async()=>{
        const response=await request(entry.url,{signal:controller.signal});
        if(!response.ok)throw new Error('단지 제공 위치를 불러오지 못했습니다.');
        const body=await response.arrayBuffer();
        if(body.byteLength!==entry.manifest.bytes||body.byteLength>256*1024)throw new Error('단지 위치 자료의 크기가 다릅니다.');
        const sha=[...new Uint8Array(await crypto.subtle.digest('SHA-256',body))].map(v=>v.toString(16).padStart(2,'0')).join('');
        if(sha!==entry.manifest.sha256)throw new Error('단지 위치 자료의 내용을 검증하지 못했습니다.');
        return parsePropertyMapPoints(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(body)),release,entry.manifest.source_sha256);
      })();
      job=Promise.race([load,timeout]).finally(()=>clearTimeout(timer));
      pending.set(release,job);
      const current=job;
      void job.catch(()=>{if(pending.get(release)===current)pending.delete(release);});
    }
    return (await job).get(id)??null;
  };
  return {supports,find};
}

// Add each newly audited manifest and its own generated JSON here. Keep older entries for pinned shares.
const lookup=createPropertyMapPointLookup([
  {manifest,url:new URL('./data/seoul-property-navigation.json',import.meta.url).href},
  {manifest:historyManifest,url:new URL('./data/seoul-property-navigation-54bf1817fdcc7bd9.json',import.meta.url).href},
]);
export const supportsPropertyMapPoint=lookup.supports;
export const findPropertyMapPoint=lookup.find;
