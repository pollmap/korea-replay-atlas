import type {PropertyTransaction} from './property';
export type RentKind='all'|'jeonse'|'monthly';
export const RENT_KINDS:readonly [RentKind,string][]=[['all','전월세 전체'],['jeonse','전세'],['monthly','월세']];
export function readRentKind(value:unknown):RentKind{return value==='jeonse'||value==='monthly'?value:'all';}
/** Only an explicitly reported zero monthly payment is classified as jeonse. */
export function rentKindMatches(row:PropertyTransaction,kind:RentKind='all'):boolean{
  if(row.trade_type!=='rent'||kind==='all')return true;
  const monthly=row.monthly_rent_krw;
  if(monthly===null||!Number.isFinite(monthly)||monthly<0)return false;
  return kind==='jeonse'?monthly===0:monthly>0;
}
export function rentKindLabel(kind:RentKind='all'):string{return RENT_KINDS.find(([key])=>key===kind)![1];}
