import {readFile,writeFile,mkdir,stat} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import process from 'node:process';
import {Buffer} from 'node:buffer';
import {performance} from 'node:perf_hooks';
import ts from 'typescript';

const ROOT=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const bytes=value=>Buffer.from(JSON.stringify(value));
const moduleUrls=new Map();
async function sourceModule(name){
  if(moduleUrls.has(name))return moduleUrls.get(name);
  const source=await readFile(path.join(ROOT,'shared',`${name}.ts`),'utf8');
  let compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
  for(const dependency of ['sources','search']){
    if(compiled.includes(`from './${dependency}'`))compiled=compiled.replaceAll(`from './${dependency}'`,`from '${await sourceModule(dependency)}'`);
  }
  const url=`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`;
  moduleUrls.set(name,url);return url;
}
const {preparePlaceSearch}=await import(await sourceModule('search'));
const {searchBucket,searchGrams}=await import(await sourceModule('search-v2'));

/** The exact application preparation/collation code owns the offline row order. */
export function buildSearchFiles(entries,{candidate_count=entries.length,source_sha256='fixture'}={}){
  if(!Array.isArray(entries)||entries.length>50000||!Number.isInteger(candidate_count)||candidate_count<entries.length)throw new Error('Invalid search source coverage');
  const rows=preparePlaceSearch(entries).rows.map(({place,name,text})=>({place,name,text}));
  const buckets=Array.from({length:128},()=>({prefix:new Map(),grams:new Map()}));
  for(let row=0;row<rows.length;row++){
    const record=rows[row];
    for(let size=1;size<=Math.min(80,record.name.length);size++){
      const key=record.name.slice(0,size),map=buckets[searchBucket(key)].prefix;
      const selected=map.get(key)??[];
      const compare=(a,b)=>(rows[a].name===key?0:1)-(rows[b].name===key?0:1)||a-b;
      const previous=selected.findIndex(at=>rows[at].place.id===record.place.id);
      if(previous>=0){if(compare(selected[previous],row)<=0)continue;selected.splice(previous,1);}
      if(selected.length===20&&compare(selected[19],row)<=0)continue;
      const insert=selected.findIndex(at=>compare(at,row)>0);
      if(insert<0)selected.push(row);else selected.splice(insert,0,row);
      if(selected.length>20)selected.pop();map.set(key,selected);
    }
    const grams=new Set([...searchGrams(record.text),...searchGrams(record.text,3)]);
    for(let at=0;at<record.text.length;at++)grams.add(record.text.slice(at,at+1));
    for(const key of grams){const map=buckets[searchBucket(key)].grams;const list=map.get(key)??[];list.push(row);map.set(key,list);}
  }
  const files=new Map(),root=`/data/search-v2/${source_sha256.slice(0,20)}`;
  function addBytes(label,content,extra={}){
    const sha256=hash(content);
    if(content.length>4*1024*1024)throw new Error(`Search shard exceeds 4 MiB: ${label}`);
    const extension=extra.encoding==='row-bytes-v1'?'bin':extra.encoding?'jsonl':'json';
    const url=`${root}/${label}-${sha256.slice(0,16)}.${extension}`;
    files.set(url,content);return {url,sha256,byte_length:content.length,...extra};
  }
  const add=(label,value)=>addBytes(label,bytes(value));
  const ordered=map=>Object.fromEntries([...map].sort(([a],[b])=>a<b?-1:a>b?1:0));
  const bucketDescriptors=buckets.map((bucket,i)=>addBytes(`bucket-${String(i).padStart(3,'0')}`,Buffer.from(JSON.stringify({schema_version:1,kind:'postings-lines',bucket:i})+'\n'+JSON.stringify(ordered(bucket.prefix))+'\n'+JSON.stringify(ordered(bucket.grams))+'\n'),{encoding:'lookup-lines-v1'}));
  const pages=[];
  for(let start=0;start<rows.length;start+=512){
    const lines=rows.slice(start,start+512).map(row=>JSON.stringify(row)),offsets=[0];
    for(const line of lines)offsets.push(offsets.at(-1)+Buffer.byteLength(line,'utf8')+1);
    const header={schema_version:1,kind:'record-bytes',start,count:lines.length,offsets};
    pages.push(addBytes(`page-${String(start/512).padStart(3,'0')}`,Buffer.from(JSON.stringify(header)+'\n'+lines.join('\n')+'\n'),{encoding:'row-bytes-v1'}));
  }
  const manifest={schema_version:2,entry_count:entries.length,candidate_count,omitted_count:candidate_count-entries.length,index_limit:50000,row_count:rows.length,page_size:512,bucket_count:128,gram_size:3,buckets:bucketDescriptors,pages,source_sha256};
  const descriptor=add('manifest',manifest);
  if(files.size>266)throw new Error('Search release exceeds the static file budget');
  return {files,manifest,descriptor};
}

