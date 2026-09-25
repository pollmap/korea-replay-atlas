import {parsePropertyTransactions,type PropertyRegionDetail,type PropertyTransaction} from '../shared/property';
import {historyPlan,type HistoryRange} from '../shared/property-history';
import {fetchPinnedJson} from './atlas-client';

export interface HistoryResult {month:string;status:'ready'|'missing'|'error';rows:PropertyTransaction[];reason?:string;}
/** Two month jobs at a time; the shared network gate still enforces four global slots. */
export async function loadPropertyHistory({detail,end,count,trade,complex,origin,signal,onMonth,fetchJson=fetchPinnedJson,budget={remaining:24*1024*1024}}:{detail:PropertyRegionDetail;end:string;count:HistoryRange;trade:'sale'|'rent';complex:string|readonly string[];origin:string;signal:AbortSignal;onMonth:(result:HistoryResult)=>void;fetchJson?:typeof fetchPinnedJson;budget?:{remaining:number}}){
  const plan=historyPlan(detail,end,count,trade).reverse();
  const complexIds=new Set(typeof complex==='string'?[complex]:complex);
  let next=0;
  const run=async()=>{while(next<plan.length&&!signal.aborted){const {month,partition}=plan[next++];
    if(!partition||!['complete','empty'].includes(partition.status)){onMonth({month,status:'missing',rows:[],reason:partition?.status??'outside_release'});continue;}
    const bytes=partition.transactions.reduce((sum,ref)=>sum+ref.bytes,0);
    if(bytes>budget.remaining){onMonth({month,status:'missing',rows:[],reason:'download_budget'});continue;}
    budget.remaining-=bytes;
    try{
      const kept:PropertyTransaction[]=[];let sourceCount=0;const ids=new Set<string>();
      for(const ref of partition.transactions){
        const data=parsePropertyTransactions(await fetchJson(ref,origin,signal));
        if(signal.aborted)return;
        if(data.release_id!==detail.release_id||data.lawd_code!==detail.lawd_code||data.deal_month!==month)throw new Error('history_scope_mismatch');
        for(const row of data.transactions){if(row.trade_type!==trade)continue;if(ids.has(row.id))throw new Error('history_partition_mismatch');ids.add(row.id);sourceCount++;if(row.complex_id&&complexIds.has(row.complex_id))kept.push(row);}
      }
      if(sourceCount!==partition.source_rows)throw new Error('history_row_count_mismatch');
      if(!signal.aborted)onMonth({month,status:'ready',rows:kept});
    }catch(error){if(signal.aborted)return;onMonth({month,status:'error',rows:[],reason:error instanceof Error?error.message:'history_load_error'});}
  }};
  await Promise.all([run(),run()]);
}
