import type {Place} from './contracts';
import {PLACES} from './sources';
import {preparePlaceSearch,searchPreparedPlaces,type PlaceSearchIndex,type SearchEntry} from './search';

export interface SearchDescriptor {url:string;sha256:string;}
export interface SearchShard extends SearchDescriptor {byte_length:number;encoding?:'row-lines-v1'|'row-bytes-v1'|'lookup-lines-v1';}
export interface SearchManifestV2 {
  schema_version:2;entry_count:number;candidate_count:number;omitted_count:number;index_limit:50000;
  row_count:number;page_size:number;bucket_count:number;gram_size?:2|3;buckets:SearchShard[];pages:SearchShard[];
}
export interface SearchMetadata {indexed_count:number;candidate_count:number;omitted_count:number;index_limit:number;}
export interface PublishedSearchResult {places:Place[];metadata:SearchMetadata;schema_version:1|2;}
export interface PreparedSearchRow {place:Place;name:string;text:string;}
export type SearchErrorCode='invalid-path'|'missing'|'unavailable'|'integrity'|'invalid-data'|'budget';
export class PublishedSearchError extends Error {
  constructor(public readonly code:SearchErrorCode,message:string){super(message);this.name='PublishedSearchError';}
}
export interface PublishedSearchOptions {
  /** Maximum storage requests per query; cached immutable shards do not consume it. */
  maxRequests?:number;cacheBytes?:number;cacheEntries?:number;
}
interface JsonBucket {schema_version:1;kind:'postings';bucket:number;prefix:Record<string,number[]>;grams:Record<string,number[]>;}
interface LineBucket {schema_version:1;kind:'postings-lines';bucket:number;prefix:string;grams:string;}
type Bucket=JsonBucket|LineBucket;
interface JsonPage {schema_version:1;kind:'records';start:number;rows:PreparedSearchRow[];}
interface LinePage {schema_version:1;kind:'record-lines';start:number;count:number;offsets:number[];records:string;}
interface BytePage {schema_version:1;kind:'record-bytes';start:number;count:number;offsets:number[];records:Uint8Array;}
type Page=JsonPage|LinePage|BytePage;
type Loaded={schema_version:1;prepared:PlaceSearchIndex;metadata:SearchMetadata}|{schema_version:2;manifest:SearchManifestV2;metadata:SearchMetadata};
const LIMIT=50000;
const MAX_SHARD_BYTES=4*1024*1024;
const zeroMetadata:SearchMetadata={indexed_count:0,candidate_count:0,omitted_count:0,index_limit:LIMIT};
const decoder=new TextDecoder();
export const normalizeSearch=(value:string)=>value.normalize('NFKC').toLocaleLowerCase().replace(/\s/g,'');
/** Deterministic UTF-16 FNV-1a, shared with the offline builder. */
export function searchBucket(key:string,count=128):number {
  let hash=2166136261;
  for(let i=0;i<key.length;i++)hash=Math.imul(hash^key.charCodeAt(i),16777619);
  return (hash>>>0)%count;
}
export function searchGrams(value:string,width=2):string[]{
  if(value.length<width)return value?[value]:[];
  const grams=new Set<string>();for(let i=0;i<=value.length-width;i++)grams.add(value.slice(i,i+width));
  return [...grams];
}
function invalid(message='검색 자료 형식이 올바르지 않습니다.'):never {throw new PublishedSearchError('invalid-data',message);}
function isRecord(value:unknown):value is Record<string,unknown>{return !!value&&typeof value==='object'&&!Array.isArray(value);}
function safePath(url:string):boolean{return url.startsWith('/data/')&&!url.includes('..')&&!/[\\?#%]/.test(url)&&![...url].some(char=>char.charCodeAt(0)<32);}
function validDescriptor(value:unknown,withSize=false):value is SearchShard {
  return isRecord(value)&&typeof value.url==='string'&&safePath(value.url)&&typeof value.sha256==='string'&&(!withSize||(/^[0-9a-f]{64}$/.test(value.sha256)&&Number.isInteger(value.byte_length)&&Number(value.byte_length)>0&&Number(value.byte_length)<=MAX_SHARD_BYTES));
}
function metadata(data:Record<string,unknown>,count:number):SearchMetadata {
  const candidate=data.candidate_count===undefined?count:Number(data.candidate_count);
  const omitted=data.omitted_count===undefined?candidate-count:Number(data.omitted_count);
  if(!Number.isInteger(candidate)||candidate<count||!Number.isInteger(omitted)||omitted!==candidate-count)invalid();
  return {indexed_count:count,candidate_count:candidate,omitted_count:omitted,index_limit:LIMIT};
}
function manifest(value:Record<string,unknown>):SearchManifestV2 {
  if(value.schema_version!==2||!Number.isInteger(value.entry_count)||Number(value.entry_count)<0||Number(value.entry_count)>LIMIT||value.index_limit!==LIMIT||!Number.isInteger(value.row_count)||Number(value.row_count)<0||Number(value.row_count)>LIMIT+PLACES.length||value.page_size!==512||value.bucket_count!==128||!Array.isArray(value.buckets)||value.buckets.length!==128||!Array.isArray(value.pages)||value.pages.length!==Math.ceil(Number(value.row_count)/512))invalid();
  if(![...value.buckets,...value.pages].every(item=>validDescriptor(item,true)))invalid();
  if(!value.pages.every(item=>item.encoding===undefined||item.encoding==='row-lines-v1'||item.encoding==='row-bytes-v1'))invalid();
  if(!value.buckets.every(item=>item.encoding===undefined||item.encoding==='lookup-lines-v1'))invalid();
  if(value.gram_size!==undefined&&value.gram_size!==2&&value.gram_size!==3)invalid();
  metadata(value,Number(value.entry_count));
  return value as unknown as SearchManifestV2;
}
function ids(value:unknown,rowCount:number,max=rowCount):number[]{
  if(value===undefined)return [];
  if(!Array.isArray(value)||value.length>max||value.some(id=>!Number.isInteger(id)||id<0||id>=rowCount))invalid();
  return value;
}
function intersect(a:readonly number[],b:readonly number[]):number[]{
  const result:number[]=[];let i=0,j=0;
  while(i<a.length&&j<b.length){if(a[i]===b[j]){result.push(a[i]);i++;j++;}else if(a[i]<b[j])i++;else j++;}
  return result;
}
/** These hash-checked maps contain only JSON string keys and integer arrays.
 * Decode the selected value instead of allocating every unrelated key/list. */
function lookup(bucket:Bucket,kind:'prefix'|'grams',key:string):unknown {
  if(bucket.kind==='postings')return Object.hasOwn(bucket[kind],key)?bucket[kind][key]:undefined;
  const data=bucket[kind],token=JSON.stringify(key)+':';let position=0;
  while((position=data.indexOf(token,position))>=0){
    if(position>0&&(data[position-1]==='{'||data[position-1]===',')){
      const start=position+token.length,end=data.indexOf(']',start);
      if(data[start]!=='['||end<0)invalid();
      try{return JSON.parse(data.slice(start,end+1));}catch{invalid();}
    }
    position+=token.length;
  }
  return undefined;
}

/** Immutable, hash-checked lazy search used by both a browser Worker and the edge API. */
export function createPublishedPlaceSearch(fetcher:typeof fetch=fetch,options:PublishedSearchOptions={}){
  const maxRequests=options.maxRequests??256,maxCacheBytes=options.cacheBytes??8*1024*1024,maxCacheEntries=options.cacheEntries??48;
  const shards=new Map<string,{promise:Promise<unknown>;bytes:number}>();
  const versions=new Map<string,Promise<Loaded>>();
  async function read(descriptor:SearchDescriptor,maxBytes:number,request:{count:number},decode:'json'|'text'|'bytes'='json'):Promise<{value:unknown;bytes:number}>{
    if(!validDescriptor(descriptor))throw new PublishedSearchError('invalid-path','지역 검색 자료 경로가 올바르지 않습니다.');
    if(++request.count>maxRequests)throw new PublishedSearchError('budget','검색 작업량 한도를 넘었습니다. 검색어를 더 구체적으로 입력해 주세요.');
    let response:Response;
    try{response=await fetcher(descriptor.url);}catch{throw new PublishedSearchError('unavailable','검색 자료에 연결할 수 없습니다. 다시 검색해 주세요.');}
    if(response.status===404)throw new PublishedSearchError('missing','이 공개 버전의 검색 자료가 없습니다.');
    if(!response.ok)throw new PublishedSearchError('unavailable','검색 자료를 불러오지 못했습니다. 다시 검색해 주세요.');
    const length=Number(response.headers.get('content-length'));
    if(Number.isFinite(length)&&length>maxBytes)invalid('검색 자료가 허용 크기를 넘었습니다.');
    const bytes=await response.arrayBuffer();if(bytes.byteLength>maxBytes)invalid('검색 자료가 허용 크기를 넘었습니다.');
    if(/^[0-9a-f]{64}$/.test(descriptor.sha256)){
      const actual=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
      if(actual!==descriptor.sha256)throw new PublishedSearchError('integrity','검색 자료의 해시가 공개 목록과 일치하지 않습니다.');
    }
    if(decode==='bytes')return {value:new Uint8Array(bytes),bytes:bytes.byteLength};
    const text=decoder.decode(bytes);
    if(decode==='text')return {value:text,bytes:bytes.byteLength};
    try{return {value:JSON.parse(text),bytes:bytes.byteLength};}catch{invalid();}
  }
  function trim(){
    let bytes=0;for(const entry of shards.values())bytes+=entry.bytes;
    for(const [key,entry] of shards){if(shards.size<=maxCacheEntries&&bytes<=maxCacheBytes)break;shards.delete(key);bytes-=entry.bytes;}
  }
  async function shard(descriptor:SearchShard,request:{count:number}):Promise<unknown>{
    const key=`${descriptor.url}:${descriptor.sha256}`;let entry=shards.get(key);
    if(entry){shards.delete(key);shards.set(key,entry);return entry.promise;}
    const created={bytes:descriptor.byte_length,promise:Promise.resolve(undefined) as Promise<unknown>};
    created.promise=read(descriptor,descriptor.byte_length,request,descriptor.encoding==='row-bytes-v1'?'bytes':descriptor.encoding?'text':'json').then(({value,bytes})=>{
      if(bytes!==descriptor.byte_length)throw new PublishedSearchError('integrity','검색 자료 크기가 공개 목록과 일치하지 않습니다.');
      return value;
    }).catch(error=>{if(shards.get(key)===created)shards.delete(key);throw error;});
    shards.set(key,created);entry=created;trim();return entry.promise;
  }
  async function load(descriptor:SearchDescriptor,request:{count:number}):Promise<Loaded>{
    const key=`${descriptor.url}:${descriptor.sha256}`;let pending=versions.get(key);
    if(pending){versions.delete(key);versions.set(key,pending);return pending;}
    pending=read(descriptor,16*1024*1024,request).then(({value,bytes})=>{
      if(!isRecord(value))invalid();
      if(value.schema_version===2){
        if(bytes>1024*1024)invalid();const current=manifest(value);
        if(!/^[0-9a-f]{64}$/.test(descriptor.sha256))throw new PublishedSearchError('integrity','검색 공개 목록에 유효한 해시가 없습니다.');
        return {schema_version:2 as const,manifest:current,metadata:metadata(value,current.entry_count)};
      }
      if((value.schema_version!==undefined&&value.schema_version!==1)||!Array.isArray(value.entries)||value.entries.length>LIMIT||(value.entry_count!==undefined&&value.entry_count!==value.entries.length))invalid();
      return {schema_version:1 as const,prepared:preparePlaceSearch(value.entries as SearchEntry[]),metadata:metadata(value,value.entries.length)};
    }).catch(error=>{if(versions.get(key)===pending)versions.delete(key);throw error;});
    versions.set(key,pending);while(versions.size>2)versions.delete(versions.keys().next().value!);return pending;
  }
  return async(descriptor:SearchDescriptor|undefined,query:string,control:{signal?:AbortSignal}={}):Promise<PublishedSearchResult>=>{
    const active=()=>{if(control.signal?.aborted)throw new DOMException('검색 요청이 변경되었습니다.','AbortError');};
    active();
    const q=normalizeSearch(query.slice(0,80));
    if(!descriptor)return {places:searchPreparedPlaces(preparePlaceSearch([]),q),metadata:{...zeroMetadata},schema_version:1};
    const request={count:0},loaded=await load(descriptor,request);
    active();
    if(loaded.schema_version===1)return {places:searchPreparedPlaces(loaded.prepared,q),metadata:{...loaded.metadata},schema_version:1};
    if(!q)return {places:PLACES.map(place=>({...place})),metadata:{...loaded.metadata},schema_version:2};
    const current=loaded.manifest;
    const queryBuckets=new Map<number,Promise<Bucket>>(),queryPages=new Map<number,Promise<Page>>();
    function bucket(key:string):Promise<Bucket>{
      active();
      const at=searchBucket(key,current.bucket_count);let pending=queryBuckets.get(at);
      if(!pending){pending=shard(current.buckets[at],request).then(value=>{
        if(current.buckets[at].encoding==='lookup-lines-v1'){
          if(typeof value!=='string')invalid();
          const first=value.indexOf('\n'),second=value.indexOf('\n',first+1),third=value.indexOf('\n',second+1);
          if(first<0||second<0||third!==value.length-1)invalid();
          let header:unknown;try{header=JSON.parse(value.slice(0,first));}catch{invalid();}
          const prefix=value.slice(first+1,second),grams=value.slice(second+1,third);
          if(!isRecord(header)||header.schema_version!==1||header.kind!=='postings-lines'||header.bucket!==at||!prefix.startsWith('{')||!prefix.endsWith('}')||!grams.startsWith('{')||!grams.endsWith('}'))invalid();
          return {...header,prefix,grams} as unknown as LineBucket;
        }
        if(!isRecord(value)||value.schema_version!==1||value.kind!=='postings'||value.bucket!==at||!isRecord(value.prefix)||!isRecord(value.grams))invalid();
        return value as unknown as Bucket;
      });queryBuckets.set(at,pending);}return pending;
    }
    function page(row:number):Promise<Page>{
      active();
      const at=Math.floor(row/current.page_size);let pending=queryPages.get(at);
      if(!pending){pending=shard(current.pages[at],request).then(value=>{
        const expected=Math.min(current.page_size,current.row_count-at*current.page_size);
        if(current.pages[at].encoding==='row-bytes-v1'){
          if(!(value instanceof Uint8Array))invalid();
          const newline=value.indexOf(10);if(newline<0)invalid();
          let header:unknown;try{header=JSON.parse(decoder.decode(value.subarray(0,newline)));}catch{invalid();}
          const records=value.subarray(newline+1);
          if(!isRecord(header)||header.schema_version!==1||header.kind!=='record-bytes'||header.start!==at*current.page_size||header.count!==expected||!Array.isArray(header.offsets)||header.offsets.length!==expected+1||header.offsets[0]!==0||header.offsets.at(-1)!==records.length||header.offsets.some((n,i,all)=>!Number.isInteger(n)||n<0||(i>0&&n<=all[i-1])))invalid();
          return {...header,records} as unknown as BytePage;
        }
        if(current.pages[at].encoding==='row-lines-v1'){
          if(typeof value!=='string')invalid();
          const newline=value.indexOf('\n');if(newline<0)invalid();
          let header:unknown;try{header=JSON.parse(value.slice(0,newline));}catch{invalid();}
          const records=value.slice(newline+1);
          if(!isRecord(header)||header.schema_version!==1||header.kind!=='record-lines'||header.start!==at*current.page_size||header.count!==expected||!Array.isArray(header.offsets)||header.offsets.length!==expected+1||header.offsets[0]!==0||header.offsets.at(-1)!==records.length||header.offsets.some((n,i,all)=>!Number.isInteger(n)||n<0||(i>0&&n<=all[i-1])))invalid();
          return {...header,records} as unknown as LinePage;
        }
        if(!isRecord(value)||value.schema_version!==1||value.kind!=='records'||value.start!==at*current.page_size||!Array.isArray(value.rows)||value.rows.length!==expected)invalid();
        return value as unknown as Page;
      });queryPages.set(at,pending);}return pending;
    }
    async function row(at:number):Promise<PreparedSearchRow>{
      const data=await page(at),offset=at%current.page_size;let record:PreparedSearchRow;
      if(data.kind==='records')record=data.rows[offset];
      else if(data.kind==='record-bytes'){try{record=JSON.parse(decoder.decode(data.records.subarray(data.offsets[offset],data.offsets[offset+1]-1)));}catch{invalid();}}
      else{try{record=JSON.parse(data.records.slice(data.offsets[offset],data.offsets[offset+1]-1));}catch{invalid();}}
      active();
      if(!record||typeof record.name!=='string'||typeof record.text!=='string'||!record.place||typeof record.place.id!=='string'||typeof record.place.name!=='string'||typeof record.place.region!=='string'||!Number.isFinite(record.place.lon)||!Number.isFinite(record.place.lat)||record.place.lon<124||record.place.lon>132.5||record.place.lat<32||record.place.lat>39.5||!Number.isFinite(record.place.range))invalid();
      return record;
    }
    const prefixBucket=await bucket(q);
    const prefix=ids(lookup(prefixBucket,'prefix',q),current.row_count,20);
    const places:Place[]=[],selected=new Set<string>();
    const prefixPages=[...new Set(prefix.map(at=>Math.floor(at/current.page_size)))];
    for(let offset=0;offset<prefixPages.length;offset+=4)await Promise.all(prefixPages.slice(offset,offset+4).map(at=>page(at*current.page_size)));
    for(const at of prefix){const record=await row(at);if(!record.name.startsWith(q))invalid();if(!selected.has(record.place.id)){places.push({...record.place});selected.add(record.place.id);}}
    if(places.length<20){
      const grams=searchGrams(q,Math.min(q.length,current.gram_size??2));
      // The first and last grams reject normal negative queries before any record pages.
      if(grams.length>2)grams.splice(1,0,grams.pop()!);
      let candidates:number[]|undefined;
      for(const gram of grams){
        const data=await bucket(gram),postings=ids(lookup(data,'grams',gram),current.row_count);
        if(postings.some((at,i)=>i>0&&at<=postings[i-1]))invalid();
        candidates=candidates===undefined?postings:intersect(candidates,postings);
        if(!candidates.length)break;
        // Verifying a few candidate pages is cheaper than downloading every gram
        // of a long alias. Full substring checks below retain exact semantics.
        if(new Set(candidates.map(at=>Math.floor(at/current.page_size))).size<=4)break;
      }
      for(const at of candidates??[]){
        const record=await row(at);if(!record.text.includes(q)||selected.has(record.place.id))continue;
        places.push({...record.place});selected.add(record.place.id);if(places.length===20)break;
      }
    }
    return {places,metadata:{...loaded.metadata},schema_version:2};
  };
}
