/** Match source row IDs, never dates/prices: identical contracts may coexist. */
export function historyRecordLimit(rows:readonly {id:string}[],id:string,current:number):number|null {
  const index=rows.findIndex(row=>row.id===id);
  return index<0?null:Math.max(current,index+1);
}
export function historySelectionIndex(points:readonly {id:string}[],id:string|undefined):number {
  return id===undefined?-1:points.findIndex(row=>row.id===id);
}
