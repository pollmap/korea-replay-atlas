export interface CommuteDestination {label:string;address:string;}
export const COMMUTE_STORAGE_KEY='korea-replay:commute:v1';
const clean=(value:unknown,max:number):value is string=>typeof value==='string'&&value.trim().length>0&&value.length<=max&&[...value].every(char=>char.charCodeAt(0)>=32);
export function parseCommuteDestinations(raw:string|null):CommuteDestination[]{
  if(!raw||raw.length>4096)return [];
  try {
    const value:unknown=JSON.parse(raw);
    if(!value||typeof value!=='object'||!('version' in value)||value.version!==1||!('items' in value)||!Array.isArray(value.items))return [];
    return value.items.filter((item):item is CommuteDestination=>!!item&&typeof item==='object'&&clean(item.label,40)&&clean(item.address,200))
      .slice(0,3).map(item=>({label:item.label.trim(),address:item.address.trim()}));
  }catch{return [];}
}
export function commuteUrl(origin:string,destination:string,mode:'transit'|'driving'|'walking'):string|null {
  if(!clean(origin,400)||!clean(destination,200)||!['transit','driving','walking'].includes(mode))return null;
  const params=new URLSearchParams({api:'1',origin:origin.trim(),destination:destination.trim(),travelmode:mode});
  return `https://www.google.com/maps/dir/?${params}`;
}
