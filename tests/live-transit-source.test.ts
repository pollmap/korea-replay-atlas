import {DatabaseSync} from 'node:sqlite';
import {describe,expect,it,vi} from 'vitest';
import {createLiveTransitHandler,fetchCanonicalTransitSnapshot,type CanonicalTransitSourceRequest} from '../worker/live-transit';
import {createLiveBroker} from '../worker/live-broker';
import {SqliteLiveBrokerStore,type BrokerSqlStorage} from '../worker/live-broker-store';
import {decodeLiveTransit} from '../src/live-client';

const now=Date.parse('2026-09-19T12:00:00Z');
const target={id:'busan-1001',city_code:'21',route_id:'BSB1001',label:'부산 1001'};
const key='fixture-private-source-key';
const row=(index:number)=>({vehicleno:`fixture-${index}`,gpslong:129.1,gpslati:35.2});
const page=(rows:unknown[],pageNo=1,totalCount=rows.length)=>({response:{header:{resultCode:'00'},body:{pageNo,numOfRows:100,totalCount,items:{item:rows}}}});
function request(fetcher:typeof fetch,extra:Partial<CanonicalTransitSourceRequest>={}):CanonicalTransitSourceRequest {
  return {kind:'bus',target,env:{DATA_GO_KR_SERVICE_KEY:key},ttlSeconds:90,signal:new AbortController().signal,fetcher,now:()=>now,...extra};
}

