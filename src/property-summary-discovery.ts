import type {PropertyComplex} from '../shared/property';
import {areaMatches} from '../shared/property-area';
import {discoverPropertyComplexes,propertyDiscoveryBounds,propertyDiscoveryWithin,type PropertyDiscoveryFilters} from '../shared/property-discovery';
import {summaryPrice} from './property-monthly-summary';
import {propertySummaryCoverage} from './property-summary-coverage';
import type {ComplexPriceSummary,SummaryPartition} from './property-map-prices';

export interface SummaryDiscoveryItem {
  complex:PropertyComplex;latest:ComplexPriceSummary|null;
  /** Null denotes an incomplete period. confirmedCount is never a full-period claim. */
  count:number|null;confirmedCount:number;missingMonths:number;sourceUnavailableMonths:number;
}
export interface SummaryDiscoverySelection {months:readonly string[];trade:'sale'|'rent';area?:string;}
const nameOrder=new Intl.Collator('ko',{numeric:true});
const amount=(row:ComplexPriceSummary|null)=>row?row.trade_type==='sale'?row.latest_price_krw:row.latest_deposit_krw:null;

/** Summary rows remain summary rows. No raw transaction, floor, or price-bounded
 * transaction count is fabricated from monthly aggregates. Price bounds select
 * complexes by their last real contract; counts retain the area/type scope. */
export function discoverPropertySummaryComplexes(complexes:readonly PropertyComplex[],rows:readonly ComplexPriceSummary[],partitions:readonly SummaryPartition[],selection:SummaryDiscoverySelection,filters:PropertyDiscoveryFilters){
  const metadata=discoverPropertyComplexes(complexes,[],false,selection.trade,filters);
  const {priceMin,priceMax,areaMin,areaMax,errors}=propertyDiscoveryBounds(filters,selection.trade);
  const priceFiltered=priceMin!==null||priceMax!==null;
  const months=new Set(selection.months),{complete,unavailable,missingMonths,canEstablishNoTrade}=propertySummaryCoverage(partitions,selection.months,selection.trade);
  if(errors.length)return {items:[] as SummaryDiscoveryItem[],errors,transactionFiltersPending:missingMonths>0,priceFiltered,verifiedMonths:complete.size,missingMonths,sourceUnavailableMonths:unavailable.size};
  const grouped=new Map<string,{latest:ComplexPriceSummary;count:number}>();
  for(const row of rows){
    if(row.trade_type!==selection.trade||!months.has(row.deal_month)||!complete.has(row.deal_month)||!areaMatches(row.area_m2,selection.area??'')||!propertyDiscoveryWithin(Number(row.area_m2),areaMin,areaMax)||selection.trade==='rent'&&filters.rentKind&&filters.rentKind!=='all'&&row.rent_kind!==(filters.rentKind==='jeonse'?'jeonse':'monthly'))continue;
    const current=grouped.get(row.complex_id);
    if(!current)grouped.set(row.complex_id,{latest:row,count:row.transaction_count});
    else {current.count+=row.transaction_count;if(row.latest_contract_date>current.latest.latest_contract_date||row.latest_contract_date===current.latest.latest_contract_date&&row.latest_transaction_id<current.latest.latest_transaction_id)current.latest=row;}
  }
  const transactionFiltered=!!filters.hasTrades||selection.trade==='rent'&&!!filters.rentKind&&filters.rentKind!=='all'||!!selection.area||priceFiltered||areaMin!==null||areaMax!==null;
  const items:SummaryDiscoveryItem[]=[];
  for(const {complex} of metadata.items){const current=grouped.get(complex.id),latest=current?.latest??null;
    if(latest&&priceFiltered&&!propertyDiscoveryWithin(amount(latest),priceMin,priceMax))continue;
    if(!latest&&transactionFiltered&&canEstablishNoTrade)continue;
    items.push({complex,latest,count:missingMonths||!complete.size?null:current?.count??0,confirmedCount:current?.count??0,missingMonths,sourceUnavailableMonths:unavailable.size});
  }
  items.sort((a,b)=>{
    if(filters.sort==='price-low'||filters.sort==='price-high'||filters.sort==='pyeong-low'||filters.sort==='pyeong-high'){
      const basis=filters.sort.startsWith('pyeong')?'pyeong':'total',av=a.latest?summaryPrice(a.latest,basis):null,bv=b.latest?summaryPrice(b.latest,basis):null;
      if(av===null&&bv!==null)return 1;if(av!==null&&bv===null)return -1;
      if(av!==null&&bv!==null&&av!==bv)return filters.sort.endsWith('low')?av-bv:bv-av;
    }
    if(filters.sort==='count'){const order=b.confirmedCount-a.confirmedCount;if(order)return order;}
    if(filters.sort!=='name'){const order=(b.latest?.latest_contract_date??'').localeCompare(a.latest?.latest_contract_date??'');if(order)return order;}
    return nameOrder.compare(a.complex.name,b.complex.name)||nameOrder.compare(a.complex.legal_dong_name??'',b.complex.legal_dong_name??'')||a.complex.id.localeCompare(b.complex.id);
  });
  return {items,errors,transactionFiltersPending:missingMonths>0&&transactionFiltered,priceFiltered,verifiedMonths:complete.size,missingMonths,sourceUnavailableMonths:unavailable.size};
}
