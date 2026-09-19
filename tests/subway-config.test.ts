import {DatabaseSync} from 'node:sqlite';
import {describe,expect,it,vi} from 'vitest';
import targets from '../config/live-transit-routes.json';
import {createLiveBroker} from '../worker/live-broker';
import {SqliteLiveBrokerStore,type BrokerSqlStorage} from '../worker/live-broker-store';
import {createLiveTransitHandler,fetchCanonicalTransitSnapshot,type LiveTransitEnv} from '../worker/live-transit';
import {decodeLiveTargets,decodeLiveTransit} from '../src/live-client';

// OA-12601 openApiView + its downloadable specification, checked 2026-09-19.
// This reference tests the provider's explicit pairs, not inferred railway codes.
const OFFICIAL_PAIRS=[
  ['1001','1호선'],['1002','2호선'],['1003','3호선'],['1004','4호선'],['1005','5호선'],
  ['1006','6호선'],['1007','7호선'],['1008','8호선'],['1009','9호선'],
  ['1063','경의중앙선'],['1065','공항철도'],['1067','경춘선'],['1075','수인분당선'],
  ['1077','신분당선'],['1092','우이신설선'],['1032','GTX-A'],
];
const START=Date.parse('2026-09-19T12:00:00Z');

describe('official Seoul provider line configuration',()=>{
  it('preserves the published line-one selector and exact official pairs without duplicating IDs or bus aliases',()=>{
    expect(targets.subway_lines.map(line=>[line.subway_id,line.name])).toEqual(OFFICIAL_PAIRS);
    for(const field of ['id','name','subway_id'] as const)expect(new Set(targets.subway_lines.map(line=>line[field])).size).toBe(16);
    expect(targets.subway_lines.find(line=>line.subway_id==='1001')).toEqual({id:'seoul-line-1',name:'1호선',subway_id:'1001'});
    expect(targets.subway_lines.every(line=>/^[a-z0-9][a-z0-9-]{0,39}$/.test(line.id))).toBe(true);
    expect(targets.bus_routes).toEqual([
      {id:'daejeon-202',city_code:'25',route_id:'DJB30300052',label:'대전·계룡 202'},
      {id:'sejong-b2-osong',city_code:'12',route_id:'SJB293000077',label:'세종 B2 · 오송 방면'},
      {id:'cheongju-747',city_code:'33010',route_id:'CJB270011200',label:'청주 747'},
    ]);
  });

  it('routes every configured selector through the shared broker with one fixed budget, source names and null GPS semantics',async()=>{
    const db=new DatabaseSync(':memory:');
    try{
      const storage:BrokerSqlStorage={
        sql:{exec:(query,...bindings)=>({toArray:()=>db.prepare(query).all(...bindings) as Record<string,unknown>[]})},
        transactionSync:<T>(callback:()=>T)=>{db.exec('BEGIN');try{const value=callback();db.exec('COMMIT');return value;}catch(error){db.exec('ROLLBACK');throw error;}},
        sync:async()=>undefined,
      };
      const source=vi.fn<typeof fetch>(async input=>{
        const url=new URL(String(input)),name=decodeURIComponent(url.pathname.split('/').at(-1)!);
        const line=targets.subway_lines.find(value=>value.name===name);
        expect(url.origin).toBe('http://swopenapi.seoul.go.kr');
        expect(url.pathname).toContain('/json/realtimePosition/0/400/');
        expect(line).toBeDefined();
        return Response.json({errorMessage:{code:'INFO-000',total:1},realtimePositionList:[{
          subwayId:line!.subway_id,subwayNm:line!.name,trainNo:'0001',statnId:'9999999999',statnNm:'오프라인 검증역',
          statnTnm:'오프라인 종착역',updnLine:'0',trainSttus:'1',recptnDt:'2026-09-19 20:59:00',
        }]});
      });
      const broker=createLiveBroker({store:new SqliteLiveBrokerStore(storage),subwayLines:targets.subway_lines,now:()=>START,
        credentials:()=> 'fixture_seoul_provider_key',allowSubwayHttp:true,fetcher:source,
        source:context=>fetchCanonicalTransitSnapshot({...context,now:()=>START,
          env:{SEOUL_SUBWAY_API_KEY:context.credential,SEOUL_SUBWAY_ALLOW_HTTP:'true'}})});
      const directSource=vi.fn<typeof fetch>();
      const handler=createLiveTransitHandler({busRoutes:targets.bus_routes,subwayLines:targets.subway_lines,fetcher:directSource,now:()=>START});
      const env:LiveTransitEnv={LIVE_TRANSIT_MODE:'broker',LIVE_TRANSIT_BROKER:{
        fetch:((input:RequestInfo|URL,init?:RequestInit)=>broker.fetch(input instanceof Request?input:new Request(input,init))) as Fetcher['fetch'],
      }};
      const response=await handler(new Request('https://map.test/api/v1/live/transit/targets'),env);
      const list=decodeLiveTargets(await response!.json());
      expect(list).toMatchObject({global_quota_enforced:true,global_quota_scope:'broker-mediated-requests',continuous_collection:false});
      expect(list.subway_lines).toHaveLength(16);
      expect(list.subway_lines.every(line=>line.configured&&line.refresh_after_seconds===120)).toBe(true);
      expect(list.bus_routes.every(route=>route.refresh_after_seconds===90)).toBe(true);
      expect(source).not.toHaveBeenCalled();
      for(const line of targets.subway_lines){
        const result=await handler(new Request(`https://map.test/api/v1/live/transit/subway?line=${line.id}`),env);
        expect(result!.status).toBe(200);
        const snapshot=decodeLiveTransit(await result!.json());
        expect(snapshot).toMatchObject({target:{id:line.id,label:line.name},refresh_after_seconds:120,
          quota:{daily_limit:1000,local_budget:800,budget_limit:800,guard:'durable-object',global_enforced:true,scope:'broker-mediated-requests'},
          vehicles:[{line_id:line.subway_id,observed_at:null,position:null,station_mapping:'unresolved',source_received_at:'2026-09-19T11:59:00.000Z'}]});
      }
      expect(source).toHaveBeenCalledTimes(16);
      expect(await broker.status()).toMatchObject({daily_limits:{bus:10000,subway:1000},budget_limits:{bus:8000,subway:800},
        ttl_seconds:{bus:90,subway:120},reserved:{bus:0,subway:16},active_leases:0});
      const first=targets.subway_lines[0];
      expect(await broker.query({protocol:1,target:{kind:'subway',subway_id:first.subway_id,name:first.name}})).toMatchObject({ok:true});
      expect(await broker.query({protocol:1,target:{kind:'subway',subway_id:first.subway_id,name:'GTX-A'}})).toMatchObject({ok:false,error:{code:'invalid_request'}});
      expect(source).toHaveBeenCalledTimes(16);expect(directSource).not.toHaveBeenCalled();
    }finally{db.close();}
  });

  it('rejects duplicate public or provider IDs before reading broker status or spending an upstream request',async()=>{
    for(const duplicate of [{...targets.subway_lines[1],id:'seoul-line-1'}, {...targets.subway_lines[1],subway_id:'1001'}]){
      const lines=[targets.subway_lines[0],duplicate],fetcher=vi.fn<typeof fetch>(),service=vi.fn<Fetcher['fetch']>();
      const handler=createLiveTransitHandler({subwayLines:lines,fetcher});
      const response=await handler(new Request('https://map.test/api/v1/live/transit/targets'),
        {LIVE_TRANSIT_MODE:'broker',LIVE_TRANSIT_BROKER:{fetch:service}});
      expect(response!.status).toBe(503);expect(await response!.json()).toMatchObject({error:{code:'invalid_configuration'}});
      expect(service).not.toHaveBeenCalled();expect(fetcher).not.toHaveBeenCalled();
    }
  });
});
