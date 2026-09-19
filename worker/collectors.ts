import type {Env} from './index';
import type {ReplayChunk,Track} from '../shared/contracts';
import {kstDate} from '../shared/time';

interface Route {city_code:string;route_id:string;label?:string;}
interface TagoConfig {enabled:boolean;daily_limit:number;max_routes_per_run:number;routes:Route[];}
interface Observation {id:string;entity_id:string;route_id:string;label:string;lon:number;lat:number;observed_at:null;retrieved_at:string;input_hash:string;}
type Item=Record<string,unknown>;
export async function sha256(value:string) {return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)))].map(b=>b.toString(16).padStart(2,'0')).join('');}
export function parseTago(value:unknown):{items:Item[];total:number} {
  const v=value as {response?:{header?:{resultCode?:unknown};body?:{items?:{item?:Item|Item[]}|string;totalCount?:unknown}}};
  if(String(v?.response?.header?.resultCode)!=='00')throw new Error('TAGO 응답 결과 코드 오류');
  const body=v.response?.body,total=Number(body?.totalCount);
  if(!Number.isSafeInteger(total)||total<0||total>100000)throw new Error('TAGO 전체 건수 오류');
  const raw=typeof body?.items==='object'?body.items?.item:undefined;
  const items=raw?(Array.isArray(raw)?raw:[raw]):[];
  if(items.some(i=>!i||typeof i!=='object'))throw new Error('TAGO 항목 형식 오류');
  return {items,total};
}
export async function normalizeTago(items:Item[],route:Route,retrievedAt:string,inputHash:string) {
  const observations:Observation[]=[];let rejected=0;
  for(const item of items){
    const lon=Number(item.gpslong),lat=Number(item.gpslati),vehicle=String(item.vehicleno??'').trim();
    if(!vehicle||!Number.isFinite(lon)||!Number.isFinite(lat)||lon<124||lon>132||lat<33||lat>39){rejected++;continue;}
    // Stable identifiers do not expose vehicle registration numbers in public files.
    const entityId=(await sha256(`${route.city_code}:${route.route_id}:${vehicle}`)).slice(0,24);
    observations.push({id:await sha256(`${entityId}:${retrievedAt}:${lon}:${lat}`),entity_id:entityId,route_id:route.route_id,label:route.label??`${String(item.routenm??'버스')}번 버스`,lon,lat,observed_at:null,retrieved_at:retrievedAt,input_hash:inputHash});
  }
  return {observations,rejected};
}
function safeConfig(value:unknown):TagoConfig {
  const c=value as TagoConfig;
  if(!c||typeof c.enabled!=='boolean'||!Number.isSafeInteger(c.daily_limit)||c.daily_limit<1||c.daily_limit>1000000||!Number.isSafeInteger(c.max_routes_per_run)||c.max_routes_per_run<1||c.max_routes_per_run>5||!Array.isArray(c.routes)||c.routes.length>10000||c.routes.some(r=>!/^\d+$/.test(r.city_code)||!/^[A-Za-z0-9_-]{1,60}$/.test(r.route_id)))throw new Error('TAGO 수집 설정 오류');
  return c;
}
async function reserve(db:D1Database,day:string,limit:number) {
  const row=await db.prepare('INSERT INTO daily_quota(source_id,day,used) VALUES(?,?,1) ON CONFLICT(source_id,day) DO UPDATE SET used=used+1 WHERE used < ? RETURNING used').bind('tago',day,Math.floor(limit*.8)).first();
  return Boolean(row);
}
interface SavedObservation {entity_id:string;route_id:string;label:string;lon:number;lat:number;observed_at:string|null;retrieved_at:string;input_hash:string;}
export function replayChunk(rows:SavedObservation[]):ReplayChunk {
  const byEntity=new Map<string,SavedObservation[]>();
  for(const row of rows){const items=byEntity.get(row.entity_id)??[];items.push(row);byEntity.set(row.entity_id,items);}
  const tracks:Track[]=[];
  for(const [id,values] of byEntity){
    values.sort((a,b)=>Date.parse(a.retrieved_at)-Date.parse(b.retrieved_at));
    const unique=[...new Map(values.map(v=>[v.retrieved_at,v])).values()],first=unique[0];
    tracks.push({id,label:first.label,layer:'bus',position_evidence:'calculation',max_gap_seconds:180,
      provenance:{source_id:'tago',source_record_id:first.route_id,dataset_version:'BusLcInfoInqireService-v1',observed_at:null,retrieved_at:first.retrieved_at,evidence_type:'observation',input_hash:first.input_hash,transform_version:'korea-replay-track-1'},
      points:unique.map(v=>({time:v.retrieved_at,lon:v.lon,lat:v.lat})),
    });
  }
  return {schema_version:1,tracks};
}
async function stageHour(env:Env,hour:string) {
  const start=`${hour}:00:00.000Z`,end=new Date(Date.parse(start)+3600000).toISOString();
  const rows:SavedObservation[]=[];
  for(let offset=0;offset<100000;offset+=1000){
    const result=await env.DB!.prepare('SELECT entity_id,route_id,label,lon,lat,observed_at,retrieved_at,input_hash FROM observations WHERE retrieved_at >= ? AND retrieved_at < ? ORDER BY retrieved_at,id LIMIT 1000 OFFSET ?').bind(start,end,offset).all<SavedObservation>();
    rows.push(...result.results);if(result.results.length<1000)break;
    if(offset===99000)throw new Error('시간별 공개 한도 초과: 추가 분할 필요');
  }
  if(!rows.length)return;
  const body=JSON.stringify(replayChunk(rows)),hash=await sha256(body);
  const key=`replay/tago/${hour.replace(/[:]/g,'-')}-${hash.slice(0,16)}.json`;
  await env.DATA!.put(`private/staged/${key}`,body,{httpMetadata:{contentType:'application/json'},customMetadata:{sha256:hash}});
  const id=`tago-${hour}`;
  const bbox=rows.reduce<[number,number,number,number]>((b,r)=>[Math.min(b[0],r.lon),Math.min(b[1],r.lat),Math.max(b[2],r.lon),Math.max(b[3],r.lat)],[180,90,-180,-90]);
  const candidate={schema_version:1,status:'awaiting-publication-audit',staged_key:`private/staged/${key}`,
    asset:{id,layer:'bus',format:'replay',url:`/data/${key}`,bbox,source_id:'tago',version:hour,count:rows.length,sha256:hash,from:rows[0].retrieved_at,to:rows.at(-1)!.retrieved_at,cadence_seconds:60}};
  // Collection credentials can write private candidates only. A complete,
  // audited release must pass the ordinary publication pipeline before any
  // public pointer changes; no scheduler can bypass that gate.
  await env.DATA!.put(`private/staged/candidates/${id}-${hash.slice(0,16)}.json`,JSON.stringify(candidate),{httpMetadata:{contentType:'application/json'}});
}
export async function collectTago(env:Env,scheduledTime=Date.now()) {
  if(env.COLLECTORS_ENABLED!=='true')return {status:'disabled'};
  if(!env.DB||!env.DATA)return {status:'unconfigured'};
  if(!env.DATA_GO_KR_SERVICE_KEY)return {status:'unconfigured',reason:'DATA_GO_KR_SERVICE_KEY 미설정'};
  const minute=Math.floor(scheduledTime/60000),id=`tago-${minute}`,started=new Date().toISOString(),deadline=Date.now()+45000;
  const claimed=await env.DB.prepare('INSERT OR IGNORE INTO collection_runs(id,source_id,started_at,status) VALUES(?,?,?,?) RETURNING id').bind(id,'tago',started,'running').first();
  if(!claimed)return {status:'duplicate'};
  let requests=0,accepted=0,rejected=0,status='ok',reason:string|null=null;
  const lock=await env.DB.prepare('INSERT INTO operation_locks(name,owner,expires_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at WHERE expires_at < ? RETURNING owner').bind('tago',id,Date.now()+180000,Date.now()).first();
  try{
    if(!lock){status='skipped';reason='이전 수집 실행 중';return {status};}
    if(!env.DATA_GO_KR_SERVICE_KEY){status='skipped';reason='DATA_GO_KR_SERVICE_KEY 미설정';return {status};}
    const configObject=await env.DATA.get('private/config/tago.json');
    if(!configObject){status='skipped';reason='TAGO 노선 설정 미등록';return {status};}
    const config=safeConfig(await configObject.json());
    if(!config.enabled||!config.routes.length){status='skipped';reason='TAGO 수집 비활성';return {status};}
    const size=Math.min(config.max_routes_per_run,config.routes.length),start=(minute*size)%config.routes.length;
    for(let index=0;index<size;index++){
      const route=config.routes[(start+index)%config.routes.length];
      for(let page=1;page<=20;page++){
        if(Date.now()>deadline){status='partial';reason='실행 시간 예산 소진';break;}
        if(!await reserve(env.DB,kstDate(Date.now()),config.daily_limit)){status='partial';reason='일일 호출량 80% 예산 소진';break;}
        requests++;
        const url=new URL('https://apis.data.go.kr/1613000/BusLcInfoInqireService/getRouteAcctoBusLcList');
        url.search=new URLSearchParams({serviceKey:env.DATA_GO_KR_SERVICE_KEY,cityCode:route.city_code,routeId:route.route_id,pageNo:String(page),numOfRows:'100',_type:'json'}).toString();
        let response:Response;
        try{response=await fetch(url,{signal:AbortSignal.timeout(15000)});}catch{throw new Error('TAGO 네트워크 응답 실패');}
        if(!response.ok)throw new Error(`TAGO HTTP ${response.status}`);
        const text=await response.text();if(text.length>2*1024*1024)throw new Error('TAGO 응답 크기 초과');
        let body:unknown;try{body=JSON.parse(text);}catch{throw new Error('TAGO JSON 응답 오류');}
        const parsed=parseTago(body),retrievedAt=new Date().toISOString(),hash=await sha256(text);
        await env.DATA.put(`private/raw/tago/${id}/${route.route_id}-${page}-${hash.slice(0,12)}.json`,text,{httpMetadata:{contentType:'application/json'},customMetadata:{sha256:hash,retrieved_at:retrievedAt}});
        const normalized=await normalizeTago(parsed.items,route,retrievedAt,hash);rejected+=normalized.rejected;
        for(let offset=0;offset<normalized.observations.length;offset+=50){
          const batch=normalized.observations.slice(offset,offset+50);
          await env.DB.batch([
            ...batch.map(o=>env.DB!.prepare('INSERT OR IGNORE INTO observations(id,source_id,entity_id,route_id,label,lon,lat,observed_at,retrieved_at,input_hash,run_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)').bind(o.id,'tago',o.entity_id,o.route_id,o.label,o.lon,o.lat,null,o.retrieved_at,o.input_hash,id)),
            env.DB.prepare('INSERT INTO pending_publications(hour,updated_at) VALUES(?,?) ON CONFLICT(hour) DO UPDATE SET updated_at=excluded.updated_at').bind(retrievedAt.slice(0,13),retrievedAt),
          ]);accepted+=batch.length;
        }
        if(page*100>=parsed.total)break;
        if(!parsed.items.length||page===20)throw new Error('TAGO 페이지 누락 또는 상한 초과');
      }
    }
    // Retry unfinished private staging, including across retrieval-hour changes.
    const pending=await env.DB.prepare('SELECT hour FROM pending_publications ORDER BY hour LIMIT 2').all<{hour:string}>();
    for(const {hour} of pending.results){
      await stageHour(env,hour);
      await env.DB.prepare('DELETE FROM pending_publications WHERE hour=?').bind(hour).run();
    }
  }catch(error){status=accepted?'partial':'failed';reason=error instanceof Error?error.message:'수집 실패';}
  finally{
    await env.DB.prepare('UPDATE collection_runs SET finished_at=?,status=?,request_count=?,accepted_count=?,rejected_count=?,reason=?,raw_prefix=? WHERE id=?').bind(new Date().toISOString(),status,requests,accepted,rejected,reason,`private/raw/tago/${id}/`,id).run();
    if(lock)await env.DB.prepare('DELETE FROM operation_locks WHERE name=? AND owner=?').bind('tago',id).run();
  }
  return {status,requests,accepted,rejected,reason};
}
