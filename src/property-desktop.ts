import type {PropertyTransaction} from '../shared/property';
import {NATIONAL_AREA} from '../shared/property-area';

/** Uses reported exclusive areas only; never estimates a supply-area floor plan. */
export function defaultDetailArea(rows:readonly PropertyTransaction[]):string{
  const counts=new Map<string,number>();
  for(const row of rows){
    if(!row.statistics_eligible||row.quality==='invalid'||row.cancellation==='cancelled'||row.trade_type==='sale'&&row.cancellation!=='not_reported'||row.area_m2===null)continue;
    const value=Number(row.area_m2);if(!Number.isFinite(value)||value<=0)continue;
    if(value>=84&&value<85)return NATIONAL_AREA;
    counts.set(row.area_m2,(counts.get(row.area_m2)??0)+1);
  }
  return [...counts].sort((a,b)=>b[1]-a[1]||Number(a[0])-Number(b[0])||a[0].localeCompare(b[0]))[0]?.[0]??'';
}

/** Pure loan arithmetic, independent of lending eligibility, taxes and fees. */
export function equalLoanPayment(principal:number,annualRate:number,months:number){
  if(!Number.isFinite(principal)||principal<=0||principal>1e12||!Number.isFinite(annualRate)||annualRate<0||annualRate>100||!Number.isInteger(months)||months<1||months>600)return null;
  const rate=annualRate/1200;
  const payment=rate===0?principal/months:principal*rate/-Math.expm1(-months*Math.log1p(rate));
  const total=payment*months;
  return {monthly:Math.round(payment),interest:Math.round(total-principal),total:Math.round(total)};
}

export function readComplexNote(raw:string|null):string{
  if(raw===null)return '';
  const item:unknown=JSON.parse(raw);
  if(!item||typeof item!=='object'||Array.isArray(item)||!('version' in item)||item.version!==1||!('text' in item)||typeof item.text!=='string'||item.text.length>4000)throw new Error('저장된 메모 형식을 확인해 주세요.');
  return item.text;
}
