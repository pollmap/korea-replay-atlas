import {open,realpath,type FileHandle} from 'node:fs/promises';
import path from 'node:path';
import {Readable} from 'node:stream';

const TYPES:Record<string,string>={
  '.json':'application/json','.geojson':'application/geo+json','.terrain':'application/vnd.quantized-mesh',
  '.glb':'model/gltf-binary','.gltf':'model/gltf+json','.png':'image/png','.jpg':'image/jpeg',
  '.jpeg':'image/jpeg','.webp':'image/webp','.svg':'image/svg+xml','.html':'text/html',
  '.js':'text/javascript','.css':'text/css','.wasm':'application/wasm','.woff2':'font/woff2',
};

/** Dev-only ASSETS adapter. Resolve files at request time because pipelines add
 * immutable releases after Vite's startup public-file index has been built. */
export function createLocalAssets(publicDirectory:string,options:{cache?:boolean}={}){
  const root=path.resolve(publicDirectory),canonicalRoot=realpath(root);
  return {async fetch(input:RequestInfo|URL):Promise<Response>{
    const request=input instanceof Request?input:new Request(input.toString());
    if(!['GET','HEAD'].includes(request.method))return new Response(null,{status:405});
    let relative:string;
    try{relative=decodeURIComponent(new URL(request.url).pathname).replace(/^\//,'');}
    catch{return new Response(null,{status:400});}
    const file=path.resolve(root,relative);
    if(!file.startsWith(root+path.sep))return new Response(null,{status:400});
    let handle:FileHandle|undefined;
    try{
      const actual=await realpath(file),base=await canonicalRoot;
      if(!actual.startsWith(base+path.sep))return new Response(null,{status:400});
      // Stat and stream the same opened version, even while catalog.json is
      // atomically replaced by a running pipeline.
      handle=await open(actual,'r');const info=await handle.stat();
      if(!info.isFile()){await handle.close();return new Response(null,{status:404});}
      const etag=`"${info.size.toString(16)}-${info.mtimeMs.toString(16)}"`;
      const headers={'Content-Type':TYPES[path.extname(actual).toLowerCase()]??'application/octet-stream',
        'Content-Length':String(info.size),'Cache-Control':options.cache?(relative==='data/catalog.json'?'no-cache':'public, max-age=0, must-revalidate'):'no-store','X-Content-Type-Options':'nosniff',...(options.cache?{ETag:etag}:{})};
      if(options.cache&&request.headers.get('if-none-match')===etag){await handle.close();return new Response(null,{status:304,headers});}
      if(request.method==='HEAD'){await handle.close();return new Response(null,{headers});}
      return new Response(Readable.toWeb(handle.createReadStream({autoClose:true})) as ReadableStream,{headers});
    }catch(error){
      if(handle)await handle.close().catch(()=>{});
      const code=(error as NodeJS.ErrnoException).code;
      return new Response(null,{status:code==='EACCES'||code==='EPERM'?403:404});
    }
  }};
}
