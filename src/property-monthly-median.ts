import {areaMatches} from '../shared/property-area';
import {propertyStatisticsEligible,type PropertyTransaction} from '../shared/property';
import type {PriceBasis} from '../shared/property-pricing';
import {rentKindMatches} from '../shared/property-rent';
import type {ComplexPriceSummary,SummaryPartition} from './property-map-prices';
import {matchingSummaryRows,monthlySummaryPoints,type SummarySelection} from './property-monthly-summary';
import type {HistoryResult} from './property-history-loader';

export type PriceDistribution=readonly (readonly [number,number])[];
/** Match the producer's Decimal half-even rounding before ranking unit prices. */
export function distributionPrice(amount:number,area:string,basis:PriceBasis):number|null{
  if(!Number.isSafeInteger(amount)||amount<0||area.length>24||!/^\d+(?:\.\d{1,18})?$/.test(area))return null;
  if(basis==='total')return amount;
  const [whole,fraction='']=area.split('.'),scale=10n**BigInt(fraction.length);
  const divisor=BigInt(whole+fraction);if(divisor<=0n)return null;
  const numerator=BigInt(amount)*scale*(basis==='pyeong'?400n:1n),denominator=divisor*(basis==='pyeong'?121n:1n);
  const quotient=numerator/denominator,remainder=numerator%denominator;
  const rounded=quotient+(remainder*2n>denominator||remainder*2n===denominator&&quotient%2n!==0n?1n:0n);
  return rounded<=BigInt(Number.MAX_SAFE_INTEGER)?Number(rounded):null;
}
export function weightedMedian(entries:PriceDistribution):number|null{
  if(!entries.length)return null;
  const sorted=[...entries].sort((a,b)=>a[0]-b[0]),count=sorted.reduce((n,row)=>n+row[1],0);
  if(!Number.isSafeInteger(count)||count<=0)return null;
  const lower=Math.floor((count-1)/2),upper=Math.floor(count/2);let seen=0,a:number|undefined,b:number|undefined;
  for(const [value,weight] of sorted){if(a===undefined&&seen+weight>lower)a=value;if(seen+weight>upper){b=value;break;}seen+=weight;}
  return a===undefined||b===undefined?null:a/2+b/2;
}
export function summaryMedian(rows:readonly ComplexPriceSummary[],basis:PriceBasis):number|null{
  if(!rows.length||new Set(rows.map(row=>row.rent_kind)).size!==1)return null;
  if(rows.every(row=>row.price_distribution)){
    const entries:Array<[number,number]>=[];
    for(const row of rows)for(const [amount,count] of row.price_distribution!){const value=distributionPrice(amount,row.area_m2,basis);if(value===null)return null;entries.push([value,count]);}
    return weightedMedian(entries);
  }
  // A median of medians loses report weights and is never an exact band median.
  if(rows.length!==1)return null;
  const row=rows[0],value=basis==='pyeong'?row.median_price_per_pyeong_krw:basis==='m2'?row.median_price_per_m2_krw:row.trade_type==='sale'?row.median_price_krw:row.median_deposit_krw;
  return typeof value==='number'&&Number.isFinite(value)&&value>=0?value:null;
}
export function rawMonthlyMedian(result:HistoryResult,selection:SummarySelection,basis:PriceBasis,expectedCount:number):number|null{
  if(result.status!=='ready'||selection.trade==='rent'&&selection.rentKind==='all')return null;
  const rows=result.rows.filter(row=>propertyStatisticsEligible(row)&&row.complex_id===selection.complexId&&row.trade_type===selection.trade&&row.contract_date?.slice(0,7).replace('-','')===result.month&&row.area_m2&&areaMatches(row.area_m2,selection.area)&&rentKindMatches(row,selection.rentKind));
  if(rows.length!==expectedCount)return null;
  const entries:Array<[number,number]>=[];
  for(const row of rows){const amount=selection.trade==='sale'?row.price_krw:row.deposit_krw;if(amount===null||!row.area_m2)return null;const value=distributionPrice(amount,row.area_m2,basis);if(value===null)return null;entries.push([value,1]);}
  return weightedMedian(entries);
}
export function rawMedianPoints(rows:readonly PropertyTransaction[],months:readonly string[],selection:Omit<SummarySelection,'months'>,basis:PriceBasis,known:ReadonlySet<string>){
  const grouped=new Map<string,PropertyTransaction[]>();
  for(const row of rows){if(!propertyStatisticsEligible(row)||row.complex_id!==selection.complexId||row.trade_type!==selection.trade||!row.contract_date||!row.area_m2||!areaMatches(row.area_m2,selection.area)||!rentKindMatches(row,selection.rentKind))continue;const month=row.contract_date.slice(0,7).replace('-',''),group=grouped.get(month)??[];group.push(row);grouped.set(month,group);}
  return months.map(month=>{
    const selected=grouped.get(month)??[];
    const count=known.has(month)?selected.length:null;
    return {month,count,median:count?rawMonthlyMedian({month,status:'ready',rows:selected},{...selection,months},basis,count):null};
  });
}
export function monthlyMedianPoints(rows:readonly ComplexPriceSummary[],partitions:readonly SummaryPartition[],selection:SummarySelection,basis:PriceBasis,raw:readonly HistoryResult[]=[]){
  const matched=matchingSummaryRows(rows,selection),byMonth=new Map<string,ComplexPriceSummary[]>();
  for(const row of matched){const group=byMonth.get(row.deal_month)??[];group.push(row);byMonth.set(row.deal_month,group);}
  return monthlySummaryPoints(rows,partitions,selection).map(point=>{
    const groups=byMonth.get(point.month)??[],mixed=selection.trade==='rent'&&selection.rentKind==='all';
    const known=point.count!==null&&point.count>0&&!mixed;
    let median=known?summaryMedian(groups,basis):null;
    const record=raw.find(row=>row.month===point.month);
    if(median===null&&known&&record)median=rawMonthlyMedian(record,selection,basis,point.count!);
    return {...point,median,needsRaw:known&&median===null&&!record,mixedRent:mixed};
  });
}
/** Never join across unknown months, actual zero-trade months, or missing values. */
export function medianLinePaths(values:readonly (number|null)[],minimum:number|null,maximum:number|null):string[]{
  if(minimum===null||maximum===null)return [];
  const paths:string[]=[];let segment:string[]=[];
  const flush=()=>{if(segment.length>1)paths.push(segment.join(' '));segment=[];};
  values.forEach((value,index)=>{if(value===null){flush();return;}const x=12+(index+.5)*296/values.length,y=minimum===maximum?85:138-(value-minimum)/Math.max(1,maximum-minimum)*108;segment.push(`${segment.length?'L':'M'}${x.toFixed(2)},${y.toFixed(2)}`);});flush();return paths;
}