export async function writeSearchRelease(catalogPath=path.join(ROOT,'public/data/catalog.json')){
  const catalog=JSON.parse(await readFile(catalogPath,'utf8'));
  const previous=catalog.assets.find(asset=>asset.format==='search-index');
  if(!previous?.url?.startsWith('/data/')||previous.url.includes('..'))throw new Error('Search source descriptor not found');
  const sourcePath=path.join(ROOT,'public',previous.url),source=await readFile(sourcePath),sourceHash=hash(source);
  if(sourceHash!==previous.sha256)throw new Error('Search source hash mismatch');
  const parsed=JSON.parse(source.toString('utf8'));
  if(parsed.schema_version===2)throw new Error('Use a flat v1 source catalog to rebuild the search release');
  const result=buildSearchFiles(parsed.entries,{candidate_count:parsed.candidate_count??parsed.entries.length,source_sha256:sourceHash});
  for(const [url,content] of result.files){
    const target=path.join(ROOT,'public',url);await mkdir(path.dirname(target),{recursive:true});
    try{const existing=await readFile(target);if(!existing.equals(content))throw new Error(`Immutable search collision: ${url}`);}catch(error){if(error.code!=='ENOENT')throw error;await writeFile(target,content,{flag:'wx'});}
  }
  const output=path.join(ROOT,'.local/search-v2');await mkdir(output,{recursive:true});
  const asset={...previous,...result.descriptor,bytes:result.descriptor.byte_length,version:`${previous.version} / search-v2-5`};
  const report={schema_version:1,source_sha256:sourceHash,source_bytes:(await stat(sourcePath)).size,entry_count:result.manifest.entry_count,candidate_count:result.manifest.candidate_count,omitted_count:result.manifest.omitted_count,row_count:result.manifest.row_count,file_count:result.files.size,total_bytes:[...result.files.values()].reduce((sum,data)=>sum+data.length,0),manifest_bytes:result.descriptor.byte_length,max_shard_bytes:Math.max(...[...result.files.values()].map(data=>data.length)),normalization:'NFKC, locale lowercase, whitespace removal',ranking:'exact name, name prefix, name/region/alias substring; Korean collation; unique source ID',asset};
  await writeFile(path.join(output,'asset.json'),JSON.stringify(asset));
  await writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2));
  return report;
}

export async function auditSearchRelease(){
  const asset=JSON.parse(await readFile(path.join(ROOT,'.local/search-v2/asset.json'),'utf8'));
  const report=JSON.parse(await readFile(path.join(ROOT,'.local/search-v2/report.json'),'utf8'));
  const catalog=JSON.parse(await readFile(path.join(ROOT,'public/data/catalog.json'),'utf8'));
  const original=catalog.assets.find(item=>item.format==='search-index');
  const source=JSON.parse(await readFile(path.join(ROOT,'public',original.url),'utf8'));
  const {searchPreparedPlaces}=await import(await sourceModule('search'));
  const {createPublishedPlaceSearch}=await import(await sourceModule('search-v2'));
  const prepared=preparePlaceSearch(source.entries);
  const queries=['대전','세종','청주','서울','인천공항','부산','제주','울릉','독도','Cheongju','Incheon','airport','항만','산업단지','청주국제공항','없는음절쀍쀍','대전역','서울역','롯데월드타워','한라산','인천국제공항'];
  // Deterministic source sampling exercises uncommon names and aliases too.
  for(let i=0;i<source.entries.length;i+=431){const entry=source.entries[i];queries.push(entry.name,entry.name.slice(1,5));if(entry.alt_names?.[0])queries.push(entry.alt_names[0]);}
  const measurements=[];
  for(const query of [...new Set(queries)].filter(Boolean)){
    let reads=0,downloaded=0;
    const fetcher=async(url)=>{const content=await readFile(path.join(ROOT,'public',url));reads++;downloaded+=content.length;return new globalThis.Response(new Uint8Array(content));};
    const search=createPublishedPlaceSearch(fetcher,{maxRequests:40});
    const expected=searchPreparedPlaces(prepared,query.slice(0,80));
    const begin=performance.now(),cpu=process.cpuUsage();
    const actual=await search(asset,query),used=process.cpuUsage(cpu),milliseconds=performance.now()-begin;
    if(JSON.stringify(actual.places)!==JSON.stringify(expected))throw new Error(`Search oracle mismatch for ${query}`);
    measurements.push({query,results:actual.places.length,requests:reads,bytes:downloaded,wall_ms:Math.round(milliseconds*100)/100,node_cpu_ms:Math.round((used.user+used.system)/10)/100});
  }
  const sorted=measurements.map(item=>item.wall_ms).sort((a,b)=>a-b);
  const audit={schema_version:1,source_sha256:report.source_sha256,manifest_sha256:asset.sha256,oracle_queries:measurements.length,oracle_mismatches:0,max_query_requests:Math.max(...measurements.map(item=>item.requests)),max_query_bytes:Math.max(...measurements.map(item=>item.bytes)),wall_p95_ms:sorted[Math.ceil(sorted.length*.95)-1],measurement_environment:'Node.js local filesystem, cold search cache each query; not Cloudflare CPU or browser FPS',measurements};
  await writeFile(path.join(ROOT,'.local/search-v2/audit.json'),JSON.stringify(audit,null,2));
  return {...audit,measurements:undefined};
}

