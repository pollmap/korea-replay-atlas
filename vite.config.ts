import { defineConfig, type ViteDevServer } from 'vite';
import react from '@vitejs/plugin-react';
import { cloudflare } from '@cloudflare/vite-plugin';
import { viteStaticCopy } from 'vite-plugin-static-copy';
import path from 'node:path';
import {Readable} from 'node:stream';
import type {ReadableStream as NodeReadableStream} from 'node:stream/web';
import {pipeline} from 'node:stream/promises';
import {handleRequest} from './worker/index';
import {createLocalAssets} from './shared/local-assets';
import {downloadGatePlugin} from './scripts/download-gate-vite';

export default defineConfig(({command})=>({
  plugins: [react(), downloadGatePlugin(), ...(command==='build'?[cloudflare()]:[{
    name:'korea-replay-local-api',
    configureServer(server:ViteDevServer){
      const assets=createLocalAssets(path.resolve(server.config.root,'public'),{cache:true});
      server.middlewares.use(async(req,res,next)=>{
        const url=new URL(req.url??'/','http://127.0.0.1:5173');
        if(!url.pathname.startsWith('/api/')&&!url.pathname.startsWith('/data/'))return next();
        try{
          const headers=new Headers();
          for(const [key,value] of Object.entries(req.headers)){
            if(value!==undefined)headers.set(key,Array.isArray(value)?value.join(','):value);
          }
          const response=await handleRequest(new Request(url,{method:req.method,headers}),{
            ENVIRONMENT:'local-node',ASSETS:assets as Fetcher,
          });
          res.statusCode=response.status;response.headers.forEach((value,key)=>res.setHeader(key,value));
          if(response.body&&req.method!=='HEAD')await pipeline(Readable.fromWeb(response.body as unknown as NodeReadableStream),res);
          else res.end();
        }catch(error){
          if((error as NodeJS.ErrnoException).code==='ERR_STREAM_PREMATURE_CLOSE')return;
          if(!res.headersSent){res.statusCode=503;res.setHeader('Cache-Control','no-store');}
          res.end();
        }
      });
    },
  }]), viteStaticCopy({targets: [
    {src: 'node_modules/cesium/Build/Cesium/Workers', dest: 'cesium', rename:{stripBase:4}},
    {src: 'node_modules/cesium/Build/Cesium/Assets', dest: 'cesium', rename:{stripBase:4}},
    {src: 'node_modules/cesium/Build/Cesium/ThirdParty', dest: 'cesium', rename:{stripBase:4}},
    {src: 'node_modules/cesium/Build/Cesium/Widgets', dest: 'cesium', rename:{stripBase:4}},
  ]})],
  define: { CESIUM_BASE_URL: JSON.stringify('/cesium/') },
  server:{watch:{ignored:['**/.local/**','**/public/data/**','**/.venv/**']}},
  build: {chunkSizeWarningLimit: 1800,copyPublicDir:false},
}));
