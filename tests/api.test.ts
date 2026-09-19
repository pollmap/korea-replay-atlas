import {describe,it,expect} from 'vitest';
import {handleRequest,parseBBox,type Env} from '../worker/index';
import {EMPTY_CATALOG} from '../shared/sources';
const c=structuredClone(EMPTY_CATALOG);
c.release_id='test-fixture';
c.assets=[{id:'fixture',layer:'rail',format:'replay',url:'/data/test.json',bbox:[127,36,128,37],source_id:'test',version:'test',count:2,sha256:'test',from:'2026-09-16T00:00:00Z',to:'2026-09-16T01:00:00Z'}];
const env={ASSETS:{fetch:async()=>Response.json(c)}} as unknown as Env;
const call=(path:string)=>handleRequest(new Request(`http://localhost${path}`),env);
describe('public data contract',()=>{
  it('rejects reversed and non-finite spatial bounds',()=>{expect(()=>parseBBox('128,36,127,37')).toThrow();expect(()=>parseBBox('NaN,36,127,37')).toThrow();});
  it('returns no invented files outside published coverage',async()=>{
    const r=await call('/api/v1/replay?bbox=126,33,127,34');expect(r.status).toBe(200);expect((await r.json() as {assets:unknown[]}).assets).toEqual([]);
  });
  it('rejects unbounded multi-day replay requests',async()=>expect((await call('/api/v1/replay?from=2026-09-01T00:00:00Z&to=2026-09-16T00:00:00Z')).status).toBe(400));
  it('excludes another day even if its location matches',async()=>{
    const r=await call('/api/v1/replay?from=2026-09-15T00:00:00Z&to=2026-09-15T01:00:00Z');expect((await r.json() as {assets:unknown[]}).assets).toHaveLength(0);
  });
  it('compares timezone offsets as instants rather than strings',async()=>{
    const r=await call('/api/v1/replay?from=2026-09-16T09%3A00%3A00%2B09%3A00&to=2026-09-16T10%3A00%3A00%2B09%3A00');
    expect((await r.json() as {assets:unknown[]}).assets).toHaveLength(1);
  });
  it('rejects timezone-less requests and malformed URL encoding',async()=>{
    expect((await call('/api/v1/replay?from=2026-09-16T00:00:00&to=2026-09-16T01:00:00')).status).toBe(400);
    expect((await call('/data/%E0%A4%A')).status).toBe(400);
  });
  it('returns unknown endpoints and sources as 404',async()=>{expect((await call('/api/v1/unknown')).status).toBe(404);expect((await call('/api/v1/sources/unknown')).status).toBe(404);});
  it('never exposes raw storage',async()=>expect((await call('/data/raw/secret.json')).status).toBe(400));
  it('does not treat a SPA fallback as data',async()=>{
    const noData={ASSETS:{fetch:async()=>new Response('<html/>',{headers:{'Content-Type':'text/html'}})}} as unknown as Env;
    const r=await handleRequest(new Request('http://localhost/api/v1/catalog'),noData);expect((await r.json() as {release_id:string}).release_id).toBe('unpublished');
  });
});
