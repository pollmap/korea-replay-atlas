import {areaMatches} from '../shared/property-area';
import {validTransport,type AssetTransport} from '../shared/asset-transport';
import {fetchPinnedJson,type PinnedJson} from './atlas-client';
import type {ComplexPriceSummary,SummaryPartition} from './property-map-prices';
import sources from './data/property-summary-sources.json';

export const hasPropertySummaries=(release:string)=>sources.some(source=>source.release_id===release);
interface SummarySource {release_id:string;origin:string;manifest:PinnedJson;}
interface SummaryRef {transport?:AssetTransport;url:string;sha256:string;bytes:number;deal_month?:string;lawd_code?:string;from_month?:string;to_month?:string;}
const HASH=/^[a-f0-9]{64}$/,MONTH=/^\d{4}(?:0[1-9]|1[0-2])$/;
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
const fail=():never=>{throw new Error('가격 요약 자료의 버전·내용을 확인하지 못했습니다.');};
function ref(value:unknown):SummaryRef {
  if(!object(value)||typeof value.url!=='string'||!/^\/data\/property-summary\/summary-[a-f0-9]{16}\/(?:regions\/\d{5}\.json|rows\/\d{5}\/\d{6}-\d{3}\.json|month-packs\/\d{5}\/\d{4}\.json|complexes\/\d{5}\/\d{4}\.json)$/.test(value.url)||typeof value.sha256!=='string'||!HASH.test(value.sha256)||typeof value.bytes!=='number'||!Number.isSafeInteger(value.bytes)||value.bytes<=0||value.bytes>4*1024*1024)return fail();
  if(value.transport!==undefined&&!validTransport(value.transport))return fail();
  return value as unknown as SummaryRef;
}
export function parseComplexPriceSummaries(value:unknown,release:string,region:string,month:string|null):ComplexPriceSummary[] {
  if(!object(value)||value.schema_version!==1||(month===null?value.kind!=='property-complex-summary-pack':value.kind!=='property-complex-summaries')||value.property_release_id!==release||value.lawd_code!==region||!Array.isArray(value.rows)||value.rows.length>40_000)return fail();
  if(month===null&&(!MONTH.test(String(value.from_month))||!MONTH.test(String(value.to_month))||String(value.from_month)>String(value.to_month)))return fail();
  const seen=new Set<string>();
  return value.rows.map(raw=>{
    if(!object(raw)||typeof raw.deal_month!=='string'||!MONTH.test(raw.deal_month)||month!==null&&raw.deal_month!==month||typeof raw.complex_id!=='string'||!new RegExp(`^molit-apt:${region}:[A-Za-z0-9_-]{1,64}$`).test(raw.complex_id)||!['sale','rent'].includes(String(raw.trade_type))||!(raw.trade_type==='sale'?raw.rent_kind==='sale':['jeonse','monthly'].includes(String(raw.rent_kind)))||typeof raw.area_m2!=='string'||!/^\d+(?:\.\d+)?$/.test(raw.area_m2)||Number(raw.area_m2)<=0||Number(raw.area_m2)>10000||!Number.isSafeInteger(raw.transaction_count)||Number(raw.transaction_count)<=0||typeof raw.latest_transaction_id!=='string'||raw.latest_transaction_id.length>256||typeof raw.latest_contract_date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(raw.latest_contract_date)||raw.latest_contract_date.slice(0,7).replace('-','')!==raw.deal_month)return fail();
    if(!/^molit-(sale|rent):[a-f0-9]{64}:[1-9][0-9]*$/.test(raw.latest_transaction_id)||!raw.latest_transaction_id.startsWith(`molit-${raw.trade_type}:`)||!Number.isFinite(Date.parse(raw.latest_contract_date))||new Date(raw.latest_contract_date).toISOString().slice(0,10)!==raw.latest_contract_date||month===null&&(raw.deal_month<String(value.from_month)||raw.deal_month>String(value.to_month)))return fail();
    for(const field of ['latest_price_krw','latest_deposit_krw','latest_monthly_rent_krw'])if(raw[field]!==null&&(!Number.isSafeInteger(raw[field])||Number(raw[field])<0||Number(raw[field])%10000!==0))return fail();
    for(const field of ['min_price_krw','max_price_krw','median_price_krw','median_deposit_krw','median_monthly_rent_krw','median_price_per_m2_krw','median_price_per_pyeong_krw'])if(raw[field]!==undefined&&raw[field]!==null&&(typeof raw[field]!=='number'||!Number.isFinite(raw[field])||Number(raw[field])<0))return fail();
    if(raw.trade_type==='sale'?raw.latest_price_krw===null||raw.latest_deposit_krw!==null||raw.latest_monthly_rent_krw!==null:raw.latest_price_krw!==null||raw.latest_deposit_krw===null||raw.latest_monthly_rent_krw===null||raw.rent_kind==='jeonse'&&raw.latest_monthly_rent_krw!==0||raw.rent_kind==='monthly'&&Number(raw.latest_monthly_rent_krw)<=0)return fail();
    const key=JSON.stringify([raw.complex_id,raw.deal_month,raw.trade_type,raw.rent_kind,raw.area_m2]);if(seen.has(key))return fail();seen.add(key);
    return raw as unknown as ComplexPriceSummary;
  });
}
export function parseComplexMonthSummaryPack(value:unknown,release:string,region:string,month:string|null):ComplexPriceSummary[]{
  if(!object(value)||value.schema_version!==1||value.kind!=='property-complex-summary-month-pack'||value.property_release_id!==release||value.lawd_code!==region||!Array.isArray(value.months)||!value.months.length||value.months.length>1200)return fail();
  const seen=new Set<string>();let selected:ComplexPriceSummary[]|undefined;const all:ComplexPriceSummary[]=[];
  for(const part of value.months){if(!object(part)||typeof part.deal_month!=='string'||!MONTH.test(part.deal_month)||seen.has(part.deal_month))return fail();seen.add(part.deal_month);const rows=parseComplexPriceSummaries({...value,...part,kind:'property-complex-summaries'},release,region,part.deal_month);all.push(...rows);if(part.deal_month===month)selected=rows;}
  return month===null?all:selected??fail();
}
export interface SummarySelection {trade:'sale'|'rent';area?:string;rentKind?:'all'|'jeonse'|'monthly';}
/** Compressed regional history is read one bounded asset at a time. The
 * regional aggregate ceiling is separate from the per-complex query ceiling;
 * encoded and decoded budgets both apply, including off-window packed rows. */
