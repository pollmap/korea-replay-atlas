/** Immutable official-report release. Counts never turn missing/failed queries into zero. */
export type PropertyTradeType = 'sale' | 'rent';
export type PropertyStatus = 'complete' | 'empty' | 'failed' | 'pending' | 'partial';
export interface PropertyAsset { url:string; sha256:string; bytes:number; }
export interface PropertyPeriod { from:string; to:string; latest_complete_month:string; }
export interface PropertyCoverage {
  expected:number; complete:number; empty:number; failed:number; pending:number; partial:number;
  historical_coverage:'current_codes_only_pending_effective_date_crosswalk';
}
export interface PropertySource {
  id:'molit-apt-sale-detail'|'molit-apt-rent'; dataset_id:'15126468'|'15126474';
  label:string; page_url:string; evidence_type:'official_report';
}
export interface PropertyRelease {
  schema_version:1; kind:'property-release'; release_id:string; generated_at:string;
  period:PropertyPeriod; coverage:PropertyCoverage; sources:PropertySource[];
  code_registry:{source_url:string; retrieved_at:string; sha256:string; current_region_count:number};
  /** Small nationwide list; each region points to its own 61-month work/index file. */
  regions:PropertyAsset;
  coordinates:{verified_complexes:number; unresolved_complexes:number; name_only_join:false};
  caveats:string[];
}
export interface RegionMetric {
  lawd_code:string; deal_month:string; trade_type:PropertyTradeType; status:PropertyStatus;
  source_rows:number|null; eligible_rows:number|null; cancelled_rows:number|null;
  invalid_rows:number|null; statistics_excluded_rows:number|null; complex_count:number|null; retrieved_at:string|null;
  /** Distribution of reported unit prices, never a market valuation or same-area comparison. */
  median_price_per_m2_krw:number|null;
  statistic:'reported-row-median-price-per-m2';
  cancellation_policy:'exclude_cancelled_and_unknown'|'source_not_provided';
}
export interface PropertyRegion {
  lawd_code:string; name:string; legal_code:string;
  index:PropertyAsset; coverage:PropertyCoverage;
  latest:{sale:RegionMetric;rent:RegionMetric};
}
export interface PropertyRegions {
  schema_version:1; kind:'property-regions'; release_id:string; regions:PropertyRegion[];
}
export interface PropertyPartition {
  lawd_code:string; deal_month:string; trade_type:PropertyTradeType; status:PropertyStatus;
  source_rows:number|null; eligible_rows:number|null; retrieved_at:string|null;
  /** Same physical month asset may hold sale and rent. Filter trade_type after decoding. */
  transactions:PropertyAsset[]; error_code:string|null;
}
export interface PropertyRegionDetail {
  schema_version:1; kind:'property-region'; release_id:string;
  lawd_code:string; name:string; period:PropertyPeriod; coverage:PropertyCoverage;
  metrics:RegionMetric[]; partitions:PropertyPartition[]; complexes:PropertyAsset|null;
}
export interface VerifiedPropertyPosition {
  longitude:number; latitude:number; crs:'EPSG:4326';
  evidence:{source_id:string; source_record_id:string; source_sha256:string;
    method:'official_complex_id'|'official_building_id'|'verified_parcel_building_join';
    verified_at:string};
}
export interface PropertyComplex {
  id:string; source_complex_id:string; lawd_code:string; name:string;
  legal_dong_code:string|null; legal_dong_name:string|null; lot_number:string|null;
  build_year:number|null; position:VerifiedPropertyPosition|null;
  identity_status:'source_apt_seq'; source_ids:PropertySource['id'][];
  source_input_sha256:string[]; first_contract_month:string; last_contract_month:string;
  observed_name_variants:string[]; address_conflict:boolean;
}
export interface PropertyComplexes {
  schema_version:1; kind:'property-complexes'; release_id:string; lawd_code:string;
  complexes:PropertyComplex[];
}
export interface PropertyTransaction {
  id:string; trade_type:PropertyTradeType; complex_id:string|null; complex_name:string|null;
  lawd_code:string; source_lawd_code:string|null; legal_dong_code:string|null; legal_dong_name:string|null; lot_number:string|null;
  area_m2:string|null; floor:number|null; build_year:number|null; contract_date:string|null;
  price_krw:number|null; deposit_krw:number|null; monthly_rent_krw:number|null;
  previous_deposit_krw:number|null; previous_monthly_rent_krw:number|null;
  contract_term:string|null; contract_type:string|null; renewal_right:string|null;
  registration_date:string|null; reported_at:null; source_updated_at:null;
  cancellation:'not_reported'|'cancelled'|'unknown'|'not_provided'; cancellation_date:string|null;
  quality:'valid'|'incomplete'|'invalid'; issues:{field:string;code:string}[];
  /** Nonstandard lot descriptions remain issues without invalidating valid contract money. */
  statistics_eligible:boolean;
  source_id:PropertySource['id']; source_input_sha256:string; retrieved_at:string;
  evidence_type:'official_report'; observed_at:null;
}
export interface PropertyTransactions {
  schema_version:1; kind:'property-transactions'; release_id:string; lawd_code:string; deal_month:string;
  transactions:PropertyTransaction[];
}

