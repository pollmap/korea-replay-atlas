/** Explicit exclusive-area filters. A range is inclusive at both endpoints;
 * the national 84 band remains [84,85), never an invented supplied area. */
export const NATIONAL_AREA = '84-band';
export const M2_PER_PYEONG = 400 / 121;
const DECIMAL=/^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,5})?$/;
const validBound=(s:string)=>!s||DECIMAL.test(s)&&Number(s)>=0&&Number(s)<=10000;
export function areaRangeBounds(filter:string):{min:string;max:string}|null {
  const p=filter.split(':');
  if(p.length!==3||p[0]!=='range'||!p[1]&&!p[2]||!validBound(p[1])||!validBound(p[2])||p[1]&&p[2]&&Number(p[1])>Number(p[2]))return null;
  return {min:p[1],max:p[2]};
}
export function areaFromBounds(min:string,max:string):string {
  if(min==='84'&&max==='84.99999')return NATIONAL_AREA;
  if(!min&&!max)return '';
  if(!validBound(min)||!validBound(max)||min&&max&&Number(min)>Number(max))return '';
  if(min&&min===max&&Number(min)>0)return String(Number(min));
  return `range:${min?String(Number(min)):''}:${max?String(Number(max)):''}`;
}
export function validAreaFilter(value:string):boolean {
  return value===''||value===NATIONAL_AREA||areaRangeBounds(value)!==null||DECIMAL.test(value)&&Number(value)>0&&Number(value)<=10000;
}
export function areaMatches(value:string|null,filter:string):boolean {
  if(!filter)return true;
  if(value===null)return false;
  const range=areaRangeBounds(filter),area=Number(value);
  if(range)return Number.isFinite(area)&&(!range.min||area>=Number(range.min))&&(!range.max||area<=Number(range.max));
  return filter===NATIONAL_AREA?area>=84&&area<85:validAreaFilter(filter)&&value===filter;
}
export function areaLabel(value:string):string {
  if(value===NATIONAL_AREA)return '국평 · 전용 84㎡대';
  const range=areaRangeBounds(value);
  return range?range.min&&range.max?`전용 ${range.min}–${range.max}㎡`:range.min?`전용 ${range.min}㎡ 이상`:`전용 ${range.max}㎡ 이하`:`${value}㎡`;
}
export function exclusivePyeong(value:string|null):string {
  const area=Number(value);
  return value!==null&&Number.isFinite(area)&&area>0?(area/M2_PER_PYEONG).toLocaleString('ko-KR',{maximumFractionDigits:1}):'—';
}