export function assertSummaryReadBudget(refs:readonly Pick<SummaryRef,'bytes'|'transport'>[],regional=false):void {
  const compressedRegional=regional&&refs.every(row=>!!row.transport);
  const decodedLimit=(compressedRegional?128:16)*1024*1024;
  const wireLimit=(compressedRegional?32:16)*1024*1024;
  if(refs.length>512||refs.reduce((n,r)=>n+r.bytes,0)>decodedLimit||refs.reduce((n,r)=>n+(r.transport?.bytes??r.bytes),0)>wireLimit)throw new Error('선택 기간의 가격 요약이 조회 한도를 넘었습니다. 기간을 줄여 주세요.');
}
export function retainSelectedSummaries(rows:readonly ComplexPriceSummary[],months:ReadonlySet<string>,complexId?:string,selection?:SummarySelection):ComplexPriceSummary[]{
  return rows.filter(row=>months.has(row.deal_month)&&(!complexId||row.complex_id===complexId)&&(!selection||row.trade_type===selection.trade&&areaMatches(row.area_m2,selection.area??'')&&(selection.trade!=='rent'||!selection.rentKind||selection.rentKind==='all'||row.rent_kind===selection.rentKind)));
}
/** Immutable summary indexes share the same four-slot gate and hash cache as
 * transactions. Only selected months are downloaded; no nationwide raw preload. */
