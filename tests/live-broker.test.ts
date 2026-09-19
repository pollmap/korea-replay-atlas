import {DatabaseSync} from 'node:sqlite';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {createLiveBroker,fixedLiveBrokerStub,LIVE_BROKER_OBJECT_NAME,parseBrokerRequest,type BrokerRequest,type LiveBrokerOptions} from '../worker/live-broker';
import {BrokerStoreFailure,SqliteLiveBrokerStore,type BrokerSqlStorage} from '../worker/live-broker-store';
import {fetchCanonicalTransitSnapshot} from '../worker/live-transit';
import brokerEntry,{type LiveBrokerEnv} from '../worker/live-broker-entry';
import {LIVE_BROKER_STATUS_PATH,LIVE_BROKER_MAX_SNAPSHOT_BYTES,LIVE_BROKER_RESPONSE_TIMEOUT_MS,
  LIVE_BROKER_MAX_SOURCE_TIMEOUT_MS,LIVE_BROKER_REQUEST_BODY_TIMEOUT_MS,type BrokerStatus} from '../shared/live-broker';

const databases:DatabaseSync[]=[];
afterEach(()=>{for(const db of databases.splice(0))db.close();vi.restoreAllMocks();});
const START=Date.parse('2026-09-19T14:59:00Z');
const KEY='fixture-broker-private-key';
const bus=(route='SJB293000077'):BrokerRequest=>({protocol:1,target:{kind:'bus',city_code:'12',route_id:route}});
const subway:BrokerRequest={protocol:1,target:{kind:'subway',subway_id:'1001',name:'1호선'}};
function database(){const db=new DatabaseSync(':memory:');databases.push(db);return db;}
function storage(db=database()){
  const sync=vi.fn(async()=>undefined);
  const adapter:BrokerSqlStorage={
    sql:{exec:(query,...bindings)=>{const rows=db.prepare(query).all(...bindings) as Record<string,unknown>[];return {toArray:()=>rows};}},
    transactionSync:<T>(callback:()=>T)=>{db.exec('BEGIN');try{const value=callback();db.exec('COMMIT');return value;}catch(error){db.exec('ROLLBACK');throw error;}},
    sync,
  };
  return {db,adapter,sync,store:new SqliteLiveBrokerStore(adapter)};
}
/** Synthetic native getters test wiring only; these are never Node SQLite cost estimates. */
function meteredStorage(state=storage()){
  const execute=state.adapter.sql.exec;let reportedRead=0,reportedWritten=0,completed=0,readGetters=0,writtenGetters=0;
  state.adapter.sql.exec=(query,...bindings)=>{
    const cursor=execute(query,...bindings),read=3,written=query.startsWith('SELECT ')?0:5;let consumed=false;
    return {
      toArray:()=>{
        if(consumed)throw new Error('fixture cursor consumed twice');
        const rows=cursor.toArray();consumed=true;reportedRead+=read;reportedWritten+=written;completed++;return rows;
      },
      get rowsRead(){if(!consumed)throw new Error('fixture cursor incomplete');readGetters++;return read;},
      get rowsWritten(){if(!consumed)throw new Error('fixture cursor incomplete');writtenGetters++;return written;},
    };
  };
  return {...state,meter:()=>({sql_rows_read:reportedRead,sql_rows_written:reportedWritten}),reads:()=>({completed,readGetters,writtenGetters})};
}
function page(pageNo=1,total=0){
  const count=Math.min(100,Math.max(0,total-(pageNo-1)*100));
  const rows=Array.from({length:count},(_,i)=>({vehicleno:`fixture-${(pageNo-1)*100+i}`,gpslong:127.29,gpslati:36.49,nodeid:'NODE1',nodenm:'정류장',nodeord:2}));
  return {response:{header:{resultCode:'00'},body:{pageNo,numOfRows:100,totalCount:total,items:rows.length?{item:rows}:''}}};
}
function setup(overrides:Partial<LiveBrokerOptions>={},shared?:ReturnType<typeof storage>){
  const state=shared??storage();let clock=START,secret=KEY;
  const fetcher=vi.fn<typeof fetch>(async()=>Response.json(page()));
  const source:LiveBrokerOptions['source']=context=>fetchCanonicalTransitSnapshot({...context,now:()=>clock,
    env:{DATA_GO_KR_SERVICE_KEY:context.credential,SEOUL_SUBWAY_API_KEY:context.credential,SEOUL_SUBWAY_ALLOW_HTTP:overrides.allowSubwayHttp?'true':'false'}});
  const broker=createLiveBroker({store:state.store,source,credentials:()=>secret,subwayLines:[{subway_id:'1001',name:'1호선'}],now:()=>clock,fetcher,...overrides});
  return {...state,broker,fetcher,setTime:(value:number)=>{clock=value;},setKey:(value:string)=>{secret=value;}};
}
function used(db:DatabaseSync){return Number((db.prepare('SELECT COALESCE(SUM(used),0) AS count FROM broker_usage').get() as {count:number}).count);}
function changes(db:DatabaseSync){return Number((db.prepare('SELECT total_changes() AS changes').get() as {changes:number}).changes);}

