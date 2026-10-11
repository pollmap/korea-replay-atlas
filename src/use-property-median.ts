import {useEffect,useMemo,useState} from 'react';
import type {PropertyRegionDetail,PropertySource} from '../shared/property';
import type {PriceBasis} from '../shared/property-pricing';
import type {HistoryRange} from '../shared/property-history';
import {historySourceStart} from '../shared/property-source-period';
import {loadPropertyHistory,type HistoryResult} from './property-history-loader';
import type {ComplexPriceSummary,SummaryPartition} from './property-map-prices';
import {monthlyMedianPoints} from './property-monthly-median';
import type {SummarySelection} from './property-monthly-summary';

/** Legacy summaries have no mergeable distributions. Read only unresolved
 * months within the existing shared, bounded history budget; never block the map. */
export function usePropertyMedian({rows,partitions,selection,basis,detail,origin,range,end,sources,enabled=true}:{rows:readonly ComplexPriceSummary[];partitions:readonly SummaryPartition[];selection:SummarySelection;basis:PriceBasis;detail:PropertyRegionDetail;origin?:string;range:HistoryRange;end:string;sources?:readonly PropertySource[];enabled?:boolean}){
  const sourceStart=historySourceStart(selection.trade,sources);
  const key=JSON.stringify([detail.release_id,origin,selection.complexId,selection.trade,selection.rentKind,selection.area,selection.months,basis]);
  const initial=useMemo(()=>monthlyMedianPoints(rows,partitions,selection,basis),[rows,partitions,selection,basis]);
  const unresolved=initial.filter(point=>point.needsRaw&&(!sourceStart||point.month>=sourceStart)).map(point=>point.month).join(',');
  const [loaded,setLoaded]=useState<{key:string;results:HistoryResult[];done:boolean}>({key:'',results:[],done:false});
  const [attempt,setAttempt]=useState(0);
  useEffect(()=>{
    if(!enabled||!origin||!unresolved)return;
    const controller=new AbortController();setLoaded({key,results:[],done:false});
    void loadPropertyHistory({detail,end,count:range,trade:selection.trade,complex:selection.complexId,origin,sources,signal:controller.signal,months:unresolved.split(','),onMonth:result=>setLoaded(previous=>previous.key===key?{...previous,results:[...previous.results,result]}:previous)})
      .then(()=>{if(!controller.signal.aborted)setLoaded(previous=>previous.key===key?{...previous,done:true}:previous);})
      .catch(()=>{if(!controller.signal.aborted)setLoaded(previous=>previous.key===key?{...previous,done:true}:previous);});
    return()=>controller.abort();
  },[enabled,origin,unresolved,key,detail,end,range,selection.trade,selection.complexId,sources,attempt]);
  const results=useMemo(()=>loaded.key===key?loaded.results:[],[loaded,key]);
  const points=useMemo(()=>monthlyMedianPoints(rows,partitions,selection,basis,results).map(point=>sourceStart&&point.month<sourceStart?{...point,count:null,latest:null,median:null,needsRaw:false,status:'source_unavailable'}:point),[rows,partitions,selection,basis,results,sourceStart]);
  const loading=!!(enabled&&origin&&unresolved&&(loaded.key!==key||!loaded.done));
  return {points,loading,retry:()=>setAttempt(value=>value+1),unresolved:points.filter(point=>point.count!==null&&point.count>0&&point.median===null&&!point.mixedRent).length};
}
