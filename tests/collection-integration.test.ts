import {describe,it,expect,vi,afterEach} from 'vitest';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {collectTago} from '../worker/collectors';
import {EMPTY_CATALOG} from '../shared/sources';
import type {Env} from '../worker/index';

// SQLite executes the exact D1 SQL; remote D1 and R2 still need deployment smoke tests.
function fixture(){
  const sql=new DatabaseSync(':memory:');sql.exec(readFileSync('migrations/0001_collection.sql','utf8'));
  function prepare(query:string){
    let values:unknown[]=[];
    const stmt={bind:(...args:unknown[])=>{values=args;return stmt;},first:async()=>sql.prepare(query).get(...values as (string|number|null)[])??null,
      all:async()=>({results:sql.prepare(query).all(...values as (string|number|null)[])}),run:async()=>sql.prepare(query).run(...values as (string|number|null)[])};
    return stmt;
  }
  const db={prepare,batch:async(statements:ReturnType<typeof prepare>[])=>statements.map(s=>s.run())};
  const files=new Map<string,string>([['public/catalog.json',JSON.stringify(EMPTY_CATALOG)],['private/config/tago.json',JSON.stringify({enabled:true,daily_limit:10000,max_routes_per_run:1,routes:[{city_code:'25',route_id:'fixture'}]})]]);
  const data={get:async(key:string)=>files.has(key)?{etag:'fixture',json:async()=>JSON.parse(files.get(key)!)}:null,put:async(key:string,body:string)=>{files.set(key,body);return {etag:'fixture'};}};
  return {sql,files,env:{DB:db,DATA:data,DATA_GO_KR_SERVICE_KEY:'fixture-not-a-key',COLLECTORS_ENABLED:'true'} as unknown as Env};
}
afterEach(()=>vi.unstubAllGlobals());
describe('collection ledger → private replay candidate',()=>{
  it('does not call storage or sources unless collection is explicitly enabled',async()=>{
    const storage=vi.fn();const f={DATA:{get:storage},DB:{prepare:storage}} as unknown as Env;
    expect((await collectTago(f)).status).toBe('disabled');expect(storage).not.toHaveBeenCalled();
  });
  it('stores source-format rows and a private candidate without bypassing the publication audit',async()=>{
    const f=fixture();
    const fetchMock=vi.fn(async()=>Response.json({response:{header:{resultCode:'00'},body:{totalCount:1,items:{item:{vehicleno:'fixture-vehicle',routenm:'test',gpslong:127.43,gpslati:36.33}}}}}));
    vi.stubGlobal('fetch',fetchMock);
    const result=await collectTago(f.env,Date.now());
    expect(result.status).toBe('ok');expect(f.sql.prepare('SELECT COUNT(*) AS n FROM observations').get()?.n).toBe(1);
    expect([...f.files.keys()].some(k=>k.startsWith('private/raw/tago/'))).toBe(true);
    expect(JSON.parse(f.files.get('public/catalog.json')!).release_id).toBe('unpublished');
    const key=[...f.files.keys()].find(k=>k.startsWith('private/staged/candidates/'))!;
    const candidate=JSON.parse(f.files.get(key)!);expect(candidate.status).toBe('awaiting-publication-audit');
    expect(f.files.get(candidate.staged_key)).not.toContain('fixture-vehicle');
    expect([...f.files.keys()].filter(k=>k.startsWith('public/'))).toEqual(['public/catalog.json']);
    expect(f.sql.prepare('SELECT COUNT(*) AS n FROM publication_history').get()?.n).toBe(0);
    expect((await collectTago(f.env,Date.now())).status).toBe('duplicate');expect(fetchMock).toHaveBeenCalledTimes(1);
    f.sql.close();
  });
  it('consumes no network requests once the 80% daily budget is reached',async()=>{
    const f=fixture();
    const day=new Date(Date.now()+9*3600000).toISOString().slice(0,10);
    f.sql.prepare('INSERT INTO daily_quota VALUES(?,?,?)').run('tago',day,8000);
    const network=vi.fn();vi.stubGlobal('fetch',network);
    expect((await collectTago(f.env)).status).toBe('partial');expect(network).not.toHaveBeenCalled();
    expect(f.sql.prepare('SELECT status FROM collection_runs').get()?.status).toBe('partial');f.sql.close();
  });
  it('records authentication errors without publishing an empty success',async()=>{
    const f=fixture();vi.stubGlobal('fetch',async()=>Response.json({response:{header:{resultCode:'30'}}}));
    expect((await collectTago(f.env)).status).toBe('failed');
    expect(JSON.parse(f.files.get('public/catalog.json')!).release_id).toBe('unpublished');
    expect(f.sql.prepare('SELECT accepted_count FROM collection_runs').get()?.accepted_count).toBe(0);f.sql.close();
  });
  it('retries a queued previous hour when the new response has no vehicles',async()=>{
    const f=fixture(),time='2026-09-08T23:59:50.000Z';
    f.sql.prepare('INSERT INTO collection_runs(id,source_id,started_at,status) VALUES(?,?,?,?)').run('previous-run','tago',time,'partial');
    f.sql.prepare('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)').run('fixture-id','tago','fixture-entity','fixture','버스',127.43,36.33,null,time,'fixture-hash','previous-run');
    f.sql.prepare('INSERT INTO pending_publications VALUES(?,?)').run(time.slice(0,13),time);
    vi.stubGlobal('fetch',async()=>Response.json({response:{header:{resultCode:'00'},body:{totalCount:0,items:''}}}));
    expect((await collectTago(f.env)).status).toBe('ok');
    const key=[...f.files.keys()].find(k=>k.startsWith('private/staged/candidates/'))!;
    expect(JSON.parse(f.files.get(key)!).asset.from).toBe(time);
    expect(JSON.parse(f.files.get('public/catalog.json')!).release_id).toBe('unpublished');
    expect(f.sql.prepare('SELECT COUNT(*) AS n FROM pending_publications').get()?.n).toBe(0);f.sql.close();
  });
});
