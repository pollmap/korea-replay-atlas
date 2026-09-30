import {areaMatches,M2_PER_PYEONG,NATIONAL_AREA} from '../shared/property-area';
import type {PriceBasis} from '../shared/property-pricing';
import type {RentKind} from '../shared/property-rent';
import type {ComplexPriceSummary,SummaryPartition} from './property-map-prices';

export interface SummarySelection {complexId:string;trade:'sale'|'rent';rentKind:RentKind;area:string;months:readonly string[];}
export interface MonthlySummaryPoint {month:string;status:string;count:number|null;latest:ComplexPriceSummary|null;}
export function matchingSummaryRows(rows:readonly ComplexPriceSummary[],selection:SummarySelection){
  const months=new Set(selection.months);
  return rows.filter(row=>row.complex_id===selection.complexId&&row.trade_type===selection.trade&&months.has(row.deal_month)&&areaMatches(row.area_m2,selection.area)&&(selection.trade==='sale'||selection.rentKind==='all'||row.rent_kind===(selection.rentKind==='jeonse'?'jeonse':'monthly')));
}
export function monthlySummaryPoints(rows:readonly ComplexPriceSummary[],partitions:readonly SummaryPartition[],selection:SummarySelection):MonthlySummaryPoint[]{
  const matched=matchingSummaryRows(rows,selection),grouped=new Map<string,ComplexPriceSummary[]>();
  for(const row of matched)grouped.set(row.deal_month,[...(grouped.get(row.deal_month)??[]),row]);
  return selection.months.map(month=>{
    const status=partitions.find(item=>item.deal_month===month&&item.trade_type===selection.trade)?.status??'pending';
    if(!['complete','empty'].includes(status))return {month,status,count:null,latest:null};
    const group=(grouped.get(month)??[]).sort((a,b)=>b.latest_contract_date.localeCompare(a.latest_contract_date)||a.latest_transaction_id.localeCompare(b.latest_transaction_id));
    return {month,status,count:group.reduce((total,row)=>total+row.transaction_count,0),latest:group[0]??null};
  });
}
export function summaryPrice(row:ComplexPriceSummary,basis:PriceBasis):number|null{
  const value=row.trade_type==='sale'?row.latest_price_krw:row.latest_deposit_krw;
  return value===null?null:basis==='total'?value:basis==='pyeong'?value/Number(row.area_m2)*M2_PER_PYEONG:value/Number(row.area_m2);
}
/** Frequency is weighted by actual report counts, not by monthly group count. */
export function defaultSummaryArea(rows:readonly ComplexPriceSummary[],selection:Omit<SummarySelection,'area'>):string{
  const matched=matchingSummaryRows(rows,{...selection,area:''}),counts=new Map<string,number>();
  for(const row of matched){if(areaMatches(row.area_m2,NATIONAL_AREA))return NATIONAL_AREA;counts.set(row.area_m2,(counts.get(row.area_m2)??0)+row.transaction_count);}
  return [...counts].sort((a,b)=>b[1]-a[1]||Number(a[0])-Number(b[0])||a[0].localeCompare(b[0]))[0]?.[0]??'';
}