const HASH=/^[a-f0-9]{64}$/;
const RELEASE=/^property-[a-f0-9]{16}$/;
const LAWD=/^[0-9]{5}$/;
const STATUSES=new Set(['complete','empty','failed','pending','partial']);
function obj(v:unknown):v is Record<string,unknown>{return !!v&&typeof v==='object'&&!Array.isArray(v);}
function nat(v:unknown):v is number{return typeof v==='number'&&Number.isSafeInteger(v)&&v>=0;}
function text(v:unknown):v is string{if(typeof v!=='string'||v.length===0||v.length>512)return false;for(let i=0;i<v.length;i++)if(v.charCodeAt(i)<32)return false;return true;}
function stamp(v:unknown):v is string{return typeof v==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(v)&&Number.isFinite(Date.parse(v))&&new Date(v).toISOString().replace('.000Z','Z')===v;}
function day(v:unknown):v is string{return typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v)&&Number.isFinite(Date.parse(v))&&new Date(v).toISOString().slice(0,10)===v;}
function month(v:unknown):v is string{return typeof v==='string'&&/^[0-9]{4}(?:0[1-9]|1[0-2])$/.test(v);}
function code(v:unknown):v is string{return typeof v==='string'&&LAWD.test(v);}
function hash(v:unknown):v is string{return typeof v==='string'&&HASH.test(v);}
function asset(v:unknown):v is PropertyAsset{return obj(v)&&typeof v.url==='string'
  &&/^\/data\/property\/property-[a-f0-9]{16}\/[A-Za-z0-9/_-]+\.json$/.test(v.url)
  &&hash(v.sha256)&&nat(v.bytes)&&v.bytes>0&&v.bytes<=24*1024*1024;}
function coverage(v:unknown):v is PropertyCoverage{return obj(v)
  &&['expected','complete','empty','failed','pending','partial'].every(k=>nat(v[k]))
  &&v.expected===(v.complete as number)+(v.empty as number)+(v.failed as number)+(v.pending as number)+(v.partial as number)
  &&v.historical_coverage==='current_codes_only_pending_effective_date_crosswalk';}
function period(v:unknown):v is PropertyPeriod{return obj(v)&&month(v.from)&&month(v.to)&&month(v.latest_complete_month)
  &&v.from<=v.to&&v.latest_complete_month>=v.from&&v.latest_complete_month<=v.to;}
function base(v:unknown,kind:string):v is Record<string,unknown>{return obj(v)&&v.schema_version===1&&v.kind===kind
  &&typeof v.release_id==='string'&&RELEASE.test(v.release_id);}
