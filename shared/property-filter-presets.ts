import type {PropertyDiscoveryFilters} from './property-discovery';

export interface PropertyFilterPreset {id:string;label:string;conditions:string;patch:Partial<PropertyDiscoveryFilters>;}
/** Store concrete conditions, not a theme ID whose meaning changes with the clock. */
export function propertyFilterPresets(month?:string):PropertyFilterPreset[]{
  const national={areaMinM2:'84',areaMaxM2:'84.99999'};
  const result:PropertyFilterPreset[]=[{id:'national-price',label:'국평 · 낮은 가격순',conditions:'전용 84㎡대 · 최근 거래금액 낮은순',patch:{...national,sort:'price-low'}}];
  if(month&&/^\d{4}(0[1-9]|1[0-2])$/.test(month)){
    const year=Number(month.slice(0,4));
    if(year>=1900&&year<=9999)result.push({id:'national-recent-build',label:'국평 · 최근 10년 건축',conditions:`전용 84㎡대 · ${year-10}–${year}년 건축`,patch:{...national,buildYearMin:String(year-10),buildYearMax:String(year)}});
  }
  return result;
}
export function applyPropertyFilterPreset(filters:PropertyDiscoveryFilters,preset:PropertyFilterPreset):PropertyDiscoveryFilters{return {...filters,...preset.patch};}
export function propertyFilterPresetActive(filters:PropertyDiscoveryFilters,preset:PropertyFilterPreset){return Object.entries(preset.patch).every(([key,value])=>filters[key as keyof PropertyDiscoveryFilters]===value);}