describe('canonical transit source for a durable broker',()=>{
  it.each(['GMB수점10','GMB수점20','GMB수점30'])('preserves the official Hangul route ID %s in a canonical source request',async routeId=>{
    const fetcher=vi.fn<typeof fetch>(async input=>{
      const url=new URL(String(input));
      expect(url.origin).toBe('https://apis.data.go.kr');expect(url.searchParams.get('cityCode')).toBe('37050');
      expect(url.searchParams.get('routeId')).toBe(routeId);
      return Response.json(page([row(1)]));
    });
    const result=await fetchCanonicalTransitSnapshot(request(fetcher,{target:{id:'broker-bus',city_code:'37050',route_id:routeId,label:routeId}}));
    expect(result).toMatchObject({httpStatus:200,snapshot:{status:'available',target:{id:'broker-bus',label:routeId},
      vehicles:[{kind:'bus',city_code:'37050',route_id:routeId,observed_at:null}]}});
    expect(fetcher).toHaveBeenCalledTimes(1);expect(JSON.stringify(result)).not.toContain(key);
  });
  it.each(['GMB수점10','GMB수점20','GMB수점30'])('queries %s through the actual broker and preserves aliases without direct fallback',async routeId=>{
    const db=new DatabaseSync(':memory:');
    try{
      const adapter:BrokerSqlStorage={
        sql:{exec:(query,...bindings)=>({toArray:()=>db.prepare(query).all(...bindings) as Record<string,unknown>[]})},
        transactionSync:<T>(callback:()=>T)=>{db.exec('BEGIN');try{const value=callback();db.exec('COMMIT');return value;}catch(error){db.exec('ROLLBACK');throw error;}},
        sync:async()=>undefined,
      };
      const upstream=vi.fn<typeof fetch>(async input=>{
        expect(new URL(String(input)).searchParams.get('routeId')).toBe(routeId);
        return Response.json(page([row(1)]));
      });
      const broker=createLiveBroker({store:new SqliteLiveBrokerStore(adapter),credentials:()=>key,now:()=>now,fetcher:upstream,
        source:context=>fetchCanonicalTransitSnapshot({...context,now:()=>now,env:{DATA_GO_KR_SERVICE_KEY:context.credential}})});
      const recommended={id:'gumi-hangul',city_code:'37050',route_id:routeId,label:'구미 추천 노선'};
      const national={...recommended,id:`tago-37050-${routeId.toLowerCase()}`,label:'구미 전국 목록 노선'};
      const direct=vi.fn<typeof fetch>();
      const handle=createLiveTransitHandler({busRoutes:[recommended],now:()=>now,fetcher:direct,busCatalog:async()=>({
        reference:{url:'/data/live-transit/routes/0123456789abcdef/manifest.json',sha256:'a'.repeat(64),byte_length:300},
        resolve:async id=>id===national.id?national:null,
      })});
      const service={fetch:((input:RequestInfo|URL,init?:RequestInit)=>broker.fetch(input instanceof Request?input:new Request(input,init))) as Fetcher['fetch']};
      const env={LIVE_TRANSIT_MODE:'broker',LIVE_TRANSIT_BROKER:service,DATA_GO_KR_SERVICE_KEY:'fixture-unused-direct-key'};
      for(const selected of [national,recommended]){
        const response=await handle(new Request(`https://map.test/api/v1/live/transit/bus?route=${encodeURIComponent(selected.id)}`),env);
        expect(response!.status).toBe(200);
        const body=decodeLiveTransit(await response!.json());
        expect(body).toMatchObject({status:'available',target:{id:selected.id,label:selected.label},
          vehicles:[{route_id:routeId,city_code:'37050',label:selected.label}],quota:{guard:'durable-object',global_enforced:true}});
        expect(JSON.stringify(body)).not.toContain(key);
      }
      expect(upstream).toHaveBeenCalledTimes(1);expect(direct).not.toHaveBeenCalled();
      expect(db.prepare('SELECT SUM(used) AS used FROM broker_usage').get()).toMatchObject({used:1});
    }finally{db.close();}
  });
  it('routes every page through the guarded fetcher and never reuses an isolate cache',async()=>{
    const fetcher=vi.fn<typeof fetch>(async input=>{
      const n=Number(new URL(String(input)).searchParams.get('pageNo'));
      return Response.json(page(Array.from({length:n===1?100:1},(_,i)=>row((n-1)*100+i)),n,101));
    });
    const one=await fetchCanonicalTransitSnapshot(request(fetcher));
    const two=await fetchCanonicalTransitSnapshot(request(fetcher));
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(one).toMatchObject({httpStatus:200,snapshot:{status:'available',counts:{accepted:101}}});
    expect(two.snapshot.vehicles).toEqual(one.snapshot.vehicles);
    expect(one.snapshot.expires_at).toBe(new Date(now+90000).toISOString());
    expect(JSON.stringify(one)).not.toContain(key);
    expect(fetcher.mock.calls.every(([,init])=>init?.redirect==='manual')).toBe(true);
  });
  it('discards all pages if the guarded second request is denied without reflecting its exception',async()=>{
    const fetcher=vi.fn<typeof fetch>(async()=>{
      if(fetcher.mock.calls.length===2)throw new Error(`reservation failed ${key}`);
      return Response.json(page(Array.from({length:100},(_,i)=>row(i)),1,101));
    });
    const result=await fetchCanonicalTransitSnapshot(request(fetcher));
    expect(result.httpStatus).toBe(502);expect(result.snapshot.vehicles).toEqual([]);
    expect(result.snapshot.retrieved_at).toBeNull();expect(JSON.stringify(result)).not.toContain(key);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('refuses malformed targets, missing credentials, invalid TTLs and pre-aborted jobs before fetching',async()=>{
    const fetcher=vi.fn<typeof fetch>();const aborted=new AbortController();aborted.abort();
    for(const extra of [{target:{...target,route_id:'https://invalid.test'}},{target:{...target,route_id:'GMB수점10&serviceKey=other'}},
      {target:{...target,route_id:'GMB수점10/../other'}},{env:{}},{ttlSeconds:0},{ttlSeconds:3601},{signal:aborted.signal}]) {
      const result=await fetchCanonicalTransitSnapshot(request(fetcher,extra));
      expect(result.httpStatus).toBeGreaterThanOrEqual(400);expect(result.snapshot.vehicles).toEqual([]);
    }
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('retains Seoul reception time and station status without inventing positions',async()=>{
    const fetcher=vi.fn<typeof fetch>(async()=>Response.json({errorMessage:{code:'INFO-000',total:1},realtimePositionList:[{
      subwayId:'1001',trainNo:'123',statnId:'1001000127',statnNm:'동묘앞',statnTnm:'청량리',updnLine:'0',trainSttus:'1',recptnDt:'2026-09-19 20:59:00',
    }]}));
    const result=await fetchCanonicalTransitSnapshot(request(fetcher,{kind:'subway',target:{id:'seoul-line-1',name:'1호선',subway_id:'1001'},env:{SEOUL_SUBWAY_API_KEY:'fixture_seoul_key'},ttlSeconds:120}));
    expect(result.snapshot.vehicles[0]).toMatchObject({kind:'subway',position:null,observed_at:null,source_received_at:'2026-09-19T11:59:00.000Z',station_mapping:'unresolved'});
    expect(new URL(String(fetcher.mock.calls[0][0])).protocol).toBe('https:');
  });
});

describe('Seoul flat empty envelopes keep provider errors distinct',()=>{
  const subway={id:'seoul-line-2',name:'2호선',subway_id:'1002'};
  const seoulKey='fixture_seoul_empty_key';
  const query=async(body:unknown,httpStatus=200)=>{
    const fetcher=vi.fn<typeof fetch>(async()=>Response.json(body,{status:httpStatus}));
    const result=await fetchCanonicalTransitSnapshot(request(fetcher,{
      kind:'subway',target:subway,env:{SEOUL_SUBWAY_API_KEY:seoulKey},ttlSeconds:120,
    }));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(result)).not.toContain(seoulKey);
    return result;
  };
  it.each([
    {code:'INFO-200',status:500,total:0},
    {code:'INFO-200',status:500,total:0,realtimePositionList:[]},
    {code:'INFO-200',status:500,total:'0',realtimePositionList:[]},
  ])('accepts explicit zero with an absent or empty list regardless of provider body status: %j',async body=>{
    const result=await query(body);
    expect(result).toMatchObject({httpStatus:200,snapshot:{status:'empty',target:{id:subway.id,label:subway.name},
      vehicles:[],counts:{upstream:0,accepted:0,invalid:0,stale:0,duplicate:0,ambiguous:0},
      coverage:{scope:'selected-line',complete:true},refresh_after_seconds:120,
      retrieved_at:new Date(now).toISOString(),expires_at:new Date(now+120_000).toISOString()}});
    expect(result.snapshot.error).toBeUndefined();
    expect(decodeLiveTransit(result.snapshot)?.status).toBe('empty');
  });
  it.each([
    {code:'INFO-200'},
    {code:'INFO-200',total:null},
    {code:'INFO-200',total:false},
    {code:'INFO-200',total:1,realtimePositionList:[]},
    {code:'INFO-200',total:0,realtimePositionList:[{}]},
    {code:'INFO-200',total:1,realtimePositionList:[{}]},
    {code:'INFO-200',total:0,realtimePositionList:null},
    {code:'INFO-200',total:0,realtimePositionList:{}},
  ])('rejects contradictory or incomplete flat empty metadata: %j',async body=>{
    const result=await query(body);
    expect(result.httpStatus).toBe(502);expect(result.snapshot.status).toBe('unavailable');
    expect(result.snapshot.vehicles).toEqual([]);expect(result.snapshot.retrieved_at).toBeNull();
    expect(result.snapshot.error?.code).toMatch(/upstream_inconsistent|response_limit/);
  });
  it.each([null,{},'',false,[],{code:'ERROR-300'}])('does not bypass a present errorMessage with a root empty code: %j',async errorMessage=>{
    const result=await query({code:'INFO-200',total:0,errorMessage});
    expect(result.snapshot.status).toBe('unavailable');expect(result.snapshot.retrieved_at).toBeNull();
    expect(result.snapshot.error?.code).toBe(errorMessage&&typeof errorMessage==='object'&&'code' in errorMessage?'upstream_auth':'upstream_invalid');
  });
  it.each([
    ['INFO-000','upstream_invalid',502],
    ['INFO-100','upstream_auth',503],
    ['ERROR-300','upstream_auth',503],
    ['ERROR-301','upstream_auth',503],
    ['ERROR-336','quota_exceeded',429],
    ['ERROR-999','upstream_invalid',502],
  ] as const)('preserves flat %s error classification without reflecting its message',async(code,error,httpStatus)=>{
    const result=await query({code,status:500,total:0,realtimePositionList:[],message:`private ${seoulKey}`});
    expect(result.httpStatus).toBe(httpStatus);expect(result.snapshot.error?.code).toBe(error);
    expect(result.snapshot.status).toBe('unavailable');expect(result.snapshot.retrieved_at).toBeNull();
  });
  it('does not promote a flat INFO-000 with nonempty rows into the nested success contract',async()=>{
    const result=await query({code:'INFO-000',total:1,realtimePositionList:[{
      subwayId:'1002',trainNo:'123',statnId:'1002000201',statnNm:'시청',updnLine:'0',trainSttus:'1',recptnDt:'2026-09-19 20:59:00',
    }]});
    expect(result.snapshot.error?.code).toBe('upstream_invalid');expect(result.snapshot.vehicles).toEqual([]);
  });
  it('retains nested success and empty contracts without consulting conflicting root fields',async()=>{
    const empty=await query({code:'ERROR-300',total:1,errorMessage:{code:'INFO-200',total:0}});
    expect(empty.snapshot.status).toBe('empty');
    const success=await query({code:'INFO-200',total:0,errorMessage:{code:'INFO-000',total:1},realtimePositionList:[{
      subwayId:'1002',trainNo:'123',statnId:'1002000201',statnNm:'시청',updnLine:'0',trainSttus:'1',recptnDt:'2026-09-19 20:59:00',
    }]});
    expect(success.snapshot.status).toBe('available');
    expect(success.snapshot.vehicles[0]).toMatchObject({kind:'subway',line_id:'1002',observed_at:null,position:null,
      source_received_at:'2026-09-19T11:59:00.000Z',station_mapping:'unresolved'});
  });
  it('keeps transport HTTP failures authoritative over an empty body code',async()=>{
    const result=await query({code:'INFO-200',status:500,total:0},500);
    expect(result.snapshot.error?.code).toBe('upstream_http');expect(result.snapshot.retrieved_at).toBeNull();
  });
});
