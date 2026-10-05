import {areaMatches,areaLabel,validAreaFilter,NATIONAL_AREA} from './property-area';
import type {PropertyStatus,PropertyTransaction,RegionMetric} from './property';
import {eligiblePropertyTransactions} from './property';
import {HISTORY_RANGES,type HistoryRange} from './property-history';
import {readRentKind,rentKindMatches,type RentKind} from './property-rent';
import type {PriceBasis} from './property-pricing';
import {EMPTY_PROPERTY_DISCOVERY_FILTERS,propertyDiscoveryBounds} from './property-discovery';
export interface LatestPriceSelection {latestPriceMinEok?:string;latestPriceMaxEok?:string;}
/** Qualifies apartments by their last valid contract; never filters historical rows. */
export function latestPriceBounds(view:LatestPriceSelection){return propertyDiscoveryBounds({...EMPTY_PROPERTY_DISCOVERY_FILTERS,priceMinEok:view.latestPriceMinEok??'',priceMaxEok:view.latestPriceMaxEok??''},'sale');}
function readLatestPrice(p:URLSearchParams):LatestPriceSelection{
  const min=p.get('latestPriceMinEok')??'',max=p.get('latestPriceMaxEok')??'';
  if(p.getAll('latestPriceMinEok').length>1||p.getAll('latestPriceMaxEok').length>1||[min,max].some(v=>v.length>24||v.trim()!==v||v&&!/^\d+(?:\.\d{1,8})?$/.test(v)))return {};
  const bounds=latestPriceBounds({latestPriceMinEok:min,latestPriceMaxEok:max});
  if(bounds.errors.length&&!(bounds.priceMin!==null&&bounds.priceMax!==null&&bounds.priceMin>bounds.priceMax))return {};
  return {...(min?{latestPriceMinEok:min}:{}),...(max?{latestPriceMaxEok:max}:{})};
}
export type PropertyType='apartment'|'officetel';
export type PropertyMarkerDisplay='price-area'|'price'|'unit-price'|'name';
export type PropertyDetailSection='trades'|'facts'|'fees'|'life'|'schools'|'region'|'costs'|'compare';
export interface PropertyViewState extends LatestPriceSelection {markerDisplay?:PropertyMarkerDisplay;priceBasis?:PriceBasis;areaBasis?:'exclusive'|'supply';detailSection?:PropertyDetailSection;legalDong?:string;regionQuery?:string;propertyType?:PropertyType;rentKind?:RentKind;region:string;trade:'sale'|'rent';month:string;complex:string;area:string;compare:string[];compareComplexes:string[];includeReview:boolean;historyMonths:HistoryRange;}
export function readPropertyView(hash:string,period:{from:string;to:string;latest_complete_month:string}):PropertyViewState{
  const p=new URLSearchParams(hash.replace(/^#/,'')),region=p.get('regionCode')??'',month=p.get('month')??'',complex=p.get('complex')??'',area=p.get('area')??'';
  const legalDong=p.get('legalDong')??'';
  const propertyType:PropertyType=p.get('propertyType')==='officetel'?'officetel':'apartment';
  return {...readLatestPrice(p),...(/^\d{5}$/.test(region)&&p.getAll('legalDong').length===1&&legalDong.trim()===legalDong&&legalDong.length>0&&legalDong.length<=80&&Array.from(legalDong).every(char=>char.charCodeAt(0)>=32&&char.charCodeAt(0)!==127)?{legalDong}:{}),...(p.get('regionQuery')&&p.get('regionQuery')!.length<=80&&Array.from(p.get('regionQuery')!).every(char=>char.charCodeAt(0)>=32)?{regionQuery:p.get('regionQuery')!}:{}),propertyType,...(p.get('trade')==='rent'&&readRentKind(p.get('rentKind'))!=='all'?{rentKind:readRentKind(p.get('rentKind'))}:{}),region:/^\d{5}$/.test(region)?region:'',trade:p.get('trade')==='rent'?'rent':'sale',
    month:/^\d{4}(?:0[1-9]|1[0-2])$/.test(month)&&month>=period.from&&month<=period.to?month:period.latest_complete_month,
    complex:propertyType==='apartment'&&/^molit-apt:\d{5}:[A-Za-z0-9_-]{1,64}$/.test(complex)?complex:'',
    area:validAreaFilter(area)?area:'',
    compare:[...new Set((p.get('compareRegions')??'').split(',').filter(v=>/^\d{5}$/.test(v)))].slice(0,3),
    compareComplexes:propertyType==='apartment'?[...new Set((p.get('compareComplexes')??'').split(',').filter(v=>/^molit-apt:\d{5}:[A-Za-z0-9_-]{1,64}$/.test(v)))].slice(0,3):[],
    markerDisplay:(['price-area','price','unit-price','name'].includes(p.get('markerDisplay')??'')?p.get('markerDisplay'):'price-area') as PropertyMarkerDisplay,
    priceBasis:(['total','pyeong','m2'].includes(p.get('priceBasis')??'')?p.get('priceBasis'):'total') as PriceBasis,
    areaBasis:p.get('areaBasis')==='supply'?'supply':'exclusive',
    detailSection:(['trades','facts','fees','life','schools','region','costs','compare'].includes(p.get('detailSection')??'')?p.get('detailSection'):'trades') as PropertyDetailSection,
    includeReview:p.get('review')==='include',historyMonths:(HISTORY_RANGES.includes(Number(p.get('historyMonths')) as HistoryRange)?Number(p.get('historyMonths')):p.toString()?3:36) as HistoryRange};
}

export const monthLabel=(month:string)=>`${month.slice(0,4)}.${month.slice(4,6)}`;
export function moneyLabel(value:number|null):string {
  if(value===null)return '자료 없음';
  if(value>=1e8){const remainder=Math.round(value%1e8/1e4);return `${Math.floor(value/1e8).toLocaleString('ko-KR')}억${remainder?` ${remainder.toLocaleString('ko-KR')}만`:''}`;}
  return `${(value/1e4).toLocaleString('ko-KR',{maximumFractionDigits:1})}만`;
}
/** A monthly-rent headline must not hide the payment behind the deposit. */
export function propertyHeadline(row:PropertyTransaction):{amount:string;label:string}{
  if(row.trade_type==='sale')return {amount:moneyLabel(row.price_krw),label:'매매'};
  if(row.monthly_rent_krw!==null&&row.monthly_rent_krw>0)return {amount:`${moneyLabel(row.deposit_krw)} / 월 ${moneyLabel(row.monthly_rent_krw)}`,label:'보증금 / 월세'};
  return {amount:moneyLabel(row.deposit_krw),label:row.monthly_rent_krw===0?'전세 보증금':'보증금'};
}
export function metricCount(metric:RegionMetric):number|null{return ['complete','empty'].includes(metric.status)?metric.eligible_rows:null;}
export function propertyStatus(status:string):string{return ({complete:'수집 완료',empty:'신고 없음',failed:'조회 실패',partial:'부분 수집',pending:'수집 대기',source_unavailable:'원천 자료 제공 전'} as Record<string,string>)[status]??'미확인';}
export interface PropertyRowsView {state:'ready'|'loading'|'unavailable'|'error';count:number|null;message:string;}
/** A filtered zero is meaningful only after the selected partition has been loaded. */
export function propertyRowsView({status,loading,loaded,error,count}:{status:PropertyStatus|undefined;loading:boolean;loaded:boolean;error:string;count:number}):PropertyRowsView{
  if(status!=='complete'&&status!=='empty')return {state:'unavailable',count:null,message:status==='source_unavailable'?'원천 자료 제공 전 기간입니다. 거래 0건을 뜻하지 않습니다.':status==='pending'?'선택 기간은 수집 대기 중입니다.':status==='partial'?'선택 기간은 부분 수집 상태입니다. 전체 거래를 확인할 수 없습니다.':status==='failed'?'선택 기간의 원천 조회에 실패했습니다.':'선택 기간의 자료가 없습니다.'};
  if(error)return {state:'error',count:null,message:'거래 자료를 불러오지 못했습니다. 거래 건수를 확인할 수 없습니다.'};
  if(loading||!loaded)return {state:'loading',count:null,message:'선택 월의 거래를 불러오는 중…'};
  return {state:'ready',count,message:count===0?'선택한 조건에 해당하는 신고 거래가 없습니다.':''};
}
/** Keep a pinned filter visible even when the new month has no matching area. */
export function propertyAreaOptions(areas:readonly string[],selected:string,dataReady:boolean):{value:string;label:string}[]{
  const available=new Set(areas);
  if(areas.some(value=>areaMatches(value,NATIONAL_AREA)))available.add(NATIONAL_AREA);
  if(selected.startsWith('range:')&&areas.some(value=>areaMatches(value,selected)))available.add(selected);
  return [...new Set([NATIONAL_AREA,...areas,...(selected?[selected]:[])])].sort((a,b)=>a===NATIONAL_AREA?-1:b===NATIONAL_AREA?1:Number(a)-Number(b)).map(value=>({value,label:`${value===NATIONAL_AREA||value.startsWith('range:')?areaLabel(value):`${value} ㎡`}${available.has(value)?'':dataReady?' · 현재 기간 거래 없음':' · 자료 확인 전'}`}));
}
export function transactionRows(rows:readonly PropertyTransaction[],filters:{trade:'sale'|'rent';complex:string|null;area:string;cancelled:boolean;rentKind?:RentKind}):PropertyTransaction[]{
  return (filters.cancelled?rows:eligiblePropertyTransactions(rows)).filter(row=>row.trade_type===filters.trade&&rentKindMatches(row,filters.rentKind)&&(!filters.complex||row.complex_id===filters.complex)&&areaMatches(row.area_m2,filters.area)).sort((a,b)=>(b.contract_date??'').localeCompare(a.contract_date??'')||a.id.localeCompare(b.id));
}
export function transactionCsv(rows:readonly PropertyTransaction[]):string {
  const cell=(value:unknown)=>{let text=value===null||value===undefined?'':String(value);if(/^[=+\-@\t\r]/.test(text))text=`'${text}`;return `"${text.replaceAll('"','""')}"`;};
  return '\uFEFF'+[['계약일','단지','전용면적_m2','층','매매가격_원','보증금_원','월세_원','등기일','취소상태','출처','수집시각'],...rows.map(r=>[r.contract_date,r.complex_name,r.area_m2,r.floor,r.price_krw,r.deposit_krw,r.monthly_rent_krw,r.registration_date,r.cancellation,r.source_id,r.retrieved_at])].map(row=>row.map(cell).join(',')).join('\r\n');
}
