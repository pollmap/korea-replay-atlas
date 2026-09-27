import {createHash} from 'node:crypto';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {afterAll,beforeAll,describe,expect,it,vi} from 'vitest';
import manifestData from '../src/data/property-poi/manifest.json';
import {PLACES} from '../shared/sources';
import {REGION_NAVIGATION_SOURCE,regionNavigation} from '../src/region-navigation';
import {fetchPinnedPoiJson} from '../src/atlas-client';
import {nearbyPoiChunks,parsePoiChunk,poiDistance,poiSourceStates,POI_MAX_QUERY_BYTES,POI_MAX_QUERY_CHUNKS,type PoiManifest} from '../shared/property-poi';
import {DEFAULT_SURROUNDINGS_FILTER,surroundingsView} from '../shared/property-surroundings';

/** Real data-query contract only: not browser UX, network timing, or complete regional coverage. */
const manifest=manifestData as PoiManifest;
const dataRoot=new URL('../src/data/property-poi/',import.meta.url);
const digest=(body:Uint8Array)=>createHash('sha256').update(body).digest('hex');
function existingPlace(id:string){
  const place=PLACES.find(row=>row.id===id);
  if(!place)throw new Error(`Missing existing place fixture: ${id}`);
  return {id,label:place.name,longitude:place.lon,latitude:place.lat,centerEvidence:`shared/sources.ts PLACES:${id}`};
}
function existingRegion(id:string,name:string){
  const navigation=regionNavigation({name},{reference_dates:{sgis:REGION_NAVIGATION_SOURCE.referenceDate}});
  if(!navigation)throw new Error(`Missing existing SGIS camera fixture: ${name}`);
  return {id,label:name,longitude:navigation.place.lon,latitude:navigation.place.lat,centerEvidence:navigation.sourceRecordId};
}
const centers=[
  existingRegion('gyeonggi','경기도 수원시 팔달구'),existingPlace('seoul'),existingPlace('incheon'),
  existingRegion('cheonan','충청남도 천안시 동남구'),existingRegion('asan','충청남도 아산시'),
  existingPlace('sejong'),existingPlace('cheongju'),existingPlace('daejeon'),existingPlace('busan'),
];
interface QueryReport {
  id:string;label:string;longitude:number;latitude:number;centerEvidence:string;
  queryBytes:number;chunks:number;sourceRows:number;rowsWithin3km:number;
  categoryRows:Record<string,number>;positionMethods:Record<string,number>;
  networkRequests:number;peakConcurrentFetches:number;
}
const reports:QueryReport[]=[];
const byFile=new Map(manifest.chunks.map(chunk=>[chunk.file,chunk]));
let active=0,peak=0,requests=0;

beforeAll(()=>{
  vi.stubGlobal('location',{origin:'https://poi-data-query.test'});
  vi.stubGlobal('fetch',vi.fn(async(input:RequestInfo|URL)=>{
    const url=new URL(String(input)),match=/^\/assets\/(poi-[a-f0-9]{64})-dataqa01\.json$/.exec(url.pathname);
    if(url.origin!=='https://poi-data-query.test'||!match||url.search||url.hash)throw new Error('Unexpected fixture request');
    const chunk=byFile.get(`${match[1]}.json`);
    if(!chunk)throw new Error('Fixture requested an unlisted asset');
    requests++;active++;peak=Math.max(peak,active);
    try{
      const body=await readFile(new URL(chunk.file,dataRoot));
      expect(body.byteLength).toBe(chunk.bytes);
      expect(digest(body)).toBe(chunk.sha256);
      // Uses the real four-slot atlas reader and its SHA/size verification.
      return new Response(new Uint8Array(body),{headers:{'content-length':String(body.byteLength)}});
    }finally{active--;}
  }));
});