export async function loadComplexPriceSummaries(release:string,region:string,months:readonly string[],signal:AbortSignal,complexId?:string,selection?:SummarySelection):Promise<{rows:ComplexPriceSummary[];partitions:SummaryPartition[]}|null>{
  const registered=(sources as unknown as SummarySource[]).find(row=>row.release_id===release);if(!registered)return null;
  if(!/^https:\/\/[a-f0-9]{8}\.korea-replay-data\.pages\.dev$/.test(registered.origin)||!/^\/data\/property-summary\/summary-[a-f0-9]{16}\/manifest\.json$/.test(registered.manifest.path??'')||!HASH.test(registered.manifest.sha256))return fail();
  if(!/^\d{5}$/.test(region)||months.length>240||months.some(month=>!MONTH.test(month)))return fail();
  const manifest=await fetchPinnedJson(registered.manifest,registered.origin,signal);
  if(!object(manifest)||manifest.schema_version!==1||manifest.kind!=='property-complex-summary-release'||manifest.property_release_id!==release||!Array.isArray(manifest.regions))return fail();
  const indexRef=manifest.regions.find(row=>object(row)&&row.lawd_code===region);if(!indexRef)return null;
  const index=await fetchPinnedJson(ref(indexRef),registered.origin,signal);
  if(!object(index)||index.kind!=='property-complex-summary-region'||index.property_release_id!==release||index.lawd_code!==region||!Array.isArray(index.partitions)||!Array.isArray(index.summaries))return fail();
  const wanted=new Set(months),partitions:SummaryPartition[]=[];
  for(const p of index.partitions){if(!object(p)||typeof p.deal_month!=='string'||!MONTH.test(p.deal_month)||!['sale','rent'].includes(String(p.trade_type))||!['complete','empty','pending','failed','partial','source_unavailable'].includes(String(p.status)))return fail();if(wanted.has(p.deal_month))partitions.push(p as unknown as SummaryPartition);}
  const packed=complexId&&object(index.complex_chunks)&&Array.isArray(index.complex_chunks[complexId])?index.complex_chunks[complexId]:null;
  const refs=(packed??index.summaries).map(ref).filter(row=>row.deal_month?wanted.has(row.deal_month):!!row.from_month&&!!row.to_month&&months.some(month=>month>=row.from_month!&&month<=row.to_month!));
  const distinct=new Map(refs.map(row=>[`${row.url}:${row.sha256}`,row]));
  assertSummaryReadBudget([...distinct.values()],!complexId);
  const rows:ComplexPriceSummary[]=[];let next=0;const assets=[...distinct.values()];
  const query=new AbortController(),cancel=()=>query.abort();signal.addEventListener('abort',cancel,{once:true});if(signal.aborted)cancel();
  const worker=async()=>{while(next<assets.length){if(query.signal.aborted)throw new DOMException('Aborted','AbortError');const descriptor=assets[next++],raw=await fetchPinnedJson(descriptor,registered.origin,query.signal);if(object(raw)&&raw.kind==='property-complex-summary-month-pack'){const expected=refs.filter(row=>row.url===descriptor.url&&row.sha256===descriptor.sha256).flatMap(row=>row.deal_month?[row.deal_month]:[]);if(!Array.isArray(raw.months)||expected.some(month=>!(raw.months as unknown[]).some(part=>object(part)&&part.deal_month===month)))return fail();}const decoded=object(raw)&&raw.kind==='property-complex-summary-month-pack'&&descriptor.deal_month?parseComplexMonthSummaryPack(raw,release,region,null):parseComplexPriceSummaries(raw,release,region,descriptor.deal_month??null);rows.push(...retainSelectedSummaries(decoded,wanted,complexId,selection));if(rows.length>250_000)throw new Error('선택 조건의 요약이 너무 많습니다. 면적이나 기간을 줄여 주세요.');}};
  try{await Promise.all([worker(),worker()]);return {rows,partitions};}
  catch(error){query.abort();throw error;}
  finally{signal.removeEventListener('abort',cancel);}
}
