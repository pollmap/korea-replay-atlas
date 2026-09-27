import type {PropertySource,PropertyTradeType} from './property';

/** MOLIT publication scope, not a claim that this release has collected these months.
 * https://rt.molit.go.kr/pt/info/info.do?mobileAt=
 * Rent includes fixed-date reports from 2011 and lease reports from June 2021.
 */
const SOURCE_START={sale:{id:'molit-apt-sale-detail',dataset:'15126468',month:'200601'},rent:{id:'molit-apt-rent',dataset:'15126474',month:'201101'}} as const;

export function historySourceStart(trade:PropertyTradeType,sources:readonly PropertySource[]=[]):string|undefined {
  const rule=SOURCE_START[trade];
  return sources.some(source=>source.id===rule.id&&source.dataset_id===rule.dataset&&source.page_url===`https://www.data.go.kr/data/${rule.dataset}/openapi.do`&&source.evidence_type==='official_report')?rule.month:undefined;
}

export function historySourceCoverage(months:readonly string[],start:string|undefined){
  const before=start?months.filter(month=>month<start).length:0;
  return {before,eligible:months.length-before};
}
