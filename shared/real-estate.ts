/** Reported contracts, never current asking prices, valuations or observations. */
export const REAL_ESTATE_SOURCE_URL = 'https://www.data.go.kr/data/15126468/openapi.do';
export const REAL_ESTATE_TRANSFORM = 'apartment-sale-report-v2-opaque-apt-seq';
export type RealEstateTransform = typeof REAL_ESTATE_TRANSFORM | 'apartment-sale-report-v1';
export const REAL_ESTATE_SOURCE_ID = 'molit-apt-sale-detail';

export type RealEstateIssueCode = 'missing'|'invalid_format'|'out_of_range'|'ambiguous'|'scope_mismatch'|'unknown_value';
export interface RealEstateIssue {field:string;code:RealEstateIssueCode;}
export type RealEstateQuality = 'valid'|'incomplete'|'invalid';
export type CancellationStatus = 'not_reported'|'cancelled'|'unknown';
export interface RealEstateRecord {
  /** Snapshot row identity, not a provider transaction ID; identical rows retain occurrences. */
  id:string;
  kind:'apartment-sale-report';
  complex_id:string|null;
  source_complex_id:string|null;
  complex_name:string|null;
  lawd_code:string|null;
  legal_dong_code:string|null;
  legal_dong_name:string|null;
  lot_number:string|null;
  price_krw:number|null;
  source_price_unit:'10,000 KRW';
  /** Canonical decimal square metres, never a rounded pyeong label or float identity. */
  area_m2:string|null;
  floor:number|null;
  build_year:number|null;
  contract_date:string|null;
  /** rgstDate is title registration, not the contract-report submission date. */
  registration_date:string|null;
  reported_at:null;
  source_updated_at:null;
  cancellation:{status:CancellationStatus;reason_date:string|null;source_flag:string|null};
  position:null;
  quality:RealEstateQuality;
  issues:RealEstateIssue[];
  source_fields:Record<string,string>;
  provenance:{source_id:typeof REAL_ESTATE_SOURCE_ID;dataset_id:'15126468';
    evidence_type:'official_report';observed_at:null;retrieved_at:string;input_sha256:string;
    transform_version:RealEstateTransform};
}
export interface RealEstateSourcePage {
  page_no:number;page_size:number;total_count:number;input_sha256:string;input_bytes:number;
  retrieved_at:string;unknown_fields:string[];
}
export interface RealEstatePartition {
  schema_version:1;
  kind:'apartment-sale-report-partition';
  lawd_code:string;
  deal_month:string;
  source:{id:typeof REAL_ESTATE_SOURCE_ID;dataset_id:'15126468';page_url:typeof REAL_ESTATE_SOURCE_URL};
  retrieved_at:string;
  records:RealEstateRecord[];
  audit:{source_rows:number;record_count:number;complete_pages:true;identical_row_occurrences:number;
    quality_counts:Record<RealEstateQuality,number>;cancellation_counts:Record<CancellationStatus,number>;
    issue_counts:(RealEstateIssue&{count:number})[];pages:RealEstateSourcePage[]};
}
export interface RealEstateManifest {
  schema_version:1;
  kind:'apartment-sale-report-manifest';
  source:RealEstatePartition['source'];
  transform_version:RealEstateTransform;
  partitions:{path:string;sha256:string;bytes:number;record_count:number}[];
}
export interface RealEstateSummaryFilter {complex_id:string;area_m2:string;date_from:string;date_to:string;}
export interface RealEstateSummary extends RealEstateSummaryFilter {
  count:number;median_price_krw:number|null;price_unit:'KRW';cancellation_policy:'exclude';
  statistic:'reported-row-median';
}

const sourceFields=new Set(('sggCd umdCd landCd bonbun bubun roadNm roadNmSggCd roadNmCd roadNmSeq '
  +'roadNmbCd roadNmBonbun roadNmBubun umdNm aptNm jibun excluUseAr dealYear dealMonth dealDay '
  +'dealAmount floor buildYear aptSeq cdealType cdealDay dealingGbn estateAgentSggNm rgstDate '
  +'aptDong slerGbn buyerGbn landLeaseholdGbn').split(' '));
