import {propertyStatisticsEligible,type PropertyTransaction} from './property';
import {areaMatches,M2_PER_PYEONG,NATIONAL_AREA} from './property-area';

export type PriceBasis='total'|'pyeong'|'m2';
export const PRICE_BASES:readonly [PriceBasis,string][]=[['total','총액'],['pyeong','전용 평당가'],['m2','전용 ㎡당가']];
export function transactionPrice(row:PropertyTransaction,basis:PriceBasis):number|null {
  const amount=row.trade_type==='sale'?row.price_krw:row.deposit_krw;
  if(amount===null||!Number.isFinite(amount)||amount<0)return null;
  if(basis==='total')return amount;
  const area=Number(row.area_m2);
  if(!Number.isFinite(area)||area<=0)return null;
  return amount/area*(basis==='pyeong'?M2_PER_PYEONG:1);
}
export function priceMedian(values:readonly number[]):number|null {
  const sorted=values.filter(Number.isFinite).sort((a,b)=>a-b),middle=Math.floor(sorted.length/2);
  return sorted.length?(sorted.length%2?sorted[middle]:(sorted[middle-1]+sorted[middle])/2):null;
}
/** Medians of individual eligible reports, not median total price / median area. */
export function pricingSummary(rows:readonly PropertyTransaction[],options:{complexId:string;trade:'sale'|'rent';area:string}) {
  const valid=rows.filter(row=>row.complex_id===options.complexId&&row.trade_type===options.trade&&propertyStatisticsEligible(row));
  const selected=valid.filter(row=>areaMatches(row.area_m2,options.area));
  const national=valid.filter(row=>areaMatches(row.area_m2,NATIONAL_AREA));
  const perPyeong=selected.flatMap(row=>{const price=transactionPrice(row,'pyeong');return price===null?[]:[price];});
  const totals=selected.flatMap(row=>{const price=transactionPrice(row,'total');return price===null?[]:[price];});
  const nationalTotals=national.flatMap(row=>{const price=transactionPrice(row,'total');return price===null?[]:[price];});
  return {count:totals.length,median:priceMedian(totals),perPyeong:priceMedian(perPyeong),
    min:totals.length?Math.min(...totals):null,max:totals.length?Math.max(...totals):null,
    nationalCount:nationalTotals.length,nationalMedian:priceMedian(nationalTotals)};
}
