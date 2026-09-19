import {LIVE_BROKER_MAX_SNAPSHOT_BYTES,type BrokerStoredStatus} from '../shared/live-broker';

export interface BrokerSqlCursor {
  toArray():Record<string,unknown>[];
  /** Native Cloudflare getters. Local SQLite fixtures may omit them. */
  readonly rowsRead?:number;
  readonly rowsWritten?:number;
}
/** The SQL subset used by the coordinator; also executable against real SQLite in tests. */
export interface BrokerSqlStorage {
  sql:{exec(query:string,...bindings:(string|number|null)[]):BrokerSqlCursor};
  transactionSync<T>(callback:()=>T):T;
  sync():Promise<void>;
}
export type BrokerSource = 'bus'|'subway';
export type BrokerStoreFailureCode = 'busy'|'quota_exceeded'|'upstream_auth'|'broker_unavailable';
export class BrokerStoreFailure extends Error {
  constructor(readonly code:BrokerStoreFailureCode,readonly retryAfter=30){super(code);}
}
export interface BrokerStoredSnapshot {body:string;expires:number;bytes:number;}
export interface BrokerLease {job:string;source:BrokerSource;}
export interface LiveBrokerStore {
  initialize():Promise<void>;
  /** Read-only after initialize, including for expired cache entries and leases. */
  status(now:number):BrokerStoredStatus;
  cached(key:string,now:number):BrokerStoredSnapshot|null;
  checkBudget(source:BrokerSource,limit:number,now:number):void;
  checkAuthentication(source:BrokerSource,epoch:string,now:number):void;
  acquire(job:string,key:string,source:BrokerSource,now:number):Promise<BrokerLease>;
  reserve(lease:BrokerLease,limit:number,now:number,epoch?:string):Promise<void>;
  block(source:BrokerSource,now:number):Promise<void>;
  blockAuthentication(source:BrokerSource,epoch:string,now:number):Promise<void>;
  save(key:string,value:BrokerStoredSnapshot,now:number):Promise<void>;
  release(lease:BrokerLease):Promise<void>;
}

const HOUR=3_600_000,MAX_ENTRIES=64,MAX_CACHE_BYTES=32*1024*1024;
const LEASE_MS=30_000;
function numeric(value:unknown):number {
  if(typeof value!=='number'||!Number.isSafeInteger(value)||value<0)throw new BrokerStoreFailure('broker_unavailable');
  return value;
}

