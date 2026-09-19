import {describe,expect,it,vi} from 'vitest';
import {createPagesAdapter,type PagesPolicy} from '../worker/pages-adapter';
import {validateRuntimeV2} from '../shared/runtime-v2';
const policy:PagesPolicy={release_id:'pub-0123456789abcdef',artifact_sha256:'a'.repeat(64),snapshot_origin:'https://1234abcd.korea-replay.pages.dev',
  data:{origin:'https://abcd1234.korea-replay-data.pages.dev',manifest_path:'/data/atlas/atlas-test/manifest.json',manifest_sha256:'b'.repeat(64)},
  bus_catalog:{url:'/data/live-transit/fixed/manifest.json',sha256:'c'.repeat(64),byte_length:123}};
const request=(path:string,origin='https://1234abcd.korea-replay.pages.dev')=>new Request(origin+path);
describe('Pages static and live isolation',()=>{
  it('derives real preview identity, uses verified snapshot on production and rejects a mutable branch alias',async()=>{
    const adapter=createPagesAdapter({fetch:()=>new Response()},policy),env={ASSETS:{fetch:vi.fn()}};
    const first=validateRuntimeV2(await(await adapter.fetch(request('/api/v2/runtime','https://aabbccdd.korea-replay.pages.dev'),env)).json());
    const production=validateRuntimeV2(await(await adapter.fetch(request('/api/v2/runtime','https://korea-replay.pages.dev'),env)).json());
    expect(first.snapshot?.hash).toBe('aabbccdd');expect(production.snapshot?.hash).toBe('1234abcd');expect(first.artifact_sha256).toBe(production.artifact_sha256);
    expect((await adapter.fetch(request('/api/v2/runtime','https://branch.korea-replay.pages.dev'),env)).status).toBe(409);
  });
  it('reads pinned static APIs locally and does not forward credentials or unrelated environment values',async()=>{
    const application={fetch:vi.fn<(request:Request,env:Record<string,unknown>)=>Response>(()=>new Response('static'))},binding={fetch:vi.fn()},assets={fetch:vi.fn()};
    const adapter=createPagesAdapter(application,policy);await adapter.fetch(request('/api/v1/search?q=test&release='+policy.release_id),{ASSETS:assets,KOREA_API:binding});
    expect(binding.fetch).not.toHaveBeenCalled();expect(application.fetch.mock.calls[0][1]).toEqual({ASSETS:assets,ENVIRONMENT:'production',DATA_STORAGE:'static',COLLECTORS_ENABLED:'false',STATIC_RELEASE_ID:policy.release_id,LIVE_TRANSIT_MODE:'broker'});
  });
  it('forwards current observations internally with no external fallback or redirect',async()=>{
    const application={fetch:vi.fn()},binding={fetch:vi.fn(async(request:Request)=>{
      expect(new URL(request.url).hostname).toBe('korea-replay.internal');expect(request.headers.has('Authorization')).toBe(false);expect(request.headers.has('Cookie')).toBe(false);
      return new Response('live',{headers:{'Set-Cookie':'private=fixture','Content-Type':'application/json'}});
    })};
    const adapter=createPagesAdapter(application,policy),env={ASSETS:{fetch:vi.fn()},KOREA_API:binding};
    const response=await adapter.fetch(new Request(request('/api/v1/live/transit/bus?route=fixture'),{headers:{Authorization:'Bearer fixture',Cookie:'fixture=true'}}),env);
    expect(await response.text()).toBe('live');expect(response.headers.has('Set-Cookie')).toBe(false);expect(application.fetch).not.toHaveBeenCalled();
    expect((await adapter.fetch(request('/api/v1/live/transit/bus'),{ASSETS:env.ASSETS})).status).toBe(503);
  });
  it('replaces a newer live catalog reference with the immutable local reference',async()=>{
    const adapter=createPagesAdapter({fetch:vi.fn()},policy),env={ASSETS:{fetch:vi.fn()},KOREA_API:{fetch:vi.fn(async()=>Response.json({bus_catalog:{url:'/data/new-unpublished/manifest.json',configured:true},bus_routes:[]}))}};
    const body=await(await adapter.fetch(request('/api/v1/live/transit/targets'),env)).json() as {bus_catalog:unknown};expect(body.bus_catalog).toEqual({...policy.bus_catalog,configured:true});
  });
  it('preserves real missing-asset 404 and never calls live code for files',async()=>{
    const binding={fetch:vi.fn()},application={fetch:vi.fn()},assets={fetch:vi.fn(async()=>new Response('missing',{status:404}))};
    const adapter=createPagesAdapter(application,policy);expect((await adapter.fetch(request('/data/missing.glb'),{ASSETS:assets,KOREA_API:binding})).status).toBe(404);
    expect(binding.fetch).not.toHaveBeenCalled();expect(application.fetch).not.toHaveBeenCalled();
  });
});
