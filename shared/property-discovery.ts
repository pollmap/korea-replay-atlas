import {propertyStatisticsEligible,type PropertyComplex,type PropertyTransaction} from './property';
import {transactionPrice} from './property-pricing';

export type PropertyDiscoverySort='recent'|'count'|'name'|'price-low'|'price-high'|'pyeong-low'|'pyeong-high';
export interface PropertyDiscoveryFilters {
  query:string; dong:string; buildYearMin:string; buildYearMax:string;
  priceMinEok:string; priceMaxEok:string; areaMinM2:string; areaMaxM2:string;
  sort:PropertyDiscoverySort;
}
export const EMPTY_PROPERTY_DISCOVERY_FILTERS:PropertyDiscoveryFilters={query:'',dong:'',buildYearMin:'',buildYearMax:'',priceMinEok:'',priceMaxEok:'',areaMinM2:'',areaMaxM2:'',sort:'recent'};
export interface PropertyDiscoveryItem {
  complex:PropertyComplex;
  /** Null means unverified/unloaded, whereas zero requires a completed month. */
  count:number|null;
  latest:PropertyTransaction|null;
}
export interface PropertyDiscoveryResult {items:PropertyDiscoveryItem[];errors:string[];transactionFiltersPending:boolean;}
const nameOrder=new Intl.Collator('ko',{numeric:true});
const normalize=(value:string)=>value.normalize('NFKC').toLocaleLowerCase('ko-KR').replace(/\s+/g,'');

/** Parse an unsigned decimal without coercing blanks, NaN, or Infinity to numbers. */
function bound(value:string,maximum:number,decimals:number,scale:number):number|null|'invalid'{
  const input=value.trim();if(!input)return null;
  const pattern=decimals===0?/^\d+$/:new RegExp(`^\\d+(?:\\.\\d{1,${decimals}})?$`);
  if(!pattern.test(input))return 'invalid';
  const numeric=Number(input);if(!Number.isFinite(numeric)||numeric>maximum)return 'invalid';
  const scaled=Math.round(numeric*scale);
  if(!Number.isSafeInteger(scaled))return 'invalid';
  return decimals===0||scale!==1?scaled:numeric;
}
function range(minText:string,maxText:string,label:string,maximum:number,decimals:number,scale:number,errors:string[]):[number|null,number|null]{
  const min=bound(minText,maximum,decimals,scale),max=bound(maxText,maximum,decimals,scale);
  if(min==='invalid'||max==='invalid'){errors.push(`${label} 범위를 올바른 숫자로 입력해 주세요.`);return [null,null];}
  if(min!==null&&max!==null&&min>max)errors.push(`${label} 최솟값은 최댓값보다 클 수 없습니다.`);
  return [min,max];
}
function within(value:number|null,min:number|null,max:number|null):boolean{
  if(min===null&&max===null)return true;
  return value!==null&&Number.isFinite(value)&&(min===null||value>=min)&&(max===null||value<=max);
}

/** One pass over transactions, followed by ID lookups; names never join identities. */
export function discoverPropertyComplexes(complexes:readonly PropertyComplex[],rows:readonly PropertyTransaction[],dataReady:boolean,trade:'sale'|'rent',filters:PropertyDiscoveryFilters):PropertyDiscoveryResult{
  const errors:string[]=[];
  const [yearMin,yearMax]=range(filters.buildYearMin,filters.buildYearMax,'건축연도',9999,0,1,errors);
  const [priceMin,priceMax]=range(filters.priceMinEok,filters.priceMaxEok,trade==='sale'?'매매가':'보증금',90_000_000,8,100_000_000,errors);
  const [areaMin,areaMax]=range(filters.areaMinM2,filters.areaMaxM2,'전용면적',10_000,5,1,errors);
  const transactionFiltered=[priceMin,priceMax,areaMin,areaMax].some(value=>value!==null);
  const transactionFiltersPending=!dataReady&&transactionFiltered;
  if(errors.length)return {items:[],errors,transactionFiltersPending};
  const byId=new Map<string,{count:number;latest:PropertyTransaction}>();
  if(dataReady)for(const row of rows){
    if(row.trade_type!==trade||row.complex_id===null||!propertyStatisticsEligible(row))continue;
    const value=trade==='sale'?row.price_krw:row.deposit_krw;
    if(!within(value,priceMin,priceMax)||!within(row.area_m2===null?null:Number(row.area_m2),areaMin,areaMax))continue;
    const current=byId.get(row.complex_id);
    if(!current){byId.set(row.complex_id,{count:1,latest:row});continue;}
    current.count++;
    if(row.contract_date!>current.latest.contract_date!||row.contract_date===current.latest.contract_date&&row.id<current.latest.id)current.latest=row;
  }
  const query=normalize(filters.query),items:PropertyDiscoveryItem[]=[];
  for(const complex of complexes){
    if(filters.dong&&complex.legal_dong_name!==filters.dong)continue;
    if(!within(complex.build_year,yearMin,yearMax))continue;
    if(query){
      const dong=normalize(complex.legal_dong_name??'');
      const matches=dong.includes(query)||[complex.name,...complex.observed_name_variants].some(value=>{
        const name=normalize(value);return name.includes(query)||(name+dong).includes(query)||(dong+name).includes(query);
      });
      if(!matches)continue;
    }
    const records=byId.get(complex.id);
    if(dataReady&&transactionFiltered&&!records)continue;
    items.push({complex,count:dataReady?records?.count??0:null,latest:records?.latest??null});
  }
  items.sort((a,b)=>{
    if(filters.sort==='pyeong-low'||filters.sort==='pyeong-high'){
      const av=a.latest?transactionPrice(a.latest,'pyeong'):null,bv=b.latest?transactionPrice(b.latest,'pyeong'):null;
      if(av===null&&bv!==null)return 1;if(av!==null&&bv===null)return -1;
      if(av!==null&&bv!==null&&av!==bv)return filters.sort==='pyeong-low'?av-bv:bv-av;
    }
    if(filters.sort==='price-low'||filters.sort==='price-high'){
      const av=trade==='sale'?a.latest?.price_krw:a.latest?.deposit_krw,bv=trade==='sale'?b.latest?.price_krw:b.latest?.deposit_krw;
      if(av==null&&bv!=null)return 1;if(av!=null&&bv==null)return -1;
      if(av!=null&&bv!=null&&av!==bv)return filters.sort==='price-low'?av-bv:bv-av;
    }
    if(filters.sort==='count'){const order=(b.count??-1)-(a.count??-1);if(order)return order;}
    if(filters.sort!=='name'){const order=(b.latest?.contract_date??'').localeCompare(a.latest?.contract_date??'');if(order)return order;}
    return nameOrder.compare(a.complex.name,b.complex.name)||nameOrder.compare(a.complex.legal_dong_name??'',b.complex.legal_dong_name??'')||a.complex.id.localeCompare(b.complex.id);
  });
  return {items,errors,transactionFiltersPending};
}

export function propertyDiscoveryDongs(complexes:readonly PropertyComplex[]):string[]{
  return [...new Set(complexes.flatMap(complex=>complex.legal_dong_name?[complex.legal_dong_name]:[]))].sort(nameOrder.compare);
}
