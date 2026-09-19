import {describe,it,expect,vi} from 'vitest';
import {handleRequest,type Env} from '../worker/index';

// R2 contract double: metadata-only conditional responses and byte reads are
// modeled here. This does not execute the Cloudflare binding or edge runtime.
function fixture(options:{metadataOnly?:boolean;data?:string;missing?:boolean}={}){
  const state={etag:'version-a',data:options.data??'abcdefghij',uploaded:new Date('2026-09-16T00:00:00.800Z')};
  const metadata=()=>({etag:state.etag,httpEtag:`"${state.etag}"`,size:new TextEncoder().encode(state.data).length,
    uploaded:state.uploaded,writeHttpMetadata:(h:Headers)=>h.set('Content-Type','application/octet-stream')});
  const head=vi.fn(async()=>options.missing?null:metadata());
  const get=vi.fn(async(_key:string,args?:R2GetOptions)=>{
    if(options.missing)return null;
    if(options.metadataOnly)return metadata();
    if(args?.onlyIf&&!(args.onlyIf instanceof Headers)&&args.onlyIf.etagMatches!==state.etag)return metadata();
    const range=args?.range;
    if(range instanceof Headers)throw new Error('The adapter should normalize byte ranges');
    if(range&&'suffix' in range)throw new Error('The adapter should resolve suffix ranges');
    const offset=range?.offset??0,length=range?.length??state.data.length;
    return {...metadata(),...(range?{range:{offset,length}}:{}),body:new Response(state.data.slice(offset,offset+length)).body!};
  });
  const env={DATA:{head,get},ASSETS:{fetch:async()=>new Response('unused')}} as unknown as Env;
  const call=(headers:HeadersInit={},method='GET',path='asset.glb')=>handleRequest(new Request(`https://example.test/data/${path}`,{method,headers}),env);
  return {state,metadata,head,get,call};
}