function nullableNat(v:unknown):boolean{return v===null||nat(v);}
function nullableText(v:unknown):boolean{return v===null||text(v);}
function metric(v:unknown):v is RegionMetric{
  if(!obj(v)||!code(v.lawd_code)||!month(v.deal_month)||!['sale','rent'].includes(String(v.trade_type))
    ||!STATUSES.has(String(v.status))||!['source_rows','eligible_rows','cancelled_rows','invalid_rows','statistics_excluded_rows','complex_count','median_price_per_m2_krw'].every(k=>nullableNat(v[k]))
    ||!(v.retrieved_at===null||stamp(v.retrieved_at))||v.statistic!=='reported-row-median-price-per-m2'
    ||v.cancellation_policy!==(v.trade_type==='sale'?'exclude_cancelled_and_unknown':'source_not_provided'))return false;
  if(!['complete','empty'].includes(String(v.status)))return v.retrieved_at===null
    &&['source_rows','eligible_rows','cancelled_rows','invalid_rows','statistics_excluded_rows','complex_count','median_price_per_m2_krw'].every(k=>v[k]===null);
  return nat(v.source_rows)&&nat(v.eligible_rows)&&v.eligible_rows<=v.source_rows
    &&stamp(v.retrieved_at)&&nat(v.invalid_rows)&&v.invalid_rows<=v.source_rows
    &&v.statistics_excluded_rows===v.source_rows-v.eligible_rows
    &&nat(v.complex_count)&&v.complex_count<=v.source_rows
    &&(v.trade_type==='rent'?v.cancelled_rows===null&&v.median_price_per_m2_krw===null:nat(v.cancelled_rows)&&v.cancelled_rows<=v.source_rows)
    &&(v.status!=='empty'||v.source_rows===0&&v.eligible_rows===0&&v.median_price_per_m2_krw===null);
}
function fail():never{throw new Error('invalid_property_data');}
function ownAsset(v:PropertyAsset,release:unknown){return v.url.startsWith(`/data/property/${String(release)}/`);}

