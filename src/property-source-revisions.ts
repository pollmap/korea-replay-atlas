import {parsePropertyTransactions,type PropertyTransaction} from '../shared/property';

export interface RevisionRecord {previous:PropertyTransaction;state:'report_fields_updated'|'not_in_latest_snapshot';candidate_id:string|null;changed_fields:string[];lawd_code:string;deal_month:string;trade_type:'sale'|'rent';candidate_retrieved_at:string;}
export interface RevisionArchive {schema_version:1;kind:'property-public-revision-archive';previous_release:string;property_release_id:string;records:RevisionRecord[];audit:{preserved_ids:number};}
interface Source {url:string;sha256:string;bytes:number;previousAppOrigin:string;}
const sources:Record<string,Source>={
  'property-ceeff63959643461':{url:new URL('./data/property-revisions-ceeff63959643461.json',import.meta.url).href,sha256:'8e0cc98a0e025729f20468339f7fad1907240105598094d60929680ecaa4b2b3',bytes:847285,previousAppOrigin:'https://57769488.korea-replay.pages.dev'},
  // VERIFIED_REVISION_SOURCES
};
export const revisionSource=(release:string)=>sources[release];
const obj=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const bad=():never=>{throw Error('이전 기록의 버전을 확인하지 못했습니다.');};
export function parseRevisionArchive(value:unknown,release:string):RevisionArchive {
  if(!obj(value)||value.schema_version!==1||value.kind!=='property-public-revision-archive'||value.property_release_id!==release||!/^property-[a-f0-9]{16}$/.test(String(value.previous_release))||value.statistics_policy!=='archive_excluded_from_current_statistics'||value.absence_policy!=='not_in_latest_snapshot_is_not_cancellation'||!Array.isArray(value.records)||value.records.length>10_000||!obj(value.audit)||value.audit.preserved_ids!==value.records.length)return bad();
  const seen=new Set<string>(),fields=new Set(['registration_date','cancellation','cancellation_date','statistics_eligible','quality','issues']);
  for(const row of value.records){
    if(!obj(row)||!obj(row.previous)||!/^\d{5}$/.test(String(row.lawd_code))||!/^\d{4}(?:0[1-9]|1[0-2])$/.test(String(row.deal_month))||!['sale','rent'].includes(String(row.trade_type))||!['report_fields_updated','not_in_latest_snapshot'].includes(String(row.state))||!Array.isArray(row.changed_fields)||row.changed_fields.some(field=>typeof field!=='string'||!fields.has(field))||seen.has(String(row.previous.id))||typeof row.candidate_retrieved_at!=='string'||!Number.isFinite(Date.parse(row.candidate_retrieved_at)))return bad();
    const parsed=parsePropertyTransactions({schema_version:1,kind:'property-transactions',release_id:value.previous_release,lawd_code:row.lawd_code,deal_month:row.deal_month,transactions:[row.previous]}).transactions[0];
    if(parsed.trade_type!==row.trade_type||Date.parse(row.candidate_retrieved_at)<=Date.parse(parsed.retrieved_at)||parsed.contract_date&&parsed.contract_date.slice(0,7).replace('-','')!==row.deal_month)return bad();
    if(row.state==='report_fields_updated'?typeof row.candidate_id!=='string'||!new RegExp(`^molit-${row.trade_type}:[a-f0-9]{64}:[1-9][0-9]*$`).test(row.candidate_id):row.candidate_id!==null||row.changed_fields.length)return bad();
    seen.add(parsed.id);
  }
  return value as unknown as RevisionArchive;
}
