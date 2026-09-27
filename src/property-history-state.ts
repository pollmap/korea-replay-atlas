import type {PropertyRegionDetail,PropertyPartition} from '../shared/property';
import {historyMonths,type HistoryRange} from '../shared/property-history';
import type {HistoryResult} from './property-history-loader';

/** Publication state and browser download state are deliberately separate. */
function historyReadStatus(result:HistoryResult|undefined):string {
  if(!result)return '불러오는 중';
  if(result.status==='ready')return '자료 확인';
  if(result.status==='error')return '조회 실패 · 재시도 가능';
  if(result.reason==='download_budget')return '다운로드 한도 · 기간을 나눠 조회';
  if(result.reason==='request_budget')return '요청 수 한도 · 기간을 나눠 조회';
  if(result.reason==='retention_budget')return '기록 수 한도 · 기간을 나눠 조회';
  if(result.reason==='before_source'||result.reason==='source_unavailable')return '원천 자료 제공 전';
  if(result.reason==='failed')return '원천 수집 실패';
  if(result.reason==='partial')return '일부만 수집';
  if(result.reason==='outside_release')return '이 버전에 미게시';
  return '수집 대기';
}

const refreshLabels={pending:'갱신 대기',partial:'갱신 일부 확인',failed:'갱신 실패'} as const;
export function historyMonthStatus(result:HistoryResult|undefined,partition?:PropertyPartition):string {
  const status=historyReadStatus(result);
  return partition?.refresh&&['complete','empty'].includes(partition.status)
    ?`${status} · ${refreshLabels[partition.refresh.status]} · 이전 확인본 유지 · 마지막 성공(UTC) ${partition.retrieved_at?.slice(0,10)??'미확인'}`:status;
}

/** The source's last successful snapshot and latest collection attempt are different dates. */
export function historyRefreshSummary(detail:PropertyRegionDetail,trade:'sale'|'rent',months:readonly string[]) {
  const selected=new Set(months),retained=detail.partitions.filter(p=>p.trade_type===trade&&selected.has(p.deal_month)&&p.refresh&&['complete','empty'].includes(p.status));
  const dates=retained.map(p=>p.retrieved_at).filter((v):v is string=>!!v).sort();
  return {count:retained.length,firstSuccess:dates[0]?.slice(0,10)??null,lastSuccess:dates.at(-1)?.slice(0,10)??null};
}

export function publishedHistoryCoverage(detail:PropertyRegionDetail,trade:'sale'|'rent',months:readonly string[]) {
  const available=detail.partitions.filter(row=>row.trade_type===trade&&['complete','empty'].includes(row.status)).map(row=>row.deal_month).sort();
  const selected=new Set(months);
  return {first:available[0]??null,last:available.at(-1)??null,count:available.filter(month=>selected.has(month)).length};
}

/** Compare only identical, fully read months. A missing month never becomes zero. */
export function commonHistoryMonths(months:readonly string[],histories:Readonly<Record<string,readonly HistoryResult[]>>,codes:readonly string[]) {
  if(!codes.length)return [];
  const ready=codes.map(code=>new Set((histories[code]??[]).filter(row=>row.status==='ready').map(row=>row.month)));
  return months.filter(month=>ready.every(set=>set.has(month)));
}

export function shiftHistoryEnd(end:string,range:HistoryRange,direction:-1|1):string {
  historyMonths(end,range); // Validate the shared contract before constructing a date.
  const date=new Date(Date.UTC(Number(end.slice(0,4)),Number(end.slice(4))-1+range*direction,1));
  return `${date.getUTCFullYear()}${String(date.getUTCMonth()+1).padStart(2,'0')}`;
}
