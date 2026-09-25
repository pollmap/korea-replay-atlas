/** This product's explicit 84 m² band; never a supplied-area or legal housing definition. */
export const NATIONAL_AREA = '84-band';
export const M2_PER_PYEONG = 400 / 121;
export function areaMatches(value:string|null,filter:string):boolean {
  if(!filter)return true;
  if(value===null)return false;
  return filter===NATIONAL_AREA?Number(value)>=84&&Number(value)<85:value===filter;
}
export function areaLabel(value:string):string{return value===NATIONAL_AREA?'국평 · 전용 84㎡대':`${value}㎡`;}
export function exclusivePyeong(value:string|null):string {
  const area=Number(value);
  return value!==null&&Number.isFinite(area)&&area>0?(area/M2_PER_PYEONG).toLocaleString('ko-KR',{maximumFractionDigits:1}):'—';
}