/** One instance belongs to one DO activation. Its session ID is never used as a quota scope. */
export class SqliteLiveBrokerStore implements LiveBrokerStore {
  private ready:Promise<void>|undefined;
  private readonly session=crypto.randomUUID();
  private readonly sqlActivation=crypto.randomUUID();
  private sqlRowsRead:number|null=0;
  private sqlRowsWritten:number|null=0;
  private readonly ownedJobs=new Set<string>();
  constructor(private readonly storage:BrokerSqlStorage){}
  private exec(query:string,...bindings:(string|number|null)[]):{toArray():Record<string,unknown>[]} {
    let cursor:BrokerSqlCursor,rows:Record<string,unknown>[];
    try {
      cursor=this.storage.sql.exec(query,...bindings);
      // Cloudflare reports final per-query costs only after consuming the cursor.
      // https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/
      // All store statements are single SQL statements and are consumed exactly
      // once, synchronously, including writes whose result rows are ignored.
      rows=cursor.toArray();
    }catch(error){
      // A failed statement can have a cost without returning a usable cursor.
      // Never turn an incomplete activation total into a claimed numeric zero.
      this.sqlRowsRead=null;this.sqlRowsWritten=null;throw error;
    }
    if(this.sqlRowsRead!==null&&this.sqlRowsWritten!==null){
      try {
        const read=cursor.rowsRead,written=cursor.rowsWritten;
        if(typeof read!=='number'||!Number.isSafeInteger(read)||read<0||typeof written!=='number'||!Number.isSafeInteger(written)||written<0
          ||!Number.isSafeInteger(this.sqlRowsRead+read)||!Number.isSafeInteger(this.sqlRowsWritten+written)){
          this.sqlRowsRead=null;this.sqlRowsWritten=null;
        }else{this.sqlRowsRead+=read;this.sqlRowsWritten+=written;}
      }catch{
        // Diagnostics must not break an otherwise successful storage operation.
        this.sqlRowsRead=null;this.sqlRowsWritten=null;
      }
    }
    return {toArray:()=>rows};
  }
  initialize():Promise<void> {
    if(!this.ready)this.ready=(async()=>{
      this.storage.transactionSync(()=>{
        for(const statement of [
          'CREATE TABLE IF NOT EXISTS broker_meta (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, last_time INTEGER NOT NULL)',
          'CREATE TABLE IF NOT EXISTS broker_usage (source TEXT NOT NULL, hour INTEGER NOT NULL, used INTEGER NOT NULL CHECK(used>=0), PRIMARY KEY(source,hour))',
          'CREATE TABLE IF NOT EXISTS broker_blocks (source TEXT PRIMARY KEY, until_time INTEGER NOT NULL)',
          'CREATE TABLE IF NOT EXISTS broker_auth_blocks (source TEXT NOT NULL, epoch TEXT NOT NULL, until_time INTEGER NOT NULL, PRIMARY KEY(source,epoch))',
          'CREATE TABLE IF NOT EXISTS broker_leases (slot INTEGER PRIMARY KEY CHECK(slot IN (0,1)), job TEXT NOT NULL, session TEXT NOT NULL, cache_key TEXT NOT NULL, source TEXT NOT NULL, until_time INTEGER NOT NULL)',
          'CREATE TABLE IF NOT EXISTS broker_cache (cache_key TEXT PRIMARY KEY, body TEXT NOT NULL, bytes INTEGER NOT NULL, expires INTEGER NOT NULL, created INTEGER NOT NULL)',
        ])this.exec(statement);
        this.exec('INSERT OR IGNORE INTO broker_meta(id,version,last_time) VALUES(1,1,0)');
        const meta=this.exec('SELECT version FROM broker_meta WHERE id=1').toArray()[0];
        if(meta?.version!==1)throw new BrokerStoreFailure('broker_unavailable');
      });
      await this.storage.sync();
    })();
    return this.ready;
  }
  private instant(now:number,write=false):number {
    numeric(now);
    const row=this.exec('SELECT last_time FROM broker_meta WHERE id=1').toArray()[0];
    const prior=numeric(row?.last_time),effective=Math.max(prior,now);
    if(write&&effective!==prior)this.exec('UPDATE broker_meta SET last_time=? WHERE id=1',effective);
    return effective;
  }
  private available(source:BrokerSource,limit:number,current:number):void {
    if(!Number.isSafeInteger(limit)||limit<1)throw new BrokerStoreFailure('broker_unavailable');
    const blocked=this.exec('SELECT until_time FROM broker_blocks WHERE source=?',source).toArray()[0];
    if(blocked&&numeric(blocked.until_time)>current)throw new BrokerStoreFailure('quota_exceeded',Math.ceil((numeric(blocked.until_time)-current)/1000));
    const hour=Math.floor(current/HOUR),rows=this.exec('SELECT hour,used FROM broker_usage WHERE source=? AND hour>=?',source,hour-24).toArray();
    if(rows.reduce((sum,row)=>sum+numeric(row.used),0)>=limit){
      const oldest=rows.reduce((first,row)=>Math.min(first,numeric(row.hour)),hour);
      throw new BrokerStoreFailure('quota_exceeded',Math.max(1,Math.ceil(((oldest+25)*HOUR-current)/1000)));
    }
  }
  status(now:number):BrokerStoredStatus {
    // The synchronous SELECTs cannot interleave with an acquire/reserve/release.
    // Do not advance the durable clock or prune expired rows for diagnostics.
    const current=this.instant(now),reserved={bus:0,subway:0};
    const rows=this.exec('SELECT source,SUM(used) AS reserved FROM broker_usage WHERE hour>=? GROUP BY source',Math.floor(current/HOUR)-24).toArray();
    for(const row of rows){
      if(row.source!=='bus'&&row.source!=='subway')throw new BrokerStoreFailure('broker_unavailable');
      reserved[row.source]=numeric(row.reserved);
    }
    const leases=this.exec('SELECT job,session,until_time FROM broker_leases').toArray();
    const active_leases=leases.filter(row=>numeric(row.until_time)>current||row.session===this.session&&this.ownedJobs.has(String(row.job))).length;
    return {reserved,active_leases,diagnostics:{scope:'activation',source:'sql-cursor',activation_id:this.sqlActivation,
      sql_rows_read:this.sqlRowsRead,sql_rows_written:this.sqlRowsWritten,includes_initialization:true,includes_status_reads:true}};
  }
  checkBudget(source:BrokerSource,limit:number,now:number):void {
    // This is an optimization only: reserve repeats the check atomically.
    // Exhausted callers should not consume a lease insert/delete per poll.
    this.available(source,limit,this.instant(now));
  }
  checkAuthentication(source:BrokerSource,epoch:string,now:number):void {
    if(!/^[a-f0-9]{64}$/.test(epoch))throw new BrokerStoreFailure('broker_unavailable');
    const current=this.instant(now),row=this.exec('SELECT until_time FROM broker_auth_blocks WHERE source=? AND epoch=?',source,epoch).toArray()[0];
    if(row&&numeric(row.until_time)>current)throw new BrokerStoreFailure('upstream_auth',Math.ceil((numeric(row.until_time)-current)/1000));
  }
  cached(key:string,now:number):BrokerStoredSnapshot|null {
    const current=this.instant(now),row=this.exec('SELECT body,bytes,expires FROM broker_cache WHERE cache_key=?',key).toArray()[0];
    if(!row)return null;
    const expires=numeric(row.expires),bytes=numeric(row.bytes);
    if(expires<=current){this.exec('DELETE FROM broker_cache WHERE cache_key=?',key);return null;}
    if(typeof row.body!=='string'||bytes>LIVE_BROKER_MAX_SNAPSHOT_BYTES||new TextEncoder().encode(row.body).byteLength!==bytes)throw new BrokerStoreFailure('broker_unavailable');
    return {body:row.body,expires,bytes};
  }
  async acquire(job:string,key:string,source:BrokerSource,now:number):Promise<BrokerLease> {
    this.ownedJobs.add(job);
    try{this.storage.transactionSync(()=>{
      const current=this.instant(now);
      // A live activation must never reclaim its own still-running connection.
      // After restart, old leases remain closed until their original deadline.
      const expired=this.exec('SELECT slot,job,session FROM broker_leases WHERE until_time<=?',current).toArray();
      for(const row of expired)if(row.session!==this.session||!this.ownedJobs.has(String(row.job)))this.exec('DELETE FROM broker_leases WHERE slot=?',numeric(row.slot));
      const rows=this.exec('SELECT slot,cache_key FROM broker_leases').toArray();
      if(rows.length>=2||rows.some(row=>row.cache_key===key))throw new BrokerStoreFailure('busy',5);
      const slot=rows.some(row=>row.slot===0)?1:0;
      this.instant(now,true);
      this.exec('INSERT INTO broker_leases(slot,job,session,cache_key,source,until_time) VALUES(?,?,?,?,?,?)',slot,job,this.session,key,source,current+LEASE_MS);
    });
    await this.storage.sync();
    return {job,source};
    }catch(error){this.ownedJobs.delete(job);throw error;}
  }
  async reserve(lease:BrokerLease,limit:number,now:number,epoch?:string):Promise<void> {
    if(!Number.isSafeInteger(limit)||limit<1)throw new BrokerStoreFailure('broker_unavailable');
    this.storage.transactionSync(()=>{
      const current=this.instant(now),hour=Math.floor(current/HOUR);
      const slot=this.exec('SELECT source,until_time FROM broker_leases WHERE job=? AND session=?',lease.job,this.session).toArray()[0];
      if(!slot||slot.source!==lease.source||numeric(slot.until_time)<=current)throw new BrokerStoreFailure('broker_unavailable');
      if(epoch!==undefined)this.checkAuthentication(lease.source,epoch,current);
      // Current hour plus the preceding 24 buckets conservatively cover every
      // rolling 24h interval, without inventing a provider midnight timezone.
      this.available(lease.source,limit,current);
      this.instant(now,true);
      this.exec('DELETE FROM broker_usage WHERE source=? AND hour<?',lease.source,hour-24);
      this.exec('INSERT INTO broker_usage(source,hour,used) VALUES(?,?,1) ON CONFLICT(source,hour) DO UPDATE SET used=used+1',lease.source,hour);
    });
    await this.storage.sync();
  }
  async block(source:BrokerSource,now:number):Promise<void> {
    this.storage.transactionSync(()=>{
      const until=this.instant(now,true)+24*HOUR;
      this.exec('INSERT INTO broker_blocks(source,until_time) VALUES(?,?) ON CONFLICT(source) DO UPDATE SET until_time=MAX(until_time,excluded.until_time)',source,until);
    });
    await this.storage.sync();
  }
  async blockAuthentication(source:BrokerSource,epoch:string,now:number):Promise<void> {
    if(!/^[a-f0-9]{64}$/.test(epoch))throw new BrokerStoreFailure('broker_unavailable');
    this.storage.transactionSync(()=>{
      const current=this.instant(now,true);
      this.exec('DELETE FROM broker_auth_blocks WHERE until_time<=?',current);
      this.exec('INSERT INTO broker_auth_blocks(source,epoch,until_time) VALUES(?,?,?) ON CONFLICT(source,epoch) DO UPDATE SET until_time=MAX(until_time,excluded.until_time)',source,epoch,current+300_000);
      const epochs=this.exec('SELECT epoch FROM broker_auth_blocks WHERE source=? ORDER BY until_time DESC,epoch ASC',source).toArray();
      for(const row of epochs.slice(4))this.exec('DELETE FROM broker_auth_blocks WHERE source=? AND epoch=?',source,String(row.epoch));
    });
    await this.storage.sync();
  }
  async save(key:string,value:BrokerStoredSnapshot,now:number):Promise<void> {
    if(value.bytes>LIVE_BROKER_MAX_SNAPSHOT_BYTES||value.bytes!==new TextEncoder().encode(value.body).byteLength)throw new BrokerStoreFailure('broker_unavailable');
    this.storage.transactionSync(()=>{
      // Budget time is persisted by acquire/reserve. A cache write cannot spend
      // quota and does not need another write to the clock high-water mark.
      const current=this.instant(now);
      if(numeric(value.expires)<=current)throw new BrokerStoreFailure('broker_unavailable');
      this.exec('DELETE FROM broker_cache WHERE expires<=?',current);
      this.exec('INSERT INTO broker_cache(cache_key,body,bytes,expires,created) VALUES(?,?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET body=excluded.body,bytes=excluded.bytes,expires=excluded.expires,created=excluded.created',key,value.body,value.bytes,value.expires,current);
      const rows=this.exec('SELECT cache_key,bytes FROM broker_cache ORDER BY created DESC,cache_key ASC').toArray();
      let count=0,bytes=0;
      for(const row of rows){
        const size=numeric(row.bytes);
        if(++count>MAX_ENTRIES||bytes+size>MAX_CACHE_BYTES)this.exec('DELETE FROM broker_cache WHERE cache_key=?',String(row.cache_key));
        else bytes+=size;
      }
    });
    await this.storage.sync();
  }
  async release(lease:BrokerLease):Promise<void> {
    try{this.exec('DELETE FROM broker_leases WHERE job=? AND session=?',lease.job,this.session);await this.storage.sync();}
    finally{this.ownedJobs.delete(lease.job);}
  }
}
