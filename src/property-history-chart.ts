import {historyDay} from '../shared/property-history';

interface DatedRow {id:string;contract_date:string|null;}
export interface HistoryChartData<T> {rows:T[];points:T[];min:number|null;max:number|null;byId:ReadonlyMap<string,T>;}
const newest=<T extends DatedRow>(a:T,b:T)=>(b.contract_date??'').localeCompare(a.contract_date??'')||a.id.localeCompare(b.id);

/** Representative points are original reports; never synthesize a mean-price transaction. */
export function historyChartData<T extends DatedRow>(input:readonly T[],from:number,to:number,price:(row:T)=>number|null):HistoryChartData<T>{
  const values=new Map<string,number>(),byId=new Map<string,T>();let min:number|null=null,max:number|null=null;
  for(const row of input){
    if(!row.contract_date)continue;
    const day=historyDay(row.contract_date),value=price(row);
    if(!Number.isFinite(day)||day<from||day>to||value===null||!Number.isFinite(value))continue;
    byId.set(row.id,row);values.set(row.id,value);min=min===null?value:Math.min(min,value);max=max===null?value:Math.max(max,value);
  }
  const rows=[...byId.values()].sort(newest);
  if(rows.length<=1000)return {rows,points:rows,min,max,byId};
  const months=new Map<string,T[]>();
  for(const row of rows){const month=row.contract_date!.slice(0,7),group=months.get(month)??[];group.push(row);months.set(month,group);}
  // The shared history contract permits at most 240 months: four representatives each, plus selection.
  if(months.size>240)throw new Error('history_chart_window_exceeds_240_months');
  const selected=new Map<string,T>();
  for(const group of months.values()){
    const ordered=[...group].sort((a,b)=>values.get(a.id)!-values.get(b.id)!||newest(a,b));
    // An even sample uses the lower middle report, not an invented midpoint price.
    for(const row of [ordered[0],ordered.at(-1)!,ordered[Math.floor((ordered.length-1)/2)],group[0]])selected.set(row.id,row);
  }
  selected.set(rows[0].id,rows[0]);
  return {rows,points:[...selected.values()].sort(newest),min,max,byId};
}

/** Any valid table row may join the representative set without changing the full-period axes. */
export function historyChartSelection<T extends DatedRow>(chart:HistoryChartData<T>,id:string):T[]{
  const selected=chart.byId.get(id);
  return selected&&!chart.points.some(row=>row.id===id)?[...chart.points,selected].sort(newest):chart.points;
}