const issueCodes=new Set<RealEstateIssueCode>(['missing','invalid_format','out_of_range','ambiguous','scope_mismatch','unknown_value']);
const hash=/^[a-f0-9]{64}$/;
const complexId=/^molit-apt:[0-9]{5}:[A-Za-z0-9_-]{1,64}$/;
function object(v:unknown):v is Record<string,unknown>{return !!v&&typeof v==='object'&&!Array.isArray(v);}
function integer(v:unknown,min:number,max:number):v is number{return typeof v==='number'&&Number.isSafeInteger(v)&&v>=min&&v<=max;}
function controlFree(v:string):boolean{for(let i=0;i<v.length;i++)if(v.charCodeAt(i)<32)return false;return true;}
function text(v:unknown,max=120):v is string{return typeof v==='string'&&v.length>0&&v.length<=max&&controlFree(v);}
function dateString(v:unknown):v is string{
  if(typeof v!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(v)||v.startsWith('0000'))return false;
  const time=Date.parse(`${v}T00:00:00Z`);
  return Number.isFinite(time)&&new Date(time).toISOString().slice(0,10)===v;
}
function instant(v:unknown):v is string{
  if(typeof v!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(v))return false;
  const time=Date.parse(v);
  return Number.isFinite(time)&&new Date(time).toISOString().replace('.000Z','Z')===v&&dateString(v.slice(0,10));
}
function nullable<T>(v:unknown,predicate:(v:unknown)=>v is T):v is T|null{return v===null||predicate(v);}
function area(v:unknown):v is string{
  if(typeof v!=='string'||!/^(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?$/.test(v))return false;
  const [whole,fraction='']=v.split('.');
  if(whole.length>5||fraction.length>6)return false;
  const units=BigInt(whole)*1_000_000n+BigInt(fraction.padEnd(6,'0'));
  return units>0n&&units<=10_000_000_000n;
}
function issue(v:unknown):v is RealEstateIssue{
  return object(v)&&typeof v.field==='string'&&(sourceFields.has(v.field)||v.field==='contract_date')
    &&issueCodes.has(v.code as RealEstateIssueCode);
}
function qualityFor(issues:RealEstateIssue[]):RealEstateQuality{
  return issues.some(i=>i.code!=='missing')?'invalid':issues.length?'incomplete':'valid';
}
function record(v:unknown):v is RealEstateRecord{
  if(!object(v)||typeof v.id!=='string'||!/^molit-sale:[a-f0-9]{64}:[1-9][0-9]{0,5}$/.test(v.id)
    ||v.kind!=='apartment-sale-report'||!nullable(v.complex_id,(s):s is string=>typeof s==='string'&&complexId.test(s))
    ||!nullable(v.source_complex_id,(s):s is string=>typeof s==='string'&&/^[A-Za-z0-9_-]{1,64}$/.test(s))
    ||!nullable(v.complex_name,text)||!nullable(v.legal_dong_name,text)
    ||!nullable(v.lawd_code,(s):s is string=>typeof s==='string'&&/^[0-9]{5}$/.test(s))
    ||!nullable(v.legal_dong_code,(s):s is string=>typeof s==='string'&&/^[0-9]{10}$/.test(s))
    ||!nullable(v.lot_number,(s):s is string=>typeof s==='string'&&/^(?:산\s*)?[0-9]{1,5}(?:-[0-9]{1,5})?$/.test(s))
    ||!nullable(v.price_krw,(n):n is number=>integer(n,1,Number.MAX_SAFE_INTEGER)&&n%10_000===0)
    ||v.source_price_unit!=='10,000 KRW'||!nullable(v.area_m2,area)
    ||!nullable(v.floor,(n):n is number=>integer(n,-100,1000))
    ||!nullable(v.build_year,(n):n is number=>integer(n,1,9999))
    ||!nullable(v.contract_date,dateString)||!nullable(v.registration_date,dateString)
    ||v.reported_at!==null||v.source_updated_at!==null||v.position!==null
    ||!Array.isArray(v.issues)||v.issues.length>100||!v.issues.every(issue)
    ||v.quality!==qualityFor(v.issues)||!object(v.source_fields)
    ||!Object.entries(v.source_fields).every(([k,s])=>sourceFields.has(k)&&typeof s==='string'&&s.length<=512&&controlFree(s)))return false;
  const c=v.cancellation,p=v.provenance;
  if(!object(c)||!['not_reported','cancelled','unknown'].includes(String(c.status))
    ||!nullable(c.reason_date,dateString)||!nullable(c.source_flag,(s):s is string=>text(s,512))
    ||!object(p)||p.source_id!==REAL_ESTATE_SOURCE_ID||p.dataset_id!=='15126468'
    ||p.evidence_type!=='official_report'||p.observed_at!==null||!instant(p.retrieved_at)
    ||typeof p.input_sha256!=='string'||!hash.test(p.input_sha256)
    ||![REAL_ESTATE_TRANSFORM,'apartment-sale-report-v1'].includes(String(p.transform_version)))return false;
  if(v.complex_id!==null&&v.complex_id!==`molit-apt:${v.lawd_code}:${v.source_complex_id}`)return false;
  if(c.status==='not_reported'&&(c.source_flag!==null||c.reason_date!==null))return false;
  return true;
}

/** Structural decoder; source SHA is provenance, not proof that a remote payload is authentic. */
export function parseRealEstatePartition(value:unknown):RealEstatePartition {
  const fail=():never=>{throw new Error('invalid_real_estate_partition');};
  if(!object(value)||value.schema_version!==1||value.kind!=='apartment-sale-report-partition'
    ||typeof value.lawd_code!=='string'||!/^[0-9]{5}$/.test(value.lawd_code)||value.lawd_code==='00000'
    ||typeof value.deal_month!=='string'||!/^[0-9]{6}$/.test(value.deal_month)
    ||!dateString(`${value.deal_month.slice(0,4)}-${value.deal_month.slice(4)}-01`)
    ||!instant(value.retrieved_at)||!object(value.source)||value.source.id!==REAL_ESTATE_SOURCE_ID
    ||value.source.dataset_id!=='15126468'||value.source.page_url!==REAL_ESTATE_SOURCE_URL
    ||!Array.isArray(value.records)||value.records.length>100_000||!value.records.every(record)||!object(value.audit))return fail();
  const a=value.audit,records=value.records;
  if(a.complete_pages!==true||a.source_rows!==records.length||a.record_count!==records.length
    ||!integer(a.identical_row_occurrences,0,records.length)||!object(a.quality_counts)||!object(a.cancellation_counts)
    ||!Array.isArray(a.issue_counts)||!a.issue_counts.every(i=>issue(i)&&object(i)&&integer(i.count,1,100_000))
    ||!Array.isArray(a.pages)||a.pages.length<1||a.pages.length>1000)return fail();
  if(new Set(records.map(r=>r.id)).size!==records.length)return fail();
  const fingerprints=new Map<string,Set<number>>(),issueCounts=new Map<string,number>();
  for(const r of records){
    const [,fingerprint,occurrence]=r.id.split(':');
    if(!fingerprints.has(fingerprint))fingerprints.set(fingerprint,new Set());
    fingerprints.get(fingerprint)!.add(Number(occurrence));
    const rowIssues=new Set<string>();
    for(const i of r.issues){
      const key=`${i.field}:${i.code}`;
      if(rowIssues.has(key))return fail();
      rowIssues.add(key);issueCounts.set(key,(issueCounts.get(key)??0)+1);
    }
  }
  if(a.identical_row_occurrences!==records.length-fingerprints.size)return fail();
  for(const occurrences of fingerprints.values()){
    if(!occurrences.has(1))return fail();
    for(const occurrence of occurrences)if(occurrence>occurrences.size)return fail();
  }
  const reportedIssueCounts=new Map<string,number>();
  for(const i of a.issue_counts){
    const key=`${i.field}:${i.code}`;
    if(reportedIssueCounts.has(key))return fail();
    reportedIssueCounts.set(key,i.count);
  }
  if(reportedIssueCounts.size!==issueCounts.size
    ||[...issueCounts].some(([key,count])=>reportedIssueCounts.get(key)!==count))return fail();
  for(const status of ['valid','incomplete','invalid'] as const){
    if(a.quality_counts[status]!==records.filter(r=>r.quality===status).length)return fail();
  }
  for(const status of ['not_reported','cancelled','unknown'] as const){
    if(a.cancellation_counts[status]!==records.filter(r=>r.cancellation.status===status).length)return fail();
  }
  const pages=new Map<string,RealEstateSourcePage>();
  let pageSize:number|undefined;
  for(let i=0;i<a.pages.length;i++){
    const p=a.pages[i];
    if(!object(p)||p.page_no!==i+1||!integer(p.page_size,1,10_000)||p.total_count!==records.length
      ||!integer(p.input_bytes,1,8*1024*1024)||typeof p.input_sha256!=='string'||!hash.test(p.input_sha256)
      ||!instant(p.retrieved_at)||!Array.isArray(p.unknown_fields)
      ||!p.unknown_fields.every(f=>typeof f==='string'&&/^[A-Za-z][A-Za-z0-9_]{0,119}$/.test(f))
      ||pageSize!==undefined&&pageSize!==p.page_size)return fail();
    pageSize=p.page_size;
    if(pages.has(p.input_sha256))return fail();
    pages.set(p.input_sha256,p as unknown as RealEstateSourcePage);
  }
  if(a.pages.length!==Math.max(1,Math.ceil(records.length/pageSize!)))return fail();
  const times=[...pages.values()].map(p=>p.retrieved_at).sort();
  if(times.at(-1)!==value.retrieved_at||Date.parse(times.at(-1)!)-Date.parse(times[0])>900_000)return fail();
  for(const r of records){
    const page=pages.get(r.provenance.input_sha256);
    if(!page||page.retrieved_at!==r.provenance.retrieved_at)return fail();
    if(r.quality!=='invalid'&&(r.lawd_code!==null&&r.lawd_code!==value.lawd_code
      ||r.contract_date!==null&&r.contract_date.replaceAll('-','').slice(0,6)!==value.deal_month))return fail();
  }
  return value as unknown as RealEstatePartition;
}

/** Default display excludes cancellation, unrecognized cancellation flags and invalid rows. */
export function activeRealEstateRecords(records:readonly RealEstateRecord[]):RealEstateRecord[]{
  return records.filter(r=>r.cancellation.status==='not_reported'&&r.quality!=='invalid');
}

/** Exact declared area and inclusive contract dates. Never mix differently sized apartments. */
export function summarizeTransactions(records:readonly RealEstateRecord[],filter:RealEstateSummaryFilter):RealEstateSummary {
  if(!complexId.test(filter.complex_id)||!area(filter.area_m2)||!dateString(filter.date_from)
    ||!dateString(filter.date_to)||filter.date_from>filter.date_to)throw new Error('invalid_summary_filter');
  if(new Set(records.map(r=>r.id)).size!==records.length)throw new Error('duplicate_snapshot_records');
  const values=activeRealEstateRecords(records).filter(r=>r.complex_id===filter.complex_id
    &&r.area_m2===filter.area_m2&&r.contract_date!==null&&r.contract_date>=filter.date_from
    &&r.contract_date<=filter.date_to&&r.price_krw!==null).map(r=>r.price_krw!).sort((a,b)=>a-b);
  const mid=Math.floor(values.length/2);
  const median=!values.length?null:values.length%2?values[mid]:values[mid-1]+(values[mid]-values[mid-1])/2;
  return {...filter,count:values.length,median_price_krw:median,price_unit:'KRW',
    cancellation_policy:'exclude',statistic:'reported-row-median'};
}
