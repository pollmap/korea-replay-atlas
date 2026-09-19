import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {Readable} from 'node:stream';
import {pipeline} from 'node:stream/promises';
import path from 'node:path';
import process from 'node:process';
import {pathToFileURL} from 'node:url';
import {createLocalAssets} from '../shared/local-assets.ts';

const argument=name=>process.argv.find(value=>value.startsWith(`--${name}=`))?.slice(name.length+3);
const bundle=argument('bundle');
const root=path.resolve(bundle??'.');
const client=createLocalAssets(path.join(root,bundle?'client':'dist/client'),{cache:true});
const data=bundle?client:createLocalAssets(path.resolve('public'),{cache:true});
const module=await import(pathToFileURL(path.join(root,bundle?'worker/index.js':'dist/korea_replay/index.js')));
const override=argument('catalog');
const chosen=override?path.resolve(override):path.join(root,bundle?'client/data/catalog.json':'public/data/catalog.json');
const catalog=JSON.parse(await readFile(chosen,'utf8'));
const env={ENVIRONMENT:'local-production',DATA_STORAGE:bundle||override?'static':'local-production',STATIC_RELEASE_ID:catalog.release_id,COLLECTORS_ENABLED:'false',
  ASSETS:{async fetch(request){
    const url=new URL(request.url);
    if(/\/(?:\.|private|raw)(?:\/|$)/.test(url.pathname)||url.pathname.includes('/.'))return new Response(null,{status:404});
    if(url.pathname==='/data/catalog.json'&&override)return new Response(await readFile(chosen),{headers:{'Content-Type':'application/json','Cache-Control':'no-cache'}});
    if(url.pathname.startsWith('/data/'))return data.fetch(request);
    if(url.pathname==='/'){url.pathname='/index.html';return client.fetch(new Request(url,request));}
    return client.fetch(request);
  }}};
const port=Number(argument('port')??4173);
const measure=process.argv.includes('--measure');
const downloads={active:0,peak:0,started:0,finished:0,closed_early:0};
const recentDownloads=[];
const requestCounts=new Map(),gateCounts=new Map();
const server=createServer(async(req,res)=>{
  try{
    const url=new URL(req.url??'/',`http://127.0.0.1:${port}`);
    if(measure&&url.pathname==='/__qa/downloads'){
      res.setHeader('Content-Type','application/json');res.setHeader('Cache-Control','no-store');
      res.end(JSON.stringify({scope:'Local server data response lifetime; browser cache hits excluded',...downloads,
        unique:requestCounts.size,gates:Object.fromEntries(gateCounts),
        mostRequested:[...requestCounts].sort((a,b)=>b[1]-a[1]).slice(0,20),recent:recentDownloads}));return;
    }
    if(measure&&url.pathname.startsWith('/data/')){
      downloads.active++;downloads.started++;downloads.peak=Math.max(downloads.peak,downloads.active);let settled=false;
      const gate=req.headers['x-korea-download-gate']??'none',started=performance.now();
      requestCounts.set(url.pathname,(requestCounts.get(url.pathname)??0)+1);gateCounts.set(gate,(gateCounts.get(gate)??0)+1);
      const record={id:downloads.started,path:url.pathname,active:downloads.active,gate,referer:req.headers.referer??null,start:started};
      recentDownloads.push(record);
      if(recentDownloads.length>128)recentDownloads.shift();
      const settle=early=>{if(settled)return;settled=true;downloads.active--;record.elapsed=performance.now()-started;record.closedEarly=early;record.status=res.statusCode;if(early)downloads.closed_early++;else downloads.finished++;};
      res.once('finish',()=>settle(false));res.once('close',()=>settle(!res.writableFinished));
    }
    const headers=new Headers();for(const [key,value] of Object.entries(req.headers))if(value!==undefined)headers.set(key,Array.isArray(value)?value.join(','):value);
    const response=await module.default.fetch(new Request(url,{method:req.method,headers}),env);
    res.statusCode=response.status;response.headers.forEach((value,key)=>res.setHeader(key,value));
    if(response.body&&req.method!=='HEAD')await pipeline(Readable.fromWeb(response.body),res);else res.end();
  }catch(error){
    if(error.code==='ERR_STREAM_PREMATURE_CLOSE')return;
    if(!res.headersSent)res.writeHead(503,{'Cache-Control':'no-store'});res.end();
    process.stderr.write(`Production preview request failed: ${error.message}\n`);
  }
});
server.listen(port,'127.0.0.1',()=>process.stdout.write(`KOREA REPLAY production build: http://127.0.0.1:${port}/ (${catalog.release_id})\n`));