export async function benchmarkSearchCpu(iterations=60,responseMode='native-response'){
  const asset=JSON.parse(await readFile(path.join(ROOT,'.local/search-v2/asset.json'),'utf8'));
  const manifestBytes=await readFile(path.join(ROOT,'public',asset.url)),manifest=JSON.parse(manifestBytes);
  const contents=new Map([[asset.url,manifestBytes]]);
  for(const shard of [...manifest.buckets,...manifest.pages])contents.set(shard.url,await readFile(path.join(ROOT,'public',shard.url)));
  const arrays=new Map([...contents].map(([url,data])=>[url,new Uint8Array(data).buffer]));
  const {createPublishedPlaceSearch}=await import(await sourceModule('search-v2'));
  const queries=['대전','서울','청주','산업단지','Incheon','Dongcheon','Naju Livestock Agricultural Cooperative Federation','없는음절쀍쀍'];
  const results=[];
  for(const query of queries){
    let reads=0,readBytes=0;
    const fetcher=async(url)=>{
      const content=contents.get(url);if(!content)throw new Error('Missing benchmark fixture');reads++;readBytes+=content.length;
      if(responseMode==='preloaded-buffer')return {ok:true,status:200,headers:{get:()=>null},arrayBuffer:async()=>arrays.get(url)};
      return new globalThis.Response(new Uint8Array(content));
    };
    for(const cache of ['cold','warm']){
      const warm=createPublishedPlaceSearch(fetcher,{maxRequests:40});
      if(cache==='warm')await warm(asset,query);
      reads=0;readBytes=0;
      const begin=performance.now(),cpu=process.cpuUsage();
      for(let n=0;n<iterations;n++){
        const search=cache==='warm'?warm:createPublishedPlaceSearch(fetcher,{maxRequests:40});
        await search(asset,query);
      }
      const delta=process.cpuUsage(cpu);
      results.push({query,cache,iterations,mean_process_cpu_ms:(delta.user+delta.system)/1000/iterations,mean_wall_ms:(performance.now()-begin)/iterations,mean_requests:reads/iterations,mean_bytes:readBytes/iterations});
    }
  }
  const report={schema_version:1,manifest_sha256:asset.sha256,response_mode:responseMode,environment:'Node.js in-memory response bodies; all source files loaded before measurement; process CPU includes native WebCrypto and garbage collection, not a Cloudflare isolate CPU measurement',iterations,results};
  await writeFile(path.join(ROOT,`.local/search-v2/${responseMode==='preloaded-buffer'?'cpu-audit-core':'cpu-audit'}.json`),JSON.stringify(report,null,2));
  return report;
}

if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url){
  const result=process.argv[2]==='--audit'?await auditSearchRelease():process.argv[2]==='--cpu'?await benchmarkSearchCpu():process.argv[2]==='--cpu-core'?await benchmarkSearchCpu(60,'preloaded-buffer'):await writeSearchRelease(process.argv[2]?path.resolve(process.argv[2]):undefined);
  process.stdout.write(`${JSON.stringify(result,null,2)}\n`);
}
