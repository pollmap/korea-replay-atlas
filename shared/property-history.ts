import type {PropertyRegionDetail,PropertyTransaction} from './property';

export const HISTORY_RANGES=[1,3,6,12,36,60,120] as const;
export type HistoryRange=typeof HISTORY_RANGES[number];
export function historyRangeLabel(value:HistoryRange):string{return value>=12?`${value/12}년`:`${value}개월`;}
export function historyTick(index:number,count:number):boolean{return index===0||index===count-1||count<=6||index%Math.ceil(count/4)===0&&index<count-Math.ceil(count/8);}
export function historyMonths(end:string,count:HistoryRange):string[]{
  if(!/^\d{4}(0[1-9]|1[0-2])$/.test(end)||!HISTORY_RANGES.includes(count))throw new Error('invalid_history_range');
  const year=Number(end.slice(0,4)),month=Number(end.slice(4));
  return Array.from({length:count},(_,i)=>{const date=new Date(Date.UTC(year,month-count+i,1));return `${date.getUTCFullYear()}${String(date.getUTCMonth()+1).padStart(2,'0')}`;});
}
export function historyPlan(detail:PropertyRegionDetail,end:string,count:HistoryRange,trade:'sale'|'rent'){
  return historyMonths(end,count).map(month=>({month,partition:detail.partitions.find(p=>p.deal_month===month&&p.trade_type===trade)}));
}
/** Epoch-day coordinates keep July 1 and August 1 distinct, including leap days. */
export function historyDay(date:string):number{return Date.parse(date+'T00:00:00Z')/86400000;}
export function historyPrice(row:PropertyTransaction):number|null{return row.trade_type==='sale'?row.price_krw:row.deposit_krw;}
export function historySummary(rows:readonly PropertyTransaction[]){
  let min:number|null=null,max:number|null=null,latest:PropertyTransaction|null=null;
  for(const row of rows){const value=historyPrice(row);if(value===null||!row.contract_date)continue;
    min=min===null?value:Math.min(min,value);max=max===null?value:Math.max(max,value);
    if(!latest||row.contract_date>latest.contract_date!||row.contract_date===latest.contract_date&&row.id<latest.id)latest=row;
  }
  return {min,max,latest,count:rows.length};
}
