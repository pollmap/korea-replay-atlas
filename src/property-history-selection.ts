export const HISTORY_TABLE_PAGE_SIZE=48;
export interface HistoryTableState {scope:string;start:number;expanded:boolean;}
/** A new query/filter starts collapsed; a shorter result cannot leave an empty stale page. */
export function historyTableWindow(state:HistoryTableState,scope:string,total:number){
  const count=Math.max(0,Math.floor(total)),matching=state.scope===scope;
  const expanded=matching&&state.expanded;
  const last=Math.floor(Math.max(0,count-1)/HISTORY_TABLE_PAGE_SIZE)*HISTORY_TABLE_PAGE_SIZE;
  const requested=matching&&Number.isFinite(state.start)?Math.max(0,state.start):0;
  const start=expanded?Math.min(last,Math.floor(requested/HISTORY_TABLE_PAGE_SIZE)*HISTORY_TABLE_PAGE_SIZE):0;
  return {scope,start,expanded,end:Math.min(count,start+(expanded?HISTORY_TABLE_PAGE_SIZE:12))};
}
/** Match source row IDs, never dates/prices, and jump to a bounded page. */
export function historyRecordWindow(rows:readonly {id:string}[],id:string,state:HistoryTableState,scope:string):HistoryTableState|null {
  const index=rows.findIndex(row=>row.id===id);
  if(index<0)return null;
  const current=historyTableWindow(state,scope,rows.length);
  return index>=current.start&&index<current.end?current:{scope,start:Math.floor(index/HISTORY_TABLE_PAGE_SIZE)*HISTORY_TABLE_PAGE_SIZE,expanded:true};
}
export function historySelectionIndex(points:readonly {id:string}[],id:string|undefined):number {
  return id===undefined?-1:points.findIndex(row=>row.id===id);
}
