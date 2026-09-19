import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import type {Plugin} from 'vite';

/** Keep the small gate in the real client bundle when copyPublicDir is disabled. */
export function downloadGatePlugin():Plugin {
  const source=readFileSync(fileURLToPath(new URL('../src/download-gate.worker.js',import.meta.url)),'utf8');
  const version=createHash('sha256').update(source).digest('hex').slice(0,16);
  const script=source.replaceAll('__DOWNLOAD_GATE_VERSION__',version);
  return {
    name:'korea-download-gate',
    config(){return {define:{__DOWNLOAD_GATE_VERSION__:JSON.stringify(version)}};},
    configureServer(server){
      server.middlewares.use((request,response,next)=>{
        if(new URL(request.url??'/','http://localhost').pathname!=='/download-gate.js')return next();
        response.setHeader('Content-Type','application/javascript; charset=utf-8');
        response.setHeader('Cache-Control','no-cache');
        response.end(request.method==='HEAD'?undefined:script);
      });
    },
    generateBundle(){
      if(this.environment.name==='client')this.emitFile({type:'asset',fileName:'download-gate.js',source:script});
    },
  };
}