afterAll(async()=>{
  vi.unstubAllGlobals();
  const path=new URL('../.local/poi-nine-regions-query.json',import.meta.url);
  await mkdir(fileURLToPath(new URL('../.local/',import.meta.url)),{recursive:true});
  await writeFile(path,JSON.stringify({
    schema:1,validation:'real-file data-query contract; not browser UI or timing',
    passed:reports.length===centers.length,source:manifest.source,
    manifestSha256:digest(await readFile(new URL('manifest.json',dataRoot))),
    scope:'Nine representative camera points only; not all locations or complete POI coverage',
    limits:{chunks:POI_MAX_QUERY_CHUNKS,bytes:POI_MAX_QUERY_BYTES,concurrentDownloads:4,radiusMeters:3000},
    maxima:{queryBytes:Math.max(0,...reports.map(row=>row.queryBytes)),chunks:Math.max(0,...reports.map(row=>row.chunks)),
      rowsWithin3km:Math.max(0,...reports.map(row=>row.rowsWithin3km)),peakConcurrentFetches:Math.max(0,...reports.map(row=>row.peakConcurrentFetches))},
    queries:reports,
  },null,2)+'\n','utf8');
});

describe('nine-region real POI data queries (not browser UI)',()=>{
  it.each(centers)('$label reads only pinned nearby chunks and preserves distance/position meaning',async center=>{
    const chunks=nearbyPoiChunks(manifest,center),queryBytes=chunks.reduce((sum,chunk)=>sum+chunk.bytes,0);
    expect(chunks.length).toBeGreaterThan(0);
    expect(chunks.length).toBeLessThanOrEqual(POI_MAX_QUERY_CHUNKS);
    expect(queryBytes).toBeLessThanOrEqual(POI_MAX_QUERY_BYTES);
    expect(chunks.length).toBeLessThan(manifest.chunks.length);
    peak=0;const beforeRequests=requests;
    const controller=new AbortController();
    const groups=await Promise.all(chunks.map(async chunk=>{
      const url=`/assets/${chunk.file.replace('.json','')}-dataqa01.json`;
      const payload=await fetchPinnedPoiJson(url,{sha256:chunk.sha256,bytes:chunk.bytes},controller.signal);
      const records=parsePoiChunk(payload,chunk,center,manifest.source);
      const sourceRows=(payload as {records:Array<{id:string;longitude:number;latitude:number;positionMethod:string}>}).records;
      const rawById=new Map(sourceRows.map(row=>[row.id,row]));
      const expectedIds=sourceRows.filter(row=>poiDistance(center,row)<=3000).map(row=>row.id).sort();
      expect(records.map(row=>row.id).sort()).toEqual(expectedIds);
      for(const row of records){
        const raw=rawById.get(row.id)!;
        expect(row.position).toEqual({longitude:raw.longitude,latitude:raw.latitude,method:raw.positionMethod});
        expect(row.distanceMeters).toBeCloseTo(poiDistance(center,raw),7);
        expect(row.source.url).toBe(`https://www.openstreetmap.org/${row.id}`);
        expect(row.source.asOf).toBe(manifest.source.asOf);
      }
      return records;
    }));
    expect(active).toBe(0);
    expect(peak).toBeLessThanOrEqual(4);
    const records=groups.flat();
    expect(records.length).toBeGreaterThan(0);
    expect(new Set(records.map(row=>row.id)).size).toBe(records.length);
    const scope={complexId:`camera-fixture:${center.id}`,releaseId:manifest.source.sha256};
    const states=poiSourceStates(records,scope,manifest.source);
    const categoryRows:Record<string,number>={},positionMethods:Record<string,number>={};
    for(const category of ['transport','school','life'] as const){
      const view=surroundingsView(states[category],scope,{...DEFAULT_SURROUNDINGS_FILTER,category,radius:3000,sort:'distance'});
      expect(view.state).toBe('partial');
      expect(view.count).toBeNull();
      expect(view.unknownDistances).toBe(0);
      categoryRows[category]=view.records.length;
      for(let i=0;i<view.records.length;i++){
        expect(view.records[i].distanceMeters).toBeGreaterThanOrEqual(0);
        expect(view.records[i].distanceMeters).toBeLessThanOrEqual(3000);
        if(i)expect(view.records[i].distanceMeters!).toBeGreaterThanOrEqual(view.records[i-1].distanceMeters!);
      }
    }
    for(const row of records)positionMethods[row.position!.method]=(positionMethods[row.position!.method]??0)+1;
    reports.push({...center,queryBytes,chunks:chunks.length,sourceRows:chunks.reduce((sum,chunk)=>sum+chunk.count,0),
      rowsWithin3km:records.length,categoryRows,positionMethods,networkRequests:requests-beforeRequests,peakConcurrentFetches:peak});
  });
});
