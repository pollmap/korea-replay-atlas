import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {fileURLToPath,pathToFileURL,URL} from 'node:url';
import path from 'node:path';
import process from 'node:process';
import console from 'node:console';
import {performance} from 'node:perf_hooks';
import {setImmediate} from 'node:timers';
const root=fileURLToPath(new URL('..',import.meta.url));
const output=path.join(root,'.local/audit');
const mode=process.argv[process.argv.indexOf('--mode')+1];
const inputIndex=process.argv.indexOf('--input');
const input=inputIndex>=0?path.resolve(process.argv[inputIndex+1]):path.join(root,'public/data/infrastructure/3135b6ec7b3d-infrastructure-1/overview-127-36-roads-509-145.geojson');
const runtime=path.join(output,'geometry-benchmark-runtime.mjs');
if(!['entity','primitives'].includes(mode)){
  const {build}=await import('esbuild');await mkdir(output,{recursive:true});
  await build({stdin:{contents:"export {buildPrimitives} from './src/primitive-renderer'; export {compileGeometry} from './shared/geometry'; export {FrameWorkBudget} from './shared/map-performance';",resolveDir:root,loader:'ts'},bundle:true,format:'esm',platform:'node',packages:'external',outfile:runtime,logLevel:'silent'});
  const results=[];
  for(const comparison of ['entity','primitives']){
    const child=spawnSync(process.execPath,['--expose-gc',fileURLToPath(import.meta.url),'--mode',comparison,'--input',input],{cwd:root,encoding:'utf8',maxBuffer:1024*1024});
    if(child.status!==0)throw new Error(child.stderr||child.stdout||`Benchmark exited ${child.status}`);
    results.push(JSON.parse(child.stdout));
  }
  const [entity,primitives]=results;
  if(entity.source_sha256!==primitives.source_sha256||entity.feature_count!==primitives.feature_count)throw new Error('Comparison source differs');
  const report={generated_at:new Date().toISOString(),scope:'Node.js Cesium object construction only; no WebGL/GPU or browser frame-rate claim',node:process.version,source:input,results,
    retained_heap_reduction_percent:100*(1-primitives.retained_heap_delta/entity.retained_heap_delta),
    construction_heap_reduction_percent:100*(1-primitives.construction_heap_delta/entity.construction_heap_delta),
  };
  await writeFile(path.join(output,'geometry-benchmark.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
}else{
  const C=await import('cesium');
  const {compileGeometry,buildPrimitives,FrameWorkBudget}=await import(pathToFileURL(runtime).href);
  const raw=await readFile(input),data=JSON.parse(raw.toString('utf8'));
  if(data.features.some(feature=>!['LineString','MultiLineString','Polygon','MultiPolygon'].includes(feature.geometry?.type)))throw new Error('Headless benchmark requires line/polygon data; points need a real scene');
  C.ContextLimits._minimumAliasedLineWidth=1;C.ContextLimits._maximumAliasedLineWidth=1;
  globalThis.gc();const baseline=process.memoryUsage();const start=performance.now();let resource,geometry,picks;
  const budget=new FrameWorkBudget(()=>performance.now(),()=>new Promise(resolve=>setImmediate(resolve)));
  if(mode==='entity')resource=await C.GeoJsonDataSource.load(data,{clampToGround:true});
  else{
    geometry=compileGeometry(data);picks=new Map();
    resource=await buildPrimitives(geometry,{id:'road-benchmark',layer:'infrastructure',format:'geojson',url:'/data/benchmark.geojson',bbox:[127.25,36.25,127.5,36.5],source_id:'osm',version:'2026-09-16',count:data.features.length,sha256:'benchmark'},
      {scene:{globe:{getHeight:()=>0}},isDestroyed:()=>false},{signal:new globalThis.AbortController().signal,budget,labelLimit:0,pickMap:picks});
  }
  const elapsed=performance.now()-start,constructed=process.memoryUsage();globalThis.gc();const retained=process.memoryUsage();
  const report={mode,source_sha256:createHash('sha256').update(raw).digest('hex'),feature_count:data.features.length,
    construction_ms:elapsed,construction_heap_delta:constructed.heapUsed-baseline.heapUsed,retained_heap_delta:retained.heapUsed-baseline.heapUsed,
    rss_delta:retained.rss-baseline.rss,entity_count:mode==='entity'?resource.entities.values.length:0,
    primitive_batches:mode==='primitives'?resource.root.length:0,source_properties:mode==='primitives'?picks.size:data.features.length,
    construction_chunks:mode==='primitives'?budget.snapshot():null,
  };
  if(mode==='primitives'){resource.destroy();geometry=null;picks=null;}else resource.entities.removeAll();
  resource=null;globalThis.gc();report.released_heap_delta=process.memoryUsage().heapUsed-baseline.heapUsed;
  console.log(JSON.stringify(report));
}