describe('private read-only broker status',()=>{
  it('reports configured credential formats and fixed policy without verifying or exposing credentials',async()=>{
    const source=vi.fn<LiveBrokerOptions['source']>();
    const {broker,store,db,fetcher,setKey}=setup({source});await store.initialize();const initial=changes(db);
    const response=await broker.fetch(new Request(`https://private.test${LIVE_BROKER_STATUS_PATH}`));
    expect(response.status).toBe(200);expect(response.headers.get('Cache-Control')).toBe('no-store');
    expect(response.headers.get('X-Content-Type-Options')).toBe('nosniff');
    const body=await response.json() as BrokerStatus;
    expect(body).toEqual({protocol:1,ok:true,httpStatus:200,configured:{bus:true,subway:true},
      guard:'durable-object',global_enforced:true,scope:'broker-mediated-requests',window:'rolling-24h-conservative',
      ttl_seconds:{bus:90,subway:120},daily_limits:{bus:10000,subway:1000},budget_limits:{bus:8000,subway:800},
      reserved:{bus:0,subway:0},active_leases:0,diagnostics:{scope:'activation',source:'sql-cursor',activation_id:expect.any(String),
        sql_rows_read:null,sql_rows_written:null,includes_initialization:true,includes_status_reads:true}});
    setKey('fixture/encoded+key=');expect(await broker.status()).toMatchObject({configured:{bus:true,subway:false}});
    for(const key of ['sample','short','has whitespace','x'.repeat(513),'']){
      setKey(key);expect(await broker.status()).toMatchObject({configured:{bus:false,subway:false}});
    }
    expect(changes(db)).toBe(initial);expect(source).not.toHaveBeenCalled();expect(fetcher).not.toHaveBeenCalled();
    expect(JSON.stringify(body)).not.toMatch(/fixture|fingerprint|epoch|cache_key|session|job/);
    expect(LIVE_BROKER_MAX_SNAPSHOT_BYTES).toBe(512*1024);
    expect(LIVE_BROKER_RESPONSE_TIMEOUT_MS).toBeGreaterThan(LIVE_BROKER_MAX_SOURCE_TIMEOUT_MS+LIVE_BROKER_REQUEST_BODY_TIMEOUT_MS);
  });
  it('reads actual reservations during two active requests without spending, waiting for, or releasing their slots',async()=>{
    const bodies:ReadableStreamDefaultController<Uint8Array>[]=[];
    const native=vi.fn<typeof fetch>(async()=>new Response(new ReadableStream<Uint8Array>({start(controller){bodies.push(controller);}})));
    const {broker,db}=setup({fetcher:native});
    const pending=[broker.query(bus('ONE')),broker.query(bus('ONE')),broker.query(bus('TWO'))];
    await vi.waitFor(()=>expect(bodies).toHaveLength(2));const before=changes(db);
    const statuses=await Promise.all(Array.from({length:6},()=>broker.status()));
    for(const status of statuses)expect(status).toMatchObject({ok:true,reserved:{bus:2,subway:0},active_leases:2});
    expect(changes(db)).toBe(before);expect(native).toHaveBeenCalledTimes(2);
    expect(await broker.query(bus('THREE'))).toMatchObject({ok:false,error:{code:'busy'}});
    for(const controller of bodies){controller.enqueue(new TextEncoder().encode(JSON.stringify(page())));controller.close();}
    expect((await Promise.all(pending)).every(value=>value.ok)).toBe(true);
    expect(await broker.status()).toMatchObject({ok:true,reserved:{bus:2,subway:0},active_leases:0});
  });
  it('counts both upstream pages and preserves expired rows while status performs SELECTs only',async()=>{
    const {broker,db,store,adapter,setTime}=setup({fetcher:async input=>Response.json(page(Number(new URL(String(input)).searchParams.get('pageNo')),101))});
    expect(await broker.query(bus())).toMatchObject({ok:true});
    await store.blockAuthentication('subway','a'.repeat(64),START);setTime(START+301_000);
    const prior=changes(db),reads=vi.spyOn(adapter.sql,'exec');
    expect(await broker.status()).toMatchObject({ok:true,reserved:{bus:2,subway:0},active_leases:0});
    expect(reads.mock.calls.every(([statement])=>statement.startsWith('SELECT '))).toBe(true);
    expect(changes(db)).toBe(prior);
    expect(db.prepare('SELECT COUNT(*) AS count FROM broker_cache').get()).toMatchObject({count:1});
    expect(db.prepare('SELECT COUNT(*) AS count FROM broker_auth_blocks').get()).toMatchObject({count:1});
  });
  it('keeps restart reservations and reports lease expiry without deleting or reclaiming a slot',async()=>{
    const first=storage();await first.store.initialize();
    const live=await first.store.acquire('private-job','private-cache-key','bus',START);await first.store.reserve(live,8000,START);
    const second=setup({},storage(first.db));await second.store.initialize();const initial=changes(first.db);
    second.setTime(START+1000);expect(await second.broker.status()).toMatchObject({reserved:{bus:1,subway:0},active_leases:1});
    second.setTime(START+31_000);expect(await second.broker.status()).toMatchObject({reserved:{bus:1,subway:0},active_leases:0});
    // An existing activation still running this job continues to hold its own slot.
    expect(first.store.status(START+31_000)).toMatchObject({reserved:{bus:1,subway:0},active_leases:1});
    second.setTime(START+25*3_600_000);expect(await second.broker.status()).toMatchObject({reserved:{bus:0,subway:0},active_leases:0});
    expect(changes(first.db)).toBe(initial);expect(used(first.db)).toBe(1);
    expect(first.db.prepare('SELECT COUNT(*) AS count FROM broker_leases').get()).toMatchObject({count:1});
    expect(second.fetcher).not.toHaveBeenCalled();
  });
  it('fails closed on status storage failure and pre-abort without exposing exceptions or calling upstream',async()=>{
    const {broker,store,fetcher}=setup();await store.initialize();
    const read=vi.spyOn(store,'status').mockImplementation(()=>{throw new Error(`fixture status failure ${KEY}`);});
    const response=await broker.fetch(new Request(`https://private.test${LIVE_BROKER_STATUS_PATH}`));
    expect(response.status).toBe(503);const text=await response.text();
    expect(JSON.parse(text)).toMatchObject({ok:false,error:{code:'broker_unavailable'}});expect(text).not.toContain(KEY);
    const controller=new AbortController();controller.abort();read.mockClear();
    expect(await broker.status(controller.signal)).toMatchObject({ok:false,error:{code:'aborted'}});
    expect(read).not.toHaveBeenCalled();expect(fetcher).not.toHaveBeenCalled();
  });
  it('only dispatches the fixed internal GET status path, with no query, reset, or write endpoint',async()=>{
    const {broker,fetcher}=setup();
    const dispatch=vi.fn((request:Request)=>broker.fetch(request)),get=vi.fn(()=>({fetch:dispatch}));
    const idFromName=vi.fn((name:string)=>name),env={LIVE_TRANSIT_COORDINATOR:{idFromName,get}} as unknown as LiveBrokerEnv;
    const good=await brokerEntry.fetch(new Request(`https://private.test${LIVE_BROKER_STATUS_PATH}`),env);
    expect(good.status).toBe(200);expect(idFromName).toHaveBeenCalledExactlyOnceWith('transit-production');
    for(const [path,method] of [['/v1/status?reset=true','GET'],['/v1/status','POST'],['/v1/status','DELETE'],['/v1/status/','GET'],['/v1/reset','POST'],['/v1/snapshot','GET']]){
      expect((await brokerEntry.fetch(new Request(`https://private.test${path}`,{method}),env)).status).toBe(404);
    }
    expect(dispatch).toHaveBeenCalledTimes(1);expect(fetcher).not.toHaveBeenCalled();
  });
});

