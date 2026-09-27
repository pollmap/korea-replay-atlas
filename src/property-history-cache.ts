import type {PropertyPartition,PropertyTransaction} from '../shared/property';

export function historyMonthCacheKey(origin:string,release:string,code:string,partition:PropertyPartition,complexes:ReadonlySet<string>):string {
  return JSON.stringify([origin,release,code,partition.deal_month,partition.trade_type,[...complexes].sort(),partition.status,partition.source_rows,partition.eligible_rows,partition.retrieved_at,partition.transactions]);
}

/** Only fully validated selected-complex rows are retained, never regional source packets.
 * Bytes bound the UTF-8 serialized selection plus its key, not measured JavaScript heap.
 */
export class HistoryMonthCache {
  private entries=new Map<string,{rows:PropertyTransaction[];bytes:number}>();
  private bytes=0;private rows=0;
  constructor(private readonly limits={bytes:4*1024*1024,rows:20_000,months:256}){}
  get(key:string):PropertyTransaction[]|undefined {
    const entry=this.entries.get(key);if(!entry)return;
    this.entries.delete(key);this.entries.set(key,entry);
    // Consumers may sort/remove their own array; cached report records stay immutable.
    return [...entry.rows];
  }
  set(key:string,rows:readonly PropertyTransaction[]):void {
    const bytes=new TextEncoder().encode(key+JSON.stringify(rows)).byteLength;
    if(bytes>this.limits.bytes||rows.length>this.limits.rows||this.limits.months<1)return;
    const old=this.entries.get(key);if(old){this.entries.delete(key);this.bytes-=old.bytes;this.rows-=old.rows.length;}
    while(this.entries.size&&(this.entries.size>=this.limits.months||this.bytes+bytes>this.limits.bytes||this.rows+rows.length>this.limits.rows)){
      const [id,entry]=this.entries.entries().next().value!;this.entries.delete(id);this.bytes-=entry.bytes;this.rows-=entry.rows.length;
    }
    const owned=rows.map(row=>Object.freeze({...row,issues:Object.freeze(row.issues.map(issue=>Object.freeze({...issue}))) as unknown as PropertyTransaction['issues']}));
    this.entries.set(key,{rows:owned,bytes});this.bytes+=bytes;this.rows+=owned.length;
  }
  snapshot(){return {months:this.entries.size,rows:this.rows,serializedBytes:this.bytes};}
}
