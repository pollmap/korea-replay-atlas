import {parsePropertyTransactions,type PropertyRegionDetail,type PropertySource,type PropertyTransaction} from '../shared/property';
import {historyPlan,type HistoryRange} from '../shared/property-history';
import {historySourceStart} from '../shared/property-source-period';
import {fetchPinnedJson} from './atlas-client';
import {HistoryMonthCache,historyMonthCacheKey} from './property-history-cache';

const historyCaches=new WeakMap<typeof fetchPinnedJson,HistoryMonthCache>();

export interface HistoryResult {month:string;status:'ready'|'missing'|'error';rows:PropertyTransaction[];reason?:string;}
export const HISTORY_LIMITS={bytes:24*1024*1024,requests:512,rows:20_000} as const;
export interface HistoryBudget {remaining:number;requestsRemaining?:number;rowsRemaining?:number;}
/** Two month jobs at a time; the shared network gate still enforces four global slots. */
export async function loadPropertyHistory({detail,end,count,trade,complex,origin,signal,onMonth,fetchJson=fetchPinnedJson,budget={remaining:HISTORY_LIMITS.bytes},sources}:{detail:PropertyRegionDetail;end:string;count:HistoryRange;trade:'sale'|'rent';complex:string|readonly string[];origin:string;signal:AbortSignal;onMonth:(result:HistoryResult)=>void;fetchJson?:typeof fetchPinnedJson;budget?:HistoryBudget;sources?:readonly PropertySource[]}){
  let cache=historyCaches.get(fetchJson);if(!cache){cache=new HistoryMonthCache();historyCaches.set(fetchJson,cache);}
  const plan=historyPlan(detail,end,count,trade).reverse();
  const sourceStart=historySourceStart(trade,sources);
  budget.requestsRemaining??=HISTORY_LIMITS.requests;budget.rowsRemaining??=HISTORY_LIMITS.rows;
  const complexIds=new Set(typeof complex==='string'?[complex]:complex);
  let next=0;
  const run=async()=>{while(next<plan.length&&!signal.aborted){const {month,partition}=plan[next++];
    if(sourceStart&&month<sourceStart){onMonth({month,status:'missing',rows:[],reason:'before_source'});continue;}
    if(!partition||!['complete','empty'].includes(partition.status)){onMonth({month,status:'missing',rows:[],reason:partition?.status??'outside_release'});continue;}
    if(budget.rowsRemaining===0&&partition.source_rows!==0){onMonth({month,status:'missing',rows:[],reason:'retention_budget'});continue;}
    const bytes=partition.transactions.reduce((sum,ref)=>sum+ref.bytes,0);
    if(bytes>budget.remaining){onMonth({month,status:'missing',rows:[],reason:'download_budget'});continue;}
    if(partition.transactions.length>budget.requestsRemaining!){onMonth({month,status:'missing',rows:[],reason:'request_budget'});continue;}
    budget.remaining-=bytes;
    budget.requestsRemaining!-=partition.transactions.length;
    try{
      const cacheKey=historyMonthCacheKey(origin,detail.release_id,detail.lawd_code,partition,complexIds),cached=cache.get(cacheKey);
      if(cached){
        if(cached.length>budget.rowsRemaining!)throw new Error('retention_budget');
        budget.rowsRemaining!-=cached.length;onMonth({month,status:'ready',rows:cached});continue;
      }
      const kept:PropertyTransaction[]=[];let sourceCount=0;const ids=new Set<string>();
      for(const ref of partition.transactions){
        const data=parsePropertyTransactions(await fetchJson(ref,origin,signal));
        if(signal.aborted)return;
        if(data.release_id!==detail.release_id||data.lawd_code!==detail.lawd_code||data.deal_month!==month)throw new Error('history_scope_mismatch');
        for(const row of data.transactions){if(row.trade_type!==trade)continue;if(ids.has(row.id))throw new Error('history_partition_mismatch');ids.add(row.id);sourceCount++;if(row.complex_id&&complexIds.has(row.complex_id)){if(kept.length>=budget.rowsRemaining!)throw new Error('retention_budget');kept.push(row);}}
      }
      if(sourceCount!==partition.source_rows)throw new Error('history_row_count_mismatch');
      if(kept.length>budget.rowsRemaining!)throw new Error('retention_budget');
      if(!signal.aborted){cache.set(cacheKey,kept);budget.rowsRemaining!-=kept.length;onMonth({month,status:'ready',rows:kept});}
    }catch(error){if(signal.aborted)return;const reason=error instanceof Error?error.message:'history_load_error';onMonth({month,status:reason==='retention_budget'?'missing':'error',rows:[],reason});}
  }};
  await Promise.all([run(),run()]);
}