export function parsePropertyRelease(v:unknown):PropertyRelease{
  if(!base(v,'property-release')||!stamp(v.generated_at)||!period(v.period)||!coverage(v.coverage)
    ||!asset(v.regions)||!ownAsset(v.regions,v.release_id)||!obj(v.code_registry)
    ||v.code_registry.source_url!=='https://www.code.go.kr/stdcodesrch/codeAllDownloadL.do'
    ||!stamp(v.code_registry.retrieved_at)||!hash(v.code_registry.sha256)||!nat(v.code_registry.current_region_count)
    ||!obj(v.coordinates)||!nat(v.coordinates.verified_complexes)||!nat(v.coordinates.unresolved_complexes)
    ||v.coordinates.name_only_join!==false||!Array.isArray(v.caveats)||!v.caveats.every(text)
    ||!Array.isArray(v.sources)||v.sources.length!==2)return fail();
  for(const [i,id] of ['15126468','15126474'].entries()){
    const s=v.sources[i];if(!obj(s)||s.dataset_id!==id||s.page_url!==`https://www.data.go.kr/data/${id}/openapi.do`
      ||s.id!==(i===0?'molit-apt-sale-detail':'molit-apt-rent')||!text(s.label)||s.evidence_type!=='official_report')return fail();
  }
  return v as unknown as PropertyRelease;
}
export function parsePropertyRegions(v:unknown):PropertyRegions{
  if(!base(v,'property-regions')||!Array.isArray(v.regions)||v.regions.length>1000)return fail();
  const seen=new Set<string>();
  for(const r of v.regions){if(!obj(r)||!code(r.lawd_code)||seen.has(r.lawd_code)||!text(r.name)
    ||r.legal_code!==r.lawd_code+'00000'||!asset(r.index)||!ownAsset(r.index,v.release_id)
    ||!coverage(r.coverage)||!obj(r.latest)||!metric(r.latest.sale)||!metric(r.latest.rent)
    ||r.latest.sale.trade_type!=='sale'||r.latest.rent.trade_type!=='rent'||r.latest.sale.deal_month!==r.latest.rent.deal_month
    ||r.latest.sale.lawd_code!==r.lawd_code||r.latest.rent.lawd_code!==r.lawd_code)return fail();seen.add(r.lawd_code);}
  return v as unknown as PropertyRegions;
}
export function parsePropertyRegionDetail(v:unknown):PropertyRegionDetail{
  if(!base(v,'property-region')||!code(v.lawd_code)||!text(v.name)||!period(v.period)||!coverage(v.coverage)
    ||!(v.complexes===null||asset(v.complexes)&&ownAsset(v.complexes,v.release_id))
    ||!Array.isArray(v.metrics)||!v.metrics.every(m=>metric(m)&&m.lawd_code===v.lawd_code)
    ||!Array.isArray(v.partitions)||v.partitions.length>122||v.partitions.length!==v.metrics.length)return fail();
  const seen=new Set<string>(),metrics=new Map<string,RegionMetric>();
  for(const m of v.metrics as RegionMetric[]){const key=`${m.deal_month}/${m.trade_type}`;
    if(metrics.has(key)||m.deal_month<v.period.from||m.deal_month>v.period.to)return fail();metrics.set(key,m);}
  if(v.coverage.expected!==v.partitions.length)return fail();
  for(const p of v.partitions){if(!obj(p)||p.lawd_code!==v.lawd_code||!month(p.deal_month)||!['sale','rent'].includes(String(p.trade_type))
    ||!STATUSES.has(String(p.status))||!nullableNat(p.source_rows)||!nullableNat(p.eligible_rows)
    ||!(p.retrieved_at===null||stamp(p.retrieved_at))||!Array.isArray(p.transactions)
    ||!p.transactions.every(a=>asset(a)&&ownAsset(a,v.release_id))||!(p.error_code===null||typeof p.error_code==='string'&&/^[a-z_]{1,80}$/.test(p.error_code)))return fail();
    const key=`${p.deal_month}/${String(p.trade_type)}`;if(seen.has(key))return fail();seen.add(key);
    const m=metrics.get(key);if(!m||['status','source_rows','eligible_rows','retrieved_at'].some(k=>p[k]!==m[k as keyof RegionMetric]))return fail();
    if(!['complete','empty'].includes(String(p.status))&&(p.source_rows!==null||p.eligible_rows!==null||p.transactions.length!==0))return fail();
  }
  return v as unknown as PropertyRegionDetail;
}
export function parsePropertyTransactions(v:unknown):PropertyTransactions{
  if(!base(v,'property-transactions')||!code(v.lawd_code)||!month(v.deal_month)||!Array.isArray(v.transactions)||v.transactions.length>100_000)return fail();
  const ids=new Set<string>();
  for(const r of v.transactions){if(!obj(r)||typeof r.id!=='string'||!/^molit-(sale|rent):[a-f0-9]{64}:[1-9][0-9]*$/.test(r.id)
    ||ids.has(r.id)||r.lawd_code!==v.lawd_code||!['sale','rent'].includes(String(r.trade_type))
    ||!['price_krw','deposit_krw','monthly_rent_krw','previous_deposit_krw','previous_monthly_rent_krw'].every(k=>r[k]===null||nat(r[k])&&(r[k] as number)%10_000===0)
    ||!(r.area_m2===null||typeof r.area_m2==='string'&&r.area_m2.length<=12&&/^(?:0|[1-9][0-9]*)(?:\.[0-9]{0,5}[1-9])?$/.test(r.area_m2)&&Number(r.area_m2)>0&&Number(r.area_m2)<=10_000)
    ||!['complex_name','legal_dong_name','lot_number','contract_term','contract_type','renewal_right'].every(k=>nullableText(r[k]))
    ||!(r.source_lawd_code===null||code(r.source_lawd_code))
    ||!(r.legal_dong_code===null||typeof r.legal_dong_code==='string'&&/^[0-9]{10}$/.test(r.legal_dong_code))
    ||!(r.floor===null||typeof r.floor==='number'&&Number.isInteger(r.floor)&&r.floor>=-100&&r.floor<=1000)
    ||!(r.build_year===null||nat(r.build_year)&&r.build_year>0&&r.build_year<=9999)
    ||!['contract_date','registration_date','cancellation_date'].every(k=>r[k]===null||day(r[k]))
    ||!['valid','incomplete','invalid'].includes(String(r.quality))||!['not_reported','cancelled','unknown','not_provided'].includes(String(r.cancellation))
    ||r.evidence_type!=='official_report'||r.observed_at!==null||r.reported_at!==null||r.source_updated_at!==null
    ||!hash(r.source_input_sha256)||!stamp(r.retrieved_at)||!Array.isArray(r.issues)||r.issues.length>100
    ||!r.issues.every(i=>obj(i)&&typeof i.field==='string'&&/^[A-Za-z_][A-Za-z0-9_]{0,119}$/.test(i.field)&&['missing','invalid_format','out_of_range','ambiguous','scope_mismatch','unknown_value'].includes(String(i.code)))
    ||!(r.complex_id===null||typeof r.complex_id==='string'&&new RegExp(`^molit-apt:${v.lawd_code}:[A-Za-z0-9_-]{1,64}$`).test(r.complex_id)))return fail();
    if(r.source_id!==(r.trade_type==='sale'?'molit-apt-sale-detail':'molit-apt-rent')||!r.id.startsWith(`molit-${String(r.trade_type)}:`))return fail();
    if(r.trade_type==='rent'&&(r.cancellation!=='not_provided'||r.price_krw!==null))return fail();
    if(r.trade_type==='sale'&&(r.cancellation==='not_provided'||r.deposit_krw!==null||r.monthly_rent_krw!==null))return fail();
    const quality=r.issues.some(i=>i.code!=='missing')?'invalid':r.issues.length?'incomplete':'valid';
    if(r.quality!==quality)return fail();
    if(r.statistics_eligible!==propertyStatisticsEligible(r as unknown as PropertyTransaction))return fail();
    ids.add(r.id);
  }
  return v as unknown as PropertyTransactions;
}
export function parsePropertyComplexes(v:unknown):PropertyComplexes{
  if(!base(v,'property-complexes')||!code(v.lawd_code)||!Array.isArray(v.complexes))return fail();
  const ids=new Set<string>();
  for(const c of v.complexes){if(!obj(c)||typeof c.source_complex_id!=='string'||! /^[A-Za-z0-9_-]{1,64}$/.test(c.source_complex_id)||c.id!==`molit-apt:${v.lawd_code}:${c.source_complex_id}`
    ||ids.has(String(c.id))||c.lawd_code!==v.lawd_code||!text(c.name)||c.identity_status!=='source_apt_seq'
    ||!Array.isArray(c.source_ids)||!c.source_ids.length||!c.source_ids.every(s=>['molit-apt-sale-detail','molit-apt-rent'].includes(String(s)))
    ||!Array.isArray(c.source_input_sha256)||!c.source_input_sha256.length||!c.source_input_sha256.every(hash)
    ||!['legal_dong_name','lot_number'].every(k=>nullableText(c[k]))
    ||!(c.legal_dong_code===null||typeof c.legal_dong_code==='string'&&/^[0-9]{10}$/.test(c.legal_dong_code))
    ||!(c.build_year===null||nat(c.build_year)&&c.build_year>0&&c.build_year<=9999)
    ||!Array.isArray(c.observed_name_variants)||!c.observed_name_variants.length||!c.observed_name_variants.every(text)
    ||!month(c.first_contract_month)||!month(c.last_contract_month)||c.first_contract_month>c.last_contract_month||typeof c.address_conflict!=='boolean')return fail();
    if(c.position!==null){const p=c.position;if(!obj(p)||p.crs!=='EPSG:4326'||typeof p.longitude!=='number'||!Number.isFinite(p.longitude)||p.longitude<124||p.longitude>132.5
      ||typeof p.latitude!=='number'||!Number.isFinite(p.latitude)||p.latitude<32.5||p.latitude>39.5||!obj(p.evidence)||!text(p.evidence.source_id)
      ||!text(p.evidence.source_record_id)||!hash(p.evidence.source_sha256)||!stamp(p.evidence.verified_at)
      ||!['official_complex_id','official_building_id','verified_parcel_building_join'].includes(String(p.evidence.method)))return fail();}
    ids.add(String(c.id));
  }
  return v as unknown as PropertyComplexes;
}

