import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import type {AddressInfo} from 'node:net';
import {describe,it,expect,vi} from 'vitest';
import {createTransitCatalogReader,parseTransitCatalog,parseTransitCity,validTransitRef,type TransitCatalogRef,type TransitCity} from '../shared/transit-catalog';
import {createLiveTransitHandler} from '../worker/live-transit';

const prefix='/data/live-transit/routes/1234567890abcdef/';
const route={id:'tago-12-sjb293000077',city_code:'12',route_id:'SJB293000077',label:'세종 B2 · 오송 방면',route_no:'B2',route_type:'급행',start_station_name:'월드컵경기장',end_station_name:'오송역'};
const retrieved_at='2026-09-16T17:00:00Z';
function fixture(){
  const bodies=new Map<string,string>();
  const ref=(file:string,value:unknown):TransitCatalogRef=>{const body=JSON.stringify(value),url=prefix+file;bodies.set(url,body);return {url,byte_length:Buffer.byteLength(body),sha256:createHash('sha256').update(body).digest('hex')};};
  const shard={schema_version:1,city_code:'12',city_name:'세종특별자치시',retrieved_at,routes:[route]};
  const city:TransitCity={city_code:'12',city_name:shard.city_name,retrieved_at,status:'complete',location_service_supported:true,route_count:1,...ref('12.json',shard)};
  const catalog={schema_version:1,cities:[city,{city_code:'25',city_name:'대전',retrieved_at:null,status:'failed',location_service_supported:true,route_count:0,error_code:'upstream_timeout'}]};
  const reference=ref('manifest.json',catalog);
  const fetcher=vi.fn<typeof fetch>(async(input)=>new Response(bodies.get(String(input)),{headers:{'Content-Type':'application/json'}}));
  return {bodies,ref,shard,city,catalog,reference,fetcher};
}
describe('national route catalog integrity and selection',()=>{
  it('downloads only the manifest and selected city, reuses validated data, and preserves route direction',async()=>{
    const f=fixture(),reader=createTransitCatalogReader(f.fetcher);
    expect((await reader.manifest(f.reference)).cities).toHaveLength(2);
    expect(f.fetcher).toHaveBeenCalledTimes(1);
    expect(await reader.route(f.reference,route.id)).toEqual(route);
    expect(await reader.route(f.reference,route.id)).toEqual(route);
    expect(f.fetcher).toHaveBeenCalledTimes(2);
    expect(await reader.city(f.reference,'25')).toBeNull();
    expect(await reader.route(f.reference,'tago-12-unknown')).toBeNull();
    expect(await reader.route(f.reference,'https://attacker.test')).toBeNull();
    expect(f.fetcher).toHaveBeenCalledTimes(2);
  });
  it('deduplicates simultaneous city loads without a national fan-out',async()=>{
    const f=fixture(),reader=createTransitCatalogReader(f.fetcher);
    await Promise.all(Array.from({length:8},()=>reader.route(f.reference,route.id)));
    expect(f.fetcher).toHaveBeenCalledTimes(2);
  });
  it.each([301,302,303,307,308])('rejects HTTP %i without following it and permits a valid retry',async(status)=>{
    const f=fixture();let redirect=true,targetRequests=0;
    const server=createServer((request,response)=>{
      if(request.url==='/redirect-target')targetRequests++;
      if(redirect&&request.url===f.reference.url){response.writeHead(status,{Location:'/redirect-target'});response.end();return;}
      response.writeHead(200,{'Content-Type':'application/json'});response.end(f.bodies.get(f.reference.url));
    });
    await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));
    try{
      const origin=`http://127.0.0.1:${(server.address() as AddressInfo).port}`;
      const fetcher=vi.fn<typeof fetch>((input,init)=>fetch(new URL(String(input),origin),init));
      const reader=createTransitCatalogReader(fetcher);
      await expect(reader.manifest(f.reference)).rejects.toThrow('불러오지');
      expect(targetRequests).toBe(0);expect(fetcher).toHaveBeenCalledTimes(1);
      redirect=false;
      expect((await reader.manifest(f.reference)).cities).toHaveLength(2);
      expect(targetRequests).toBe(0);expect(fetcher).toHaveBeenCalledTimes(2);
    }finally{await new Promise<void>((resolve,reject)=>server.close(error=>error?reject(error):resolve()));}
  });
  it.each(['https://attacker.test/manifest.json',prefix+'../manifest.json',prefix+'12.json','/data/raw/manifest.json',prefix+'manifest.json?key=x'])('refuses untrusted manifest URL %s',url=>{
    expect(validTransitRef({...fixture().reference,url})).toBe(false);
  });
  it('rejects same-size altered bytes and allows a later valid retry',async()=>{
    const f=fixture(),reader=createTransitCatalogReader(f.fetcher),body=f.bodies.get(f.reference.url)!;
    f.bodies.set(f.reference.url,body.replace('complete','corrupt!'));
    await expect(reader.manifest(f.reference)).rejects.toThrow('해시');
    f.bodies.set(f.reference.url,body);
    expect((await reader.manifest(f.reference)).cities).toHaveLength(2);
    expect(f.fetcher).toHaveBeenCalledTimes(2);
  });
  it('bounds bytes even if Content-Length is absent',async()=>{
    const f=fixture();f.bodies.set(f.reference.url,f.bodies.get(f.reference.url)!+'extra');
    await expect(createTransitCatalogReader(f.fetcher).manifest(f.reference)).rejects.toThrow('크기');
  });
  it('does not authorize a route solely from a matching id pattern or unsupported city',async()=>{
    const f=fixture();f.city.location_service_supported=false;
    const reference=f.ref('manifest.json',f.catalog),reader=createTransitCatalogReader(f.fetcher);
    expect(await reader.route(reference,route.id)).toBeNull();expect(f.fetcher).toHaveBeenCalledTimes(1);
  });
  it('rejects duplicate cities, cross-release shards, and a shard attached to a failed city',()=>{
    const f=fixture();
    expect(()=>parseTransitCatalog({...f.catalog,cities:[f.city,f.city]},f.reference)).toThrow();
    expect(()=>parseTransitCatalog({...f.catalog,cities:[{...f.city,url:prefix.replace('1234','4321')+'12.json'}]},f.reference)).toThrow();
    expect(()=>parseTransitCatalog({...f.catalog,cities:[{...f.city,status:'failed',route_count:0}]},f.reference)).toThrow();
  });
  it('rejects duplicate/cross-city route identities and declared counts that lose routes',()=>{
    const f=fixture();
    expect(()=>parseTransitCity({...f.shard,routes:[route,route]},{...f.city,route_count:2})).toThrow();
    expect(()=>parseTransitCity({...f.shard,routes:[{...route,city_code:'25'}]},f.city)).toThrow();
    expect(()=>parseTransitCity({...f.shard,routes:[]},f.city)).toThrow();
  });
  it('preserves the official Hangul route identifiers used in Gumi',()=>{
    const f=fixture(),gumi={...route,city_code:'37050',route_id:'GMB수점10',id:'tago-37050-gmb수점10',route_no:'수점'};
    const city={...f.city,city_code:'37050',city_name:'구미'};
    expect(parseTransitCity({...f.shard,city_code:'37050',city_name:'구미',routes:[gumi]},city).routes[0].route_id).toBe('GMB수점10');
  });
  it('uses a verified national allowlist and keeps selected route snapshots fresh',async()=>{
    const f=fixture(),reader=createTransitCatalogReader(f.fetcher);
    const upstream=vi.fn<typeof fetch>(async()=>Response.json({response:{header:{resultCode:'00'},body:{pageNo:1,numOfRows:100,totalCount:0,items:''}}}));
    const handler=createLiveTransitHandler({fetcher:upstream,busCatalog:async()=>({reference:f.reference,resolve:id=>reader.route(f.reference,id)})});
    const env={DATA_GO_KR_SERVICE_KEY:'private-test-key'};
    const targets=await handler(new Request('https://replay.test/api/v1/live/transit/targets'),env);
    expect(await targets!.json()).toMatchObject({bus_catalog:{...f.reference,configured:true}});
    expect(upstream).not.toHaveBeenCalled();
    const result=await handler(new Request(`https://replay.test/api/v1/live/transit/bus?route=${route.id}`),env);
    expect(await result!.json()).toMatchObject({status:'empty',refresh_after_seconds:90,target:{id:route.id}});
    expect(upstream).toHaveBeenCalledTimes(1);
    const bad=await handler(new Request('https://replay.test/api/v1/live/transit/bus?route=tago-12-unknown'),env);
    expect(bad!.status).toBe(400);expect(upstream).toHaveBeenCalledTimes(1);
  });
});