describe('R2 HTTP response semantics',()=>{
  it('uses one R2 get for a normal full response with length and validators',async()=>{
    const f=fixture();const r=await f.call();
    expect(r.status).toBe(200);expect(await r.text()).toBe('abcdefghij');
    expect(r.headers.get('Content-Length')).toBe('10');expect(r.headers.get('ETag')).toBe('"version-a"');
    expect(r.headers.get('Last-Modified')).toBe('Wed, 16 Sep 2026 00:00:00 GMT');
    expect(r.headers.get('Accept-Ranges')).toBe('bytes');expect(r.headers.has('Content-Range')).toBe(false);
    expect(f.get).toHaveBeenCalledTimes(1);expect(f.head).not.toHaveBeenCalled();
  });
  it('does not mislabel failed If-Match metadata as a 304 cache hit',async()=>{
    const f=fixture({metadataOnly:true});const r=await f.call({'If-Match':'"stale"'});
    expect(r.status).toBe(412);expect(await r.text()).toBe('');expect(r.headers.get('Cache-Control')).toBe('no-store');
  });
  it.each(['"version-a"','W/"version-a"','"other", W/"version-a"','*'])('returns 304 for matching If-None-Match %s',async(value)=>{
    const f=fixture({metadataOnly:true});const r=await f.call({'If-None-Match':value});
    expect(r.status).toBe(304);expect(r.body).toBeNull();expect(r.headers.get('ETag')).toBe('"version-a"');
  });
  it('requires strong If-Match comparison and gives it precedence over cache conditions',async()=>{
    const f=fixture();
    expect((await f.call({'If-Match':'W/"version-a"'})).status).toBe(412);
    expect((await f.call({'If-Match':'"wrong"','If-None-Match':'"version-a"'})).status).toBe(412);
    expect((await f.call({'If-Match':'"old", "version-a"'})).status).toBe(200);
  });
  it('ignores date preconditions superseded by ETags before passing them to R2',async()=>{
    const f=fixture();const r=await f.call({'If-Match':'"version-a"','If-Unmodified-Since':'Tue, 15 Sep 2026 00:00:00 GMT',
      'If-None-Match':'"other"','If-Modified-Since':'Thu, 17 Sep 2026 00:00:00 GMT'});
    expect(r.status).toBe(200);
    const headers=f.get.mock.calls[0][1]!.onlyIf as Headers;
    expect(headers.has('If-Unmodified-Since')).toBe(false);expect(headers.has('If-Modified-Since')).toBe(false);
  });
  it('handles date conditions with HTTP-second precision and ignores invalid dates',async()=>{
    const f=fixture();
    expect((await f.call({'If-Unmodified-Since':'Tue, 15 Sep 2026 00:00:00 GMT'})).status).toBe(412);
    expect((await f.call({'If-Modified-Since':'Wed, 16 Sep 2026 00:00:00 GMT'})).status).toBe(304);
    expect((await f.call({'If-Unmodified-Since':'Wed, 16 Sep 2026 00:00:00 GMT'})).status).toBe(200);
    expect((await f.call({'If-Modified-Since':'0','If-Unmodified-Since':'invalid'})).status).toBe(200);
  });
  it('HEAD uses metadata only, keeps the full length, and ignores Range',async()=>{
    const f=fixture();const r=await f.call({'Range':'bytes=2-4'},'HEAD');
    expect(r.status).toBe(200);expect(r.body).toBeNull();expect(r.headers.get('Content-Length')).toBe('10');
    expect(r.headers.has('Content-Range')).toBe(false);expect(f.head).toHaveBeenCalledTimes(1);expect(f.get).not.toHaveBeenCalled();
    expect((await f.call({'If-Match':'"wrong"'},'HEAD')).status).toBe(412);
    expect((await f.call({'If-None-Match':'"version-a"'},'HEAD')).status).toBe(304);
  });
  it.each([
    ['bytes=2-4','cde','bytes 2-4/10','3'],
    ['bytes=7-','hij','bytes 7-9/10','3'],
    ['bytes=-4','ghij','bytes 6-9/10','4'],
    ['bytes=7-99999999999999999999','hij','bytes 7-9/10','3'],
    ['bytes=-99999999999999999999','abcdefghij','bytes 0-9/10','10'],
  ])('returns a verified 206 response for %s',async(range,body,contentRange,length)=>{
    const f=fixture();const r=await f.call({Range:range});
    expect(r.status).toBe(206);expect(await r.text()).toBe(body);
    expect(r.headers.get('Content-Range')).toBe(contentRange);expect(r.headers.get('Content-Length')).toBe(length);
    expect(f.head).toHaveBeenCalledTimes(1);expect(f.get).toHaveBeenCalledTimes(1);
    expect(f.get.mock.calls[0][1]?.onlyIf).toEqual({etagMatches:'version-a'});
  });
  it.each(['bytes=10-','bytes=99999999999999999999-','bytes=-0'])('returns 416 and full size for unsatisfiable %s',async(range)=>{
    const f=fixture();const r=await f.call({Range:range});
    expect(r.status).toBe(416);expect(r.headers.get('Content-Range')).toBe('bytes */10');
    expect(r.body).toBeNull();expect(f.get).not.toHaveBeenCalled();
  });
  it('does not replace a precondition failure with range failure',async()=>{
    const f=fixture();expect((await f.call({Range:'bytes=99-','If-None-Match':'"version-a"'})).status).toBe(304);
    expect((await f.call({Range:'bytes=99-','If-Match':'"wrong"'})).status).toBe(412);
  });
  it.each(['items=0-1','bytes=1-2,4-5','bytes=5-2','bytes=-','nonsense'])('ignores unsupported or malformed range %s',async(range)=>{
    const f=fixture();const r=await f.call({Range:range});
    expect(r.status).toBe(200);expect(await r.text()).toBe('abcdefghij');expect(r.headers.has('Content-Range')).toBe(false);
  });
  it('supports strong If-Range ETags and safely sends full data for weak/date/changed validators',async()=>{
    const f=fixture();expect((await f.call({Range:'bytes=2-4','If-Range':'"version-a"'})).status).toBe(206);
    for(const validator of ['"stale"','W/"version-a"','Wed, 16 Sep 2026 00:00:00 GMT']){
      const r=await f.call({Range:'bytes=2-4','If-Range':validator});expect(r.status).toBe(200);expect(await r.text()).toBe('abcdefghij');
    }
  });
  it('rechecks changed metadata between HEAD and the ETag-pinned read',async()=>{
    const f=fixture();const original=f.get.getMockImplementation()!;
    f.get.mockImplementationOnce(async(key,args)=>{f.state.etag='version-b';f.state.data='XYZ';return original(key,args);});
    const r=await f.call({Range:'bytes=1-9'});
    expect(r.status).toBe(206);expect(await r.text()).toBe('YZ');expect(r.headers.get('Content-Range')).toBe('bytes 1-2/3');
    expect(r.headers.get('ETag')).toBe('"version-b"');expect(f.get).toHaveBeenCalledTimes(2);
  });
  it('rechecks If-Match after a concurrent range-read change',async()=>{
    const f=fixture();const original=f.get.getMockImplementation()!;
    f.get.mockImplementationOnce(async(key,args)=>{f.state.etag='version-b';return original(key,args);});
    expect((await f.call({Range:'bytes=1-2','If-Match':'"version-a"'})).status).toBe(412);
  });
  it('returns the complete new representation when If-Range becomes stale during the read',async()=>{
    const f=fixture();const original=f.get.getMockImplementation()!;
    f.get.mockImplementationOnce(async(key,args)=>{f.state.etag='version-b';f.state.data='XYZ';return original(key,args);});
    const r=await f.call({Range:'bytes=1-2','If-Range':'"version-a"'});
    expect(r.status).toBe(200);expect(await r.text()).toBe('XYZ');expect(r.headers.has('Content-Range')).toBe(false);
  });
  it('rejects a storage response for the wrong byte range and bounds unstable retries',async()=>{
    const f=fixture();const original=f.get.getMockImplementation()!;
    f.get.mockImplementationOnce(async(key,args)=>{
      const result=await original(key,args);
      return {...result!,range:{offset:0,length:1}};
    });
    expect((await f.call({Range:'bytes=2-4'})).status).toBe(503);
    const unstable=fixture({metadataOnly:true});
    expect((await unstable.call({Range:'bytes=2-4'})).status).toBe(503);
    expect(unstable.get).toHaveBeenCalledTimes(2);
  });
  it('returns 503 for storage failure or unexplained body absence, never fabricated 304',async()=>{
    const f=fixture();f.get.mockRejectedValueOnce(new Error('sensitive R2 details'));
    const r=await f.call();expect(r.status).toBe(503);expect(await r.text()).toBe('');expect(r.headers.get('Retry-After')).toBe('1');
    expect((await fixture({metadataOnly:true}).call()).status).toBe(503);
  });
  it('returns empty HEAD 404 and zero-byte range 416 with the correct size',async()=>{
    const missing=await fixture({missing:true}).call({},'HEAD');expect(missing.status).toBe(404);expect(missing.body).toBeNull();
    const empty=fixture({data:''});const r=await empty.call({Range:'bytes=0-0'});
    expect(r.status).toBe(416);expect(r.headers.get('Content-Range')).toBe('bytes */0');
    expect((await empty.call()).status).toBe(200);
  });
  it('preserves short-lived catalog and immutable asset cache policies',async()=>{
    const f=fixture();expect((await f.call({},'GET','catalog.json')).headers.get('Cache-Control')).toBe('public, max-age=30');
    expect((await f.call()).headers.get('Cache-Control')).toContain('immutable');
  });
});
