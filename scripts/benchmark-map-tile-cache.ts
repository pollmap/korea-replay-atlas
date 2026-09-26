import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {tileIdToZxy} from 'pmtiles';
import {createMapTilesProtocol} from '../src/map-tiles-protocol';
import type {MapCatalog2D} from '../shared/map-tiles';

// Measures identical verified local archive bytes after warm-up. This isolates
// protocol/decompression cost; it does NOT measure GPU frames or network startup.
const root=path.resolve(process.argv[2]);
const publication=JSON.parse(await readFile(path.join(root,'publication.json'),'utf8'));
const catalog:MapCatalog2D=JSON.parse(await readFile(path.join(root,publication.map_catalog.path),'utf8'));
const topic=catalog.topics.find(t=>t.id==='buildings-overview-0');
if(!topic)throw new Error('Requires audited detailed buildings');
const chunk=[...topic.chunks].sort((a,b)=>b.byte_length-a.byte_length)[0];
const [z,x,y]=tileIdToZxy(process.argv[3]?Number(process.argv[3]):chunk.first_tile_id);
const url=`krtile://${catalog.release_id}/${topic.id}/${z}/${x}/${y}`,origin='https://benchmark.invalid';
const hash=(body:ArrayBuffer)=>createHash('sha256').update(new Uint8Array(body)).digest('hex');
const percentile=(values:number[],q:number)=>[...values].sort((a,b)=>a-b)[Math.min(values.length-1,Math.floor(values.length*q))];
let expected='';
const outcomes=[];
for(const enabled of [false,true]){
  const timings:number[]=[];let decodedBytes=0;
  const api=createMapTilesProtocol({catalog,origin,allowedOrigins:[origin],cacheDecodedTiles:enabled,
    fetcher:async input=>new Response(await readFile(path.join(root,new URL(String(input)).pathname)))});
  const warm=await api.protocol({url},new AbortController());
  expected||=hash(warm.data);if(hash(warm.data)!==expected)throw new Error('Byte mismatch');
  decodedBytes=warm.data.byteLength;
  for(let sample=0;sample<10;sample++){
    const start=performance.now();
    for(let repeat=0;repeat<100;repeat++){
      const result=await api.protocol({url},new AbortController());
      if(result.data.byteLength!==decodedBytes)throw new Error('Detached cache');
    }
    timings.push((performance.now()-start)/100);
  }
  if(hash((await api.protocol({url},new AbortController())).data)!==expected)throw new Error('Byte mismatch');
  outcomes.push({cache:enabled,samples:10,callsPerSample:100,medianMs:percentile(timings,.5),p95Ms:percentile(timings,.95),timings,snapshot:api.snapshot(),decodedBytes});
  api.dispose();if(api.snapshot().decodedTileBytes!==0)throw new Error('Cache lifetime leak');
}
console.log(JSON.stringify({scope:'warm local protocol decode, not end-to-end rendering',release:catalog.release_id,url,sha256:expected,outcomes,medianSpeedup:outcomes[0].medianMs/outcomes[1].medianMs},null,2));
