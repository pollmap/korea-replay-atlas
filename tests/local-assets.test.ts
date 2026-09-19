import {afterEach,describe,expect,it} from 'vitest';
import {mkdtemp,mkdir,writeFile,rename,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createLocalAssets} from '../shared/local-assets';
import {handleRequest,type Env} from '../worker/index';

const fixtures:string[]=[];
async function setup(){
  const directory=await mkdtemp(path.join(os.tmpdir(),'korea-replay-assets-'));fixtures.push(directory);
  const root=path.join(directory,'한글 public');await mkdir(root);
  const assets=createLocalAssets(root);
  const env={ASSETS:assets} as unknown as Env;
  const call=(pathname:string,method='GET')=>handleRequest(new Request(`http://localhost${pathname}`,{method}),env);
  return {root,assets,call};
}
afterEach(async()=>{
  for(const directory of fixtures.splice(0)){
    const resolved=path.resolve(directory);
    if(path.dirname(resolved)!==path.resolve(os.tmpdir())||!path.basename(resolved).startsWith('korea-replay-assets-'))throw new Error('Unsafe fixture cleanup path');
    await rm(resolved,{recursive:true,force:true});
  }
});

describe('dynamic local data assets',()=>{
  it('serves a new terrain release created after adapter startup instead of a SPA fallback',async()=>{
    const f=await setup();const pathname='/data/terrain/merged-new/layer.json';
    expect((await f.call(pathname)).status).toBe(404);
    await mkdir(path.join(f.root,'data/terrain/merged-new'),{recursive:true});
    await writeFile(path.join(f.root,pathname),JSON.stringify({format:'quantized-mesh-1.0',available:[]}));
    const response=await f.call(pathname);
    expect(response.status).toBe(200);expect(response.headers.get('Content-Type')).toBe('application/json');
    expect(await response.json()).toEqual({format:'quantized-mesh-1.0',available:[]});
  });
  it('serves quantized terrain bytes with the right MIME type and an empty HEAD body',async()=>{
    const f=await setup();await mkdir(path.join(f.root,'data'));
    const data=Buffer.from([0,255,42,19,128]);await writeFile(path.join(f.root,'data/tile.terrain'),data);
    const get=await f.call('/data/tile.terrain');
    expect(get.headers.get('Content-Type')).toBe('application/vnd.quantized-mesh');
    expect(get.headers.get('Content-Length')).toBe('5');expect(Buffer.from(await get.arrayBuffer())).toEqual(data);
    const head=await f.call('/data/tile.terrain','HEAD');expect(head.status).toBe(200);expect(head.body).toBeNull();
    expect(head.headers.get('Content-Length')).toBe('5');
  });
  it('keeps file length and bytes from the same catalog version during atomic replacement',async()=>{
    const f=await setup();await mkdir(path.join(f.root,'data'));
    const target=path.join(f.root,'data/catalog.json'),replacement=path.join(f.root,'data/replacement.json');
    const before=JSON.stringify({release_id:'first',assets:[]});
    await writeFile(target,before);
    const selected=await f.call('/data/catalog.json');
    await writeFile(replacement,JSON.stringify({release_id:'second-and-longer',assets:[1,2,3]}));
    await rename(replacement,target);
    expect(await selected.text()).toBe(before);expect(selected.headers.get('Content-Length')).toBe(String(Buffer.byteLength(before)));
    const current=await f.call('/data/catalog.json');expect((await current.json() as {release_id:string}).release_id).toBe('second-and-longer');
  });
  it('returns data 404 without HTML and preserves private-path rejection',async()=>{
    const f=await setup();
    const response=await f.call('/data/missing.json');expect(response.status).toBe(404);expect(await response.text()).not.toContain('<html');
    expect((await f.call('/data/private/secret.json')).status).toBe(400);
    expect((await f.call('/data/raw/secret.json')).status).toBe(400);
    expect((await f.assets.fetch('http://localhost/%2e%2e%2fsecret')).status).toBe(400);
    expect((await f.assets.fetch('http://localhost/%E0%A4%A')).status).toBe(400);
  });
  it('does not treat an HTML file as a valid data response',async()=>{
    const f=await setup();await mkdir(path.join(f.root,'data'));await writeFile(path.join(f.root,'data/fallback.html'),'<!doctype html><html/>');
    const response=await f.call('/data/fallback.html');expect(response.status).toBe(404);
  });
});