export function propertyStatisticsEligible(r:PropertyTransaction):boolean{
  if(r.trade_type==='sale'?r.cancellation!=='not_reported':r.cancellation!=='not_provided')return false;
  if(r.issues.some(i=>i.code!=='missing'&&i.field!=='jibun'))return false;
  if(r.area_m2===null||r.contract_date===null||r.source_lawd_code!==r.lawd_code)return false;
  return r.trade_type==='sale'?r.price_krw!==null:r.deposit_krw!==null&&r.monthly_rent_krw!==null;
}
export function eligiblePropertyTransactions(rows:readonly PropertyTransaction[]):PropertyTransaction[]{
  return rows.filter(propertyStatisticsEligible);
}

export interface PropertySummaryFilter {complex_id:string;trade_type:PropertyTradeType;area_m2:string;from:string;to:string;}
export function summarizePropertyTransactions(rows:readonly PropertyTransaction[],filter:PropertySummaryFilter){
  if(!/^molit-apt:[0-9]{5}:[A-Za-z0-9_-]{1,64}$/.test(filter.complex_id)
    ||!['sale','rent'].includes(filter.trade_type)||!day(filter.from)||!day(filter.to)||filter.from>filter.to
    ||!/^(?:0|[1-9][0-9]*)(?:\.[0-9]{0,5}[1-9])?$/.test(filter.area_m2)||Number(filter.area_m2)<=0||Number(filter.area_m2)>10_000)throw new Error('invalid_property_summary_filter');
  if(new Set(rows.map(r=>r.id)).size!==rows.length)throw new Error('duplicate_snapshot_records');
  const selected=eligiblePropertyTransactions(rows).filter(r=>r.complex_id===filter.complex_id&&r.trade_type===filter.trade_type
    &&r.area_m2===filter.area_m2&&r.contract_date!==null&&r.contract_date>=filter.from&&r.contract_date<=filter.to);
  const median=(key:'price_krw'|'deposit_krw'|'monthly_rent_krw')=>{
    const values=selected.map(r=>r[key]).filter((v):v is number=>v!==null).sort((a,b)=>a-b),middle=Math.floor(values.length/2);
    return !values.length?null:values.length%2?values[middle]:values[middle-1]+(values[middle]-values[middle-1])/2;
  };
  return {...filter,count:selected.length,median_price_krw:median('price_krw'),median_deposit_krw:median('deposit_krw'),
    median_monthly_rent_krw:median('monthly_rent_krw'),price_unit:'KRW' as const,statistic:'reported-row-median' as const,
    cancellation_policy:filter.trade_type==='sale'?'exclude_cancelled_and_unknown' as const:'source_not_provided' as const};
}

/** Verify immutable descriptor before parsing. Fetch/origin policy is owned by the caller. */
export async function parsePropertyAssetBytes<T>(bytes:Uint8Array,descriptor:PropertyAsset,decode:(v:unknown)=>T):Promise<T>{
  if(!asset(descriptor)||bytes.byteLength!==descriptor.bytes)throw new Error('property_asset_size_mismatch');
  const digest=await crypto.subtle.digest('SHA-256',Uint8Array.from(bytes));
  const hex=Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('');
  if(hex!==descriptor.sha256)throw new Error('property_asset_hash_mismatch');
  return decode(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)) as unknown);
}