describe('activation-scoped native SQL cursor diagnostics',()=>{
  it('sums completed native cursor getters once and includes initialization and status without SQL writes',async()=>{
    const state=meteredStorage(),{broker}=setup({},state);await state.store.initialize();
    const first=await broker.status();expect(first).toMatchObject({ok:true,diagnostics:{...state.meter(),includes_initialization:true,includes_status_reads:true}});
    if(!first.ok)throw new Error('fixture status unavailable');
    expect(first.diagnostics?.sql_rows_written).toBeGreaterThan(0);
    const before=changes(state.db),second=await broker.status();
    expect(second).toMatchObject({ok:true,diagnostics:{...state.meter(),activation_id:first.diagnostics?.activation_id}});
    if(!second.ok)throw new Error('fixture status unavailable');
    expect(second.diagnostics?.sql_rows_read).toBeGreaterThan(first.diagnostics!.sql_rows_read!);
    expect(second.diagnostics?.sql_rows_written).toBe(first.diagnostics?.sql_rows_written);expect(changes(state.db)).toBe(before);
    expect(await broker.query(bus())).toMatchObject({ok:true});const afterMiss=await broker.status();
    expect(afterMiss).toMatchObject({ok:true,diagnostics:state.meter()});
    if(!afterMiss.ok)throw new Error('fixture status unavailable');
    expect(afterMiss.diagnostics?.sql_rows_written).toBeGreaterThan(second.diagnostics!.sql_rows_written!);
    const afterMissChanges=changes(state.db);expect(await broker.query(bus())).toMatchObject({ok:true});
    expect(await broker.status()).toMatchObject({diagnostics:{...state.meter(),sql_rows_written:afterMiss.diagnostics?.sql_rows_written}});
    expect(changes(state.db)).toBe(afterMissChanges);
    const reads=state.reads();expect(reads).toEqual({completed:reads.completed,readGetters:reads.completed,writtenGetters:reads.completed});
    const restarted=meteredStorage(storage(state.db)),next=setup({},restarted);const nextStatus=await next.broker.status();
    expect(nextStatus).toMatchObject({ok:true,reserved:{bus:1,subway:0},diagnostics:restarted.meter()});
    if(!nextStatus.ok)throw new Error('fixture status unavailable');
    expect(nextStatus.diagnostics?.activation_id).not.toBe(first.diagnostics?.activation_id);
    expect(nextStatus.diagnostics?.sql_rows_read).toBeLessThan(state.meter().sql_rows_read);
  });
  it('does not infer native counters from local total_changes when getters are absent',async()=>{
    const {broker,db}=setup();expect(await broker.query(bus())).toMatchObject({ok:true});expect(changes(db)).toBeGreaterThan(0);
    const response=await broker.fetch(new Request(`https://private.test${LIVE_BROKER_STATUS_PATH}`));
    expect(await response.json()).toMatchObject({diagnostics:{scope:'activation',source:'sql-cursor',sql_rows_read:null,sql_rows_written:null}});
  });
  it('keeps the whole activation unmeasured after a missing, throwing, invalid or overflowing getter without breaking queries',async()=>{
    for(const fault of [undefined,NaN,Infinity,-1,0.5,Number.MAX_SAFE_INTEGER,'throw'] as const){
      const state=meteredStorage();await state.store.initialize();const execute=state.adapter.sql.exec;
      state.adapter.sql.exec=(query,...bindings)=>{
        const cursor=execute(query,...bindings);
        return {toArray:()=>cursor.toArray(),get rowsRead(){if(fault==='throw')throw new Error('fixture getter unavailable');return fault;},get rowsWritten(){return cursor.rowsWritten;}};
      };
      expect(state.store.status(START)).toMatchObject({reserved:{bus:0,subway:0},diagnostics:{sql_rows_read:null,sql_rows_written:null}});
      state.adapter.sql.exec=execute;
      expect(state.store.status(START+1)).toMatchObject({diagnostics:{sql_rows_read:null,sql_rows_written:null}});
    }
  });
  it('does not publish partial numeric totals after a SQL execution fails without a cursor',async()=>{
    const state=meteredStorage();await state.store.initialize();const execute=state.adapter.sql.exec;
    state.adapter.sql.exec=()=>{throw new Error('fixture SQL failed without cost counters');};
    expect(()=>state.store.checkBudget('bus',8000,START)).toThrow('fixture SQL failed without cost counters');
    state.adapter.sql.exec=execute;
    expect(state.store.status(START)).toMatchObject({diagnostics:{sql_rows_read:null,sql_rows_written:null}});
  });
});

