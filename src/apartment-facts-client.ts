import {apartmentFactsIndex,type ApartmentFacts as Facts} from '../shared/property-facts';
export interface FactsIndex {release:string;source:string;retrieved:string;rows:Map<string,Facts>;}
/** Lazy validation/cache per exact release; matching complex IDs do not authorize reuse. */
export function createApartmentFactsLookup(sources:readonly unknown[]){
  const registered=new Map<string,unknown>(),cached=new Map<string,FactsIndex>();
  if(sources.length>32)throw new Error('단지 기본정보 버전 수가 제한을 초과했습니다.');
  for(const entry of sources){
    const release=entry&&typeof entry==='object'&&!Array.isArray(entry)?(entry as Record<string,unknown>).property_release_id:null;
    if(typeof release!=='string'||!/^property-[a-f0-9]{16}$/.test(release)||registered.has(release))throw new Error('단지 기본정보 버전 목록이 올바르지 않습니다.');
    registered.set(release,entry);
  }
  return (release:string):FactsIndex|undefined=>{
    if(!registered.has(release))return undefined;
    let result=cached.get(release);
    if(!result){
      const data=apartmentFactsIndex(registered.get(release));
      if(data.property_release_id!==release)throw new Error('단지 기본정보 버전이 다릅니다.');
      result={release,source:data.source,retrieved:data.retrieved_at,rows:new Map(data.rows.map(row=>[row.complex_id,row]))};
      cached.set(release,result);
    }
    return result;
  };
}

export function createAsyncApartmentFacts(loaders:Readonly<Record<string,()=>Promise<unknown>>>,capacity=4){
  if(!Number.isInteger(capacity)||capacity<1||capacity>8)throw new Error('Invalid facts cache capacity');
  const cache=new Map<string,FactsIndex>(),pending=new Map<string,Promise<FactsIndex|undefined>>();
  const has=(release:string)=>Object.prototype.hasOwnProperty.call(loaders,release);
  const peek=(release:string)=>cache.get(release);
  const load=(release:string):Promise<FactsIndex|undefined>=>{
    if(!has(release))return Promise.resolve(undefined);
    const saved=cache.get(release);if(saved){cache.delete(release);cache.set(release,saved);return Promise.resolve(saved);}
    const waiting=pending.get(release);if(waiting)return waiting;
    const request=Promise.resolve().then(()=>loaders[release]()).then(raw=>{
      const facts=createApartmentFactsLookup([raw])(release);
      if(!facts)throw new Error('단지 기본정보 버전이 다릅니다.');
      cache.set(release,facts);while(cache.size>capacity)cache.delete(cache.keys().next().value!);
      return facts;
    }).finally(()=>{pending.delete(release);});
    pending.set(release,request);return request;
  };
  return {has,peek,load};
}
// Explicit imports keep old pinned releases available without loading their bytes together.
export const apartmentFactsClient=createAsyncApartmentFacts({
  'property-2da3955e5d587c40':()=>import('./data/seoul-apartment-facts-2da3955e5d587c40.json').then(value=>value.default),
  'property-54bf1817fdcc7bd9':()=>import('./data/seoul-apartment-facts-54bf1817fdcc7bd9.json').then(value=>value.default),
  'property-87d1c67336e97209':()=>import('./data/seoul-apartment-facts-87d1c67336e97209.json').then(value=>value.default),
  'property-8879dff1b31ac5f0':()=>import('./data/seoul-apartment-facts-8879dff1b31ac5f0.json').then(value=>value.default),
  'property-8deba5b9951e48da':()=>import('./data/seoul-apartment-facts-8deba5b9951e48da.json').then(value=>value.default),
  'property-b87eea7c1c03dc21':()=>import('./data/seoul-apartment-facts-b87eea7c1c03dc21.json').then(value=>value.default),
  'property-ceeff63959643461':()=>import('./data/seoul-apartment-facts-ceeff63959643461.json').then(value=>value.default),
  'property-eea48453a819f517':()=>import('./data/seoul-apartment-facts.json').then(value=>value.default),
});
