import {createHash} from 'node:crypto';
import {gzipSync} from 'node:zlib';
import {afterEach,expect,it,vi} from 'vitest';
import {fetchPinnedJson} from '../src/atlas-client';
import {decodeGzip} from '../shared/asset-transport';
afterEach(()=>vi.unstubAllGlobals());
const hash=(body:string|Buffer)=>createHash('sha256').update(body).digest('hex');
function fixture(name:string){const raw=JSON.stringify({name,price:365000000}),encoded=gzipSync(raw);return {raw,encoded,ref:{url:`/data/${name}.json`,sha256:hash(raw),bytes:Buffer.byteLength(raw),transport:{encoding:'gzip' as const,sha256:hash(encoded),bytes:encoded.length}}};}
it('reads explicit compressed JSON, shares it, and retains plain JSON compatibility',async()=>{
  const {raw,encoded,ref}=fixture('compressed');const fetcher=vi.fn(async()=>new Response(new Uint8Array(encoded)));vi.stubGlobal('fetch',fetcher);
  expect(await fetchPinnedJson(ref,'https://example.com',new AbortController().signal)).toEqual(JSON.parse(raw));
  expect(await fetchPinnedJson(ref,'https://example.com',new AbortController().signal)).toEqual(JSON.parse(raw));expect(fetcher).toHaveBeenCalledTimes(1);
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(raw)));expect(await fetchPinnedJson({url:'/data/plain-compatible.json',sha256:hash(raw),bytes:raw.length},'https://example.com',new AbortController().signal)).toEqual(JSON.parse(raw));
});
it('rejects transfer corruption, decoded corruption, and malformed contracts before cache reuse',async()=>{
  const {encoded,ref}=fixture('corrupt');vi.stubGlobal('fetch',vi.fn(async()=>new Response(new Uint8Array(encoded))));
  await expect(fetchPinnedJson({...ref,transport:{...ref.transport,sha256:'0'.repeat(64)}},'https://example.com',new AbortController().signal)).rejects.toThrow('압축 자료의 내용');
  await expect(fetchPinnedJson({...ref,sha256:'1'.repeat(64)},'https://example.com',new AbortController().signal)).rejects.toThrow('자료의 내용');
  await expect(fetchPinnedJson({...ref,transport:{...ref.transport,bytes:0}},'https://example.com',new AbortController().signal)).rejects.toThrow('압축 참조');
});
it('bounds decoded output and rejects truncation and cancellation',async()=>{
  const raw=Buffer.alloc(100000,65),encoded=gzipSync(raw);const bytes=Uint8Array.from(encoded).buffer;
  await expect(decodeGzip(bytes,99999,new AbortController().signal)).rejects.toThrow('크기 제한');
  await expect(decodeGzip(bytes.slice(0,-8),100000,new AbortController().signal)).rejects.toThrow();
  const cancelled=new AbortController();cancelled.abort();await expect(decodeGzip(bytes,100000,cancelled.signal)).rejects.toMatchObject({name:'AbortError'});
});
