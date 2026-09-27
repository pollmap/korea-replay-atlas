import {parsePropertyRegionDetail,type PropertyComplex} from '../shared/property';
import {historyMonths,type HistoryRange} from '../shared/property-history';
import {historySourceStart} from '../shared/property-source-period';
import {fetchPinnedJson} from './atlas-client';
import {HISTORY_LIMITS,loadPropertyHistory,type HistoryResult} from './property-history-loader';
import type {AtlasContent} from './useAtlas';

export async function loadComparisonHistory({atlas,items,month,range,trade,signal,fetchJson=fetchPinnedJson}:{atlas:AtlasContent;items:readonly PropertyComplex[];month:string;range:HistoryRange;trade:'sale'|'rent';signal:AbortSignal;fetchJson?:typeof fetchPinnedJson}) {
  const codes=[...new Set(items.map(item=>item.lawd_code))].sort();
  if(items.length>3)throw new Error('최대 3개 단지를 비교할 수 있습니다.');
  const months=historyMonths(month,range);
  const sourceStart=historySourceStart(trade,atlas.property.sources);
  const histories:Record<string,HistoryResult[]>={};
  if(signal.aborted)return histories;
  await Promise.all(codes.map(async code=>{
    const results:HistoryResult[]=[];
    try {
      if(sourceStart&&month<sourceStart){histories[code]=months.map(month=>({month,status:'missing',rows:[],reason:'before_source'}));return;}
      const region=atlas.regions.regions.find(row=>row.lawd_code===code);
      if(!region)throw new Error('비교 지역이 이 자료 버전에 없습니다.');
      const detail=parsePropertyRegionDetail(await fetchJson(region.index,atlas.origin,signal));
      if(detail.lawd_code!==code||detail.release_id!==atlas.property.release_id)throw new Error('비교 지역 자료 버전 불일치');
      await loadPropertyHistory({detail,end:month,count:range,trade,complex:items.filter(item=>item.lawd_code===code).map(item=>item.id),origin:atlas.origin,signal,onMonth:result=>results.push(result),fetchJson,sources:atlas.property.sources,budget:{remaining:Math.floor(HISTORY_LIMITS.bytes/codes.length),requestsRemaining:Math.floor(HISTORY_LIMITS.requests/codes.length),rowsRemaining:Math.floor(HISTORY_LIMITS.rows/codes.length)}});
    } catch(error) {
      if(signal.aborted)return;
      for(const value of months)results.push(sourceStart&&value<sourceStart?{month:value,status:'missing',rows:[],reason:'before_source'}:{month:value,status:'error',rows:[],reason:error instanceof Error?error.message:'comparison_load_error'});
    }
    if(!signal.aborted)histories[code]=results;
  }));
  return histories;
}