describe('broker private request and credential boundary',()=>{
  it('selects one stable DO and accepts canonical IDs without caller budgets or URLs',()=>{
    const namespace={idFromName:vi.fn(name=>({name})),get:vi.fn(value=>value)};
    expect(fixedLiveBrokerStub(namespace)).toEqual({name:LIVE_BROKER_OBJECT_NAME});
    expect(namespace.idFromName).toHaveBeenCalledExactlyOnceWith('transit-production');
    expect(parseBrokerRequest(bus())).toEqual(bus());
    for(const value of [{...bus(),dailyLimit:1_000_000},{...bus(),protocol:2},{protocol:1,target:{...bus().target,url:'https://example.test'}},bus('https://invalid'),{protocol:1,target:{kind:'bus',city_code:'12x',route_id:'R'}}])expect(()=>parseBrokerRequest(value)).toThrow('invalid_request');
  });
  it('rejects unknown subway targets, missing keys and malformed private HTTP requests before the source',async()=>{
    const {broker,fetcher,setKey}=setup();
    expect(await broker.query({protocol:1,target:{kind:'subway',subway_id:'1002',name:'2호선'}})).toMatchObject({ok:false,error:{code:'invalid_request'}});
    setKey('sample');expect(await broker.query(bus())).toMatchObject({ok:false,error:{code:'not_configured'}});
    for(const request of [new Request('https://private.test/v1/snapshot'),new Request('https://private.test/v1/snapshot?url=x',{method:'POST',body:'{}'}),new Request('https://private.test/v1/snapshot',{method:'POST',body:'x'.repeat(2049)})]){
      const response=await broker.fetch(request);expect(response.status).toBe(400);expect(response.headers.get('Cache-Control')).toBe('no-store');
    }
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('enforces the source target and never forwards arbitrary source URLs',async()=>{
    for(const address of ['https://elsewhere.invalid/key','https://apis.data.go.kr/1613000/BusLcInfoInqireService/getRouteAcctoBusLcList?serviceKey=wrong']){
      const {broker,fetcher}=setup({source:async context=>{await context.fetcher(address);throw new Error('not reached');}});
      expect(await broker.query(bus())).toMatchObject({ok:false,error:{code:'upstream_invalid'}});expect(fetcher).not.toHaveBeenCalled();
    }
  });
  it('uses HTTPS by default and only the explicit Seoul HTTP setting changes the official transport',async()=>{
    for(const http of [false,true]){
      const seen:string[]=[];
      const {broker}=setup({allowSubwayHttp:http,fetcher:async input=>{seen.push(String(input));return Response.json({errorMessage:{code:'INFO-200',total:0}});}});
      expect(await broker.query(subway)).toMatchObject({ok:true,httpStatus:200});
      expect(seen).toHaveLength(1);expect(new URL(seen[0]).origin).toBe(`${http?'http':'https'}://swopenapi.seoul.go.kr`);
    }
  });
  it('sanitizes thrown source errors and rejects source snapshots containing secret values',async()=>{
    const first=setup({fetcher:async()=>{throw new TypeError(`URL contained ${KEY}`);}});
    const error=await first.broker.query(bus());expect(error).toMatchObject({ok:false,error:{code:'upstream_http'}});expect(JSON.stringify(error)).not.toContain(KEY);
    const second=setup({source:async context=>{
      const value=await fetchCanonicalTransitSnapshot({...context,now:()=>START,env:{DATA_GO_KR_SERVICE_KEY:context.credential}});
      value.snapshot.target.label=KEY;return value;
    }});
    expect(await second.broker.query(bus())).toMatchObject({ok:false,error:{code:'upstream_invalid'}});
    expect(second.db.prepare('SELECT COUNT(*) AS count FROM broker_cache').get()).toMatchObject({count:0});
  });
});

describe('confirmed policy on central snapshot errors',()=>{
  it('returns the actual source policy after target validation and does not share mutable policy objects',async()=>{
    const {broker,fetcher}=setup({dailyLimits:{bus:7,subway:13},credentials:()=>undefined});
    const first=await broker.query(bus());
    expect(first).toMatchObject({ok:false,error:{code:'not_configured'},quota:{daily_limit:7,local_budget:5,budget_limit:5,
      guard:'durable-object',global_enforced:true,scope:'broker-mediated-requests',window:'rolling-24h-conservative'}});
    if(!first.ok&&first.quota)first.quota.local_budget=500;
    expect(await broker.query(bus())).toMatchObject({quota:{daily_limit:7,local_budget:5,budget_limit:5}});
    expect(await broker.query(subway)).toMatchObject({ok:false,error:{code:'not_configured'},quota:{daily_limit:13,local_budget:10,budget_limit:10}});
    expect(fetcher).not.toHaveBeenCalled();
  });
  it('does not claim a confirmed policy for invalid requests, unknown lines, invalid policies or status errors',async()=>{
    const {broker,store,fetcher}=setup();
    for(const input of [{...bus(),protocol:2},{...bus(),quota:{daily_limit:1}},{protocol:1,target:{kind:'subway',subway_id:'1002',name:'2호선'}}]){
      const result=await broker.query(input);expect(result).toMatchObject({ok:false,error:{code:'invalid_request'}});expect(result).not.toHaveProperty('quota');
    }
    const abort=new AbortController();abort.abort();expect(await broker.query(bus(),abort.signal)).not.toHaveProperty('quota');
    const wrongPath=await broker.fetch(new Request('https://private.test/v1/unknown'));
    expect(await wrongPath.json()).not.toHaveProperty('quota');
    const bad=setup({dailyLimits:{bus:0}});const policyError=await bad.broker.query(bus());
    expect(policyError).toMatchObject({ok:false,error:{code:'broker_unavailable'}});expect(policyError).not.toHaveProperty('quota');
    await store.initialize();vi.spyOn(store,'status').mockImplementation(()=>{throw new Error('fixture status failure');});
    const status=await broker.status();expect(status).toMatchObject({ok:false,error:{code:'broker_unavailable'}});expect(status).not.toHaveProperty('quota');
    expect(fetcher).not.toHaveBeenCalled();expect(bad.fetcher).not.toHaveBeenCalled();
  });
  it('preserves the confirmed policy when completion cleanup fails after a reservation',async()=>{
    const {broker,store,db}=setup({dailyLimits:{bus:17}});
    vi.spyOn(store,'release').mockRejectedValueOnce(new Error(`fixture release failure ${KEY}`));
    const result=await broker.query(bus());
    expect(result).toMatchObject({ok:false,error:{code:'broker_unavailable'},quota:{daily_limit:17,local_budget:13,budget_limit:13,guard:'durable-object',global_enforced:true}});
    expect(JSON.stringify(result)).not.toContain(KEY);expect(used(db)).toBe(1);
  });
});

describe('durable budget and restart behavior',()=>{
  it('reserves exactly the actual pages, keeps existing metadata and overwrites the legacy quota claim',async()=>{
    const {broker,db}=setup({fetcher:async input=>Response.json(page(Number(new URL(String(input)).searchParams.get('pageNo')),101))});
    const response=await broker.query(bus());expect(response).toMatchObject({ok:true,httpStatus:200,snapshot:{counts:{accepted:101},quota:{guard:'durable-object',global_enforced:true,scope:'broker-mediated-requests'}}});
    expect(used(db)).toBe(2);
    if(response.ok)expect(response.snapshot.vehicles.every(row=>row.kind==='bus'&&row.city_code==='12'&&row.route_id==='SJB293000077')).toBe(true);
  });
  it('does not send page two with only one reservation remaining, even when the adapter sanitizes the exception',async()=>{
    const fetcher=vi.fn<typeof fetch>(async()=>Response.json(page(1,101)));
    const {broker,db}=setup({dailyLimits:{bus:2},fetcher});
    expect(await broker.query(bus())).toMatchObject({ok:false,error:{code:'quota_exceeded'},quota:{daily_limit:2,local_budget:1,budget_limit:1,guard:'durable-object',global_enforced:true}});
    expect(fetcher).toHaveBeenCalledTimes(1);expect(used(db)).toBe(1);
    expect(db.prepare('SELECT COUNT(*) AS count FROM broker_cache').get()).toMatchObject({count:0});
  });
  it('persists reservations across activations, key changes and policy cache variants',async()=>{
    const first=setup({dailyLimits:{bus:2}});expect(await first.broker.query(bus())).toMatchObject({ok:true});
    const state=storage(first.db),second=setup({dailyLimits:{bus:2}},state);
    second.setKey('fixture-rotated-private-key');
    expect(await second.broker.query(bus('OTHER'))).toMatchObject({ok:false,error:{code:'quota_exceeded'}});
    expect(second.fetcher).not.toHaveBeenCalled();expect(used(first.db)).toBe(1);
    const third=setup({dailyLimits:{bus:1}},storage(first.db));expect(await third.broker.query(bus('THIRD'))).toMatchObject({ok:false,error:{code:'quota_exceeded'}});
  });
  it('has no KST or UTC midnight reset and conservatively expires a rolling hour bucket',async()=>{
    for(const instant of [START,Date.parse('2026-09-19T23:59:00Z')]){
      const {broker,fetcher,setTime,db}=setup({dailyLimits:{bus:2}});setTime(instant);
      expect(await broker.query(bus())).toMatchObject({ok:true});setTime(instant+2*60_000);
      expect(await broker.query(bus('NEXT'))).toMatchObject({ok:false,error:{code:'quota_exceeded'}});
      setTime(instant+24*3_600_000);expect(await broker.query(bus('NEXT'))).toMatchObject({ok:false,error:{code:'quota_exceeded'}});
      setTime(instant+25*3_600_000);expect(await broker.query(bus('NEXT'))).toMatchObject({ok:true});expect(fetcher).toHaveBeenCalledTimes(2);expect(used(db)).toBe(1);
    }
  });
  it('does not release budget through backwards clock movement',async()=>{
    const {broker,setTime,fetcher}=setup({dailyLimits:{bus:2}});await broker.query(bus());setTime(START-48*3_600_000);
    expect(await broker.query(bus('NEXT'))).toMatchObject({ok:false,error:{code:'quota_exceeded'}});expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('commits a reservation before native HTTP and fails closed on durability failure',async()=>{
    const state=storage();let reserved=false;
    state.sync.mockImplementation(async()=>{if(used(state.db)>0){reserved=true;throw new Error('fixture storage unavailable');}});
    const {broker,fetcher}=setup({},state);
    expect(await broker.query(bus())).toMatchObject({ok:false,error:{code:'broker_unavailable'},quota:{daily_limit:10000,local_budget:8000,guard:'durable-object',global_enforced:true}});
    expect(reserved).toBe(true);expect(fetcher).not.toHaveBeenCalled();expect(used(state.db)).toBe(1);
  });
  it('atomically grants only the last available reservation to competing distinct targets',async()=>{
    const {broker,fetcher,db}=setup({dailyLimits:{bus:2}});
    const results=await Promise.all([broker.query(bus('ONE')),broker.query(bus('TWO'))]);
    expect(results.filter(result=>result.ok)).toHaveLength(1);expect(results.filter(result=>!result.ok&&result.error.code==='quota_exceeded')).toHaveLength(1);
    expect(fetcher).toHaveBeenCalledTimes(1);expect(used(db)).toBe(1);
  });
  it('repeated exhausted targets do not consume lease or counter writes',async()=>{
    const {broker,db,fetcher}=setup({dailyLimits:{bus:2}});await broker.query(bus());
    const before=db.prepare('SELECT total_changes() AS changes').get();
    for(let i=0;i<5;i++)expect(await broker.query(bus(`OTHER${i}`))).toMatchObject({ok:false,error:{code:'quota_exceeded'}});
    expect(db.prepare('SELECT total_changes() AS changes').get()).toEqual(before);expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('does not refund attempted HTTP when transport or source validation fails',async()=>{
    const {broker,db}=setup({fetcher:async()=>new Response('invalid-json')});
    const result=await broker.query(bus());expect(result).toMatchObject({ok:true,httpStatus:502,snapshot:{status:'unavailable'}});expect(used(db)).toBe(1);
  });
  it('persists provider quota rejection across routes and restarts',async()=>{
    const first=setup({fetcher:async()=>new Response('',{status:429})});
    expect(await first.broker.query(bus())).toMatchObject({ok:true,httpStatus:429,snapshot:{error:{code:'quota_exceeded'}}});
    const next=setup({},storage(first.db));expect(await next.broker.query(bus('OTHER'))).toMatchObject({ok:false,error:{code:'quota_exceeded'}});expect(next.fetcher).not.toHaveBeenCalled();
  });
  it('blocks the same failed credential across targets and activations without repeated writes',async()=>{
    const first=setup({fetcher:async()=>new Response('',{status:403})});
    expect(await first.broker.query(bus())).toMatchObject({ok:true,httpStatus:503,snapshot:{error:{code:'upstream_auth'}}});
    const next=setup({},storage(first.db));await next.store.initialize();const before=next.db.prepare('SELECT total_changes() AS changes').get();
    for(const route of ['OTHER','THIRD'])expect(await next.broker.query(bus(route))).toMatchObject({ok:false,error:{code:'upstream_auth',retry_after_seconds:300},quota:{daily_limit:10000,local_budget:8000,guard:'durable-object',global_enforced:true}});
    expect(next.db.prepare('SELECT total_changes() AS changes').get()).toEqual(before);expect(next.fetcher).not.toHaveBeenCalled();
    next.setTime(START+301_000);expect(await next.broker.query(bus('OTHER'))).toMatchObject({ok:true});expect(used(first.db)).toBe(2);
  });
  it('allows a new credential after auth failure while preserving usage and bounding old auth generations',async()=>{
    let valid=false;const {broker,setKey,store,db}=setup({fetcher:async()=>valid?Response.json(page()):new Response('',{status:401})});
    expect(await broker.query(bus())).toMatchObject({ok:true,httpStatus:503});setKey('fixture-rotated-private-key');valid=true;
    expect(await broker.query(bus('NEXT'))).toMatchObject({ok:true,httpStatus:200});expect(used(db)).toBe(2);
    for(let i=0;i<8;i++)await store.blockAuthentication('bus',i.toString(16).padStart(64,'0'),START+i+1);
    expect(db.prepare('SELECT COUNT(*) AS count FROM broker_auth_blocks').get()).toMatchObject({count:4});
    expect(JSON.stringify(db.prepare('SELECT * FROM broker_auth_blocks').all())).not.toContain(KEY);
  });
});

describe('global work slots, deduplication and body lifetime',()=>{
  it('merges simultaneous canonical targets and preserves the other subscriber when one aborts',async()=>{
    let finish:()=>void=()=>undefined;let calls=0;
    const fetcher:typeof fetch=async()=>{calls++;await new Promise<void>(resolve=>{finish=resolve;});return Response.json(page(1,1));};
    const {broker}=setup({fetcher});const controller=new AbortController();
    const one=broker.query(bus(),controller.signal),two=broker.query(bus());
    await vi.waitFor(()=>expect(calls).toBe(1));controller.abort();expect(await one).toMatchObject({ok:false,error:{code:'aborted'},quota:{daily_limit:10000,local_budget:8000,guard:'durable-object',global_enforced:true}});
    finish();expect(await two).toMatchObject({ok:true,snapshot:{counts:{accepted:1}}});expect(calls).toBe(1);
  });
  it('holds both global slots until response bodies finish and rejects a third distinct target',async()=>{
    const bodies:ReadableStreamDefaultController<Uint8Array>[]=[];let active=0,peak=0;
    const fetcher:typeof fetch=async()=>{active++;peak=Math.max(peak,active);return new Response(new ReadableStream<Uint8Array>({start(controller){bodies.push(controller);}}));};
    const {broker}=setup({fetcher});const pending=[broker.query(bus('ONE')),broker.query(bus('TWO'))];
    await vi.waitFor(()=>expect(bodies.length).toBe(2));
    expect(await broker.query(bus('THREE'))).toMatchObject({ok:false,error:{code:'busy'},quota:{daily_limit:10000,local_budget:8000,guard:'durable-object',global_enforced:true}});expect(peak).toBe(2);
    for(const body of bodies){body.enqueue(new TextEncoder().encode(JSON.stringify(page())));body.close();active--;}
    expect((await Promise.all(pending)).every(result=>result.ok)).toBe(true);
  });
  it('cleans up a timed-out stream without leaking a slot or a secret-bearing exception',async()=>{
    let cancellations=0;
    const {broker,db}=setup({timeoutMs:10,fetcher:async()=>new Response(new ReadableStream({cancel(){cancellations++;}}))});
    expect(await broker.query(bus())).toMatchObject({ok:false,error:{code:'upstream_timeout'}});expect(cancellations).toBe(1);
    expect(db.prepare('SELECT COUNT(*) AS count FROM broker_leases').get()).toMatchObject({count:0});expect(used(db)).toBe(1);
  });
  it('rejects aborted callers before any reservation',async()=>{
    const {broker,fetcher,db}=setup();const controller=new AbortController();controller.abort();
    expect(await broker.query(bus(),controller.signal)).toMatchObject({ok:false,error:{code:'aborted'}});expect(fetcher).not.toHaveBeenCalled();
    await vi.waitFor(()=>expect(db.prepare('SELECT COUNT(*) AS count FROM broker_usage').get()).toMatchObject({count:0}));
  });
  it('cancels an unfinished private request body without waiting for its timeout',async()=>{
    const {broker,fetcher}=setup();const controller=new AbortController();let canceled=0;
    const request=new Request('https://private.test/v1/snapshot',{method:'POST',body:new ReadableStream({cancel(){canceled++;}}),signal:controller.signal,duplex:'half'} as RequestInit);
    const result=broker.fetch(request);controller.abort();expect((await result).status).toBe(499);expect(canceled).toBe(1);expect(fetcher).not.toHaveBeenCalled();
  });
  it('keeps persisted restart leases closed, and never expires a live activation own job',async()=>{
    const first=storage();await first.store.initialize();
    const a=await first.store.acquire('one','key-one','bus',START),b=await first.store.acquire('two','key-two','subway',START);
    await expect(first.store.acquire('three','key-three','bus',START+60_000)).rejects.toMatchObject({code:'busy'});
    const restarted=storage(first.db);await restarted.store.initialize();
    // The prior unsuccessful acquire advances no committed time because its SQL transaction rolls back.
    await expect(restarted.store.acquire('new','key-new','bus',START+1000)).rejects.toMatchObject({code:'busy'});
    expect(await restarted.store.acquire('new','key-new','bus',START+31_000)).toEqual({job:'new',source:'bus'});
    // A stale activation must not release a lease owned by its successor.
    await first.store.release(a);await first.store.release(b);
    expect(first.db.prepare('SELECT job FROM broker_leases').all()).toEqual([{job:'new'}]);
  });
  it('a failed acquisition commit does not pin an abandoned slot forever in the same activation',async()=>{
    const state=storage();await state.store.initialize();state.sync.mockRejectedValueOnce(new Error('fixture sync failure'));
    await expect(state.store.acquire('orphan','key-orphan','bus',START)).rejects.toThrow();
    const live=await state.store.acquire('live','key-live','bus',START);
    await expect(state.store.acquire('early','key-early','bus',START+1000)).rejects.toBeInstanceOf(BrokerStoreFailure);
    expect(await state.store.acquire('later','key-later','bus',START+31_000)).toEqual({job:'later',source:'bus'});
    await state.store.release(live);
  });
});

describe('bounded latest snapshots',()=>{
  it('reuses a persisted snapshot across activation without refreshing its observation or expiry',async()=>{
    const first=setup();const a=await first.broker.query(bus());const second=setup({},storage(first.db));second.setTime(START+1000);
    const b=await second.broker.query(bus());expect(b).toEqual(a);expect(second.fetcher).not.toHaveBeenCalled();expect(used(first.db)).toBe(1);
  });
  it('a new empty source response replaces the prior vehicles after expiry',async()=>{
    let count=1;const {broker,setTime}=setup({fetcher:async()=>Response.json(page(1,count))});
    expect(await broker.query(bus())).toMatchObject({ok:true,snapshot:{vehicles:[{kind:'bus'}]}});setTime(START+91_000);count=0;
    expect(await broker.query(bus())).toMatchObject({ok:true,snapshot:{status:'empty',vehicles:[]}});
  });
  it('does not reuse a previous credential generation cache while keeping its durable budget',async()=>{
    let count=1;const fetcher=vi.fn<typeof fetch>(async()=>Response.json(page(1,count)));const {broker,setKey,db}=setup({fetcher});
    expect(await broker.query(bus())).toMatchObject({ok:true,snapshot:{counts:{accepted:1}}});setKey('fixture-rotated-private-key');count=0;
    expect(await broker.query(bus())).toMatchObject({ok:true,snapshot:{status:'empty',vehicles:[]}});expect(used(db)).toBe(2);expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('keeps cache hits free of SQL writes and measures the advancing-clock miss without claiming Cloudflare rowsWritten',async()=>{
    const state=storage();await state.store.initialize();let clock=START;
    const options={now:()=>++clock,store:state.store,credentials:()=>KEY,source:(context:Parameters<LiveBrokerOptions['source']>[0])=>fetchCanonicalTransitSnapshot({...context,now:()=>++clock,env:{DATA_GO_KR_SERVICE_KEY:context.credential}}),fetcher:async()=>Response.json(page())};
    const broker=createLiveBroker(options),changes=()=>Number((state.db.prepare('SELECT total_changes() AS changes').get() as {changes:number}).changes);
    const initial=changes();expect(await broker.query(bus())).toMatchObject({ok:true});const afterMiss=changes();
    expect(afterMiss-initial).toBe(6);expect(await broker.query(bus())).toMatchObject({ok:true});expect(changes()).toBe(afterMiss);
  });
  it('caps cache rows, rejects oversized bodies and never stores credentials in its key',async()=>{
    const state=storage();await state.store.initialize();
    for(let i=0;i<70;i++)await state.store.save(`key-${i}`,{body:'{}',bytes:2,expires:START+100_000},START+i);
    expect(state.db.prepare('SELECT COUNT(*) AS count FROM broker_cache').get()).toMatchObject({count:64});
    await expect(state.store.save('too-big',{body:'x'.repeat(512*1024+1),bytes:512*1024+1,expires:START+100_000},START+80)).rejects.toMatchObject({code:'broker_unavailable'});
    const {broker}=setup({},state);await broker.query(bus());
    expect(JSON.stringify(state.db.prepare('SELECT cache_key FROM broker_cache').all())).not.toContain(KEY);
  });
  it('rejects stored corruption rather than falling back to an upstream fetch',async()=>{
    const {broker,db,fetcher}=setup();await broker.query(bus());db.exec("UPDATE broker_cache SET body='invalid-json',bytes=12");
    expect(await broker.query(bus())).toMatchObject({ok:false,error:{code:'broker_unavailable'}});expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('rejects a valid JSON cache row whose vehicle belongs to another official route',async()=>{
    const source=vi.fn<typeof fetch>(async()=>Response.json(page(1,1))),{broker,db}=setup({fetcher:source});await broker.query(bus());
    const row=db.prepare('SELECT body FROM broker_cache').get() as {body:string},value=JSON.parse(row.body);value.snapshot.vehicles[0].route_id='OTHER';
    const body=JSON.stringify(value);db.prepare('UPDATE broker_cache SET body=?,bytes=?').run(body,new TextEncoder().encode(body).byteLength);
    expect(await broker.query(bus())).toMatchObject({ok:false,error:{code:'broker_unavailable'}});expect(source).toHaveBeenCalledTimes(1);
  });
});
