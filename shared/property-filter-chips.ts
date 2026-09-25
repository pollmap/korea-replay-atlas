import type {PropertyDiscoveryFilters} from './property-discovery';
import {rentKindLabel} from './property-rent';

export interface PropertyFilterChip {id:string;label:string;clear:Partial<PropertyDiscoveryFilters>;}
/** Show the actual bounds so an applied condition remains understandable when its editor is closed. */
export function propertyFilterChips(filters:PropertyDiscoveryFilters,trade:'sale'|'rent'):PropertyFilterChip[]{
  const chips:PropertyFilterChip[]=[];
  if(trade==='rent'&&filters.rentKind&&filters.rentKind!=='all')chips.push({id:'rentKind',label:rentKindLabel(filters.rentKind),clear:{rentKind:'all'}});
  if(filters.query)chips.push({id:'query',label:`검색 ${filters.query}`,clear:{query:''}});
  if(filters.dong)chips.push({id:'dong',label:filters.dong,clear:{dong:''}});
  const ranges=[
    ['price',trade==='sale'?'매매':'보증금','억','priceMinEok','priceMaxEok'],
    ['area','전용','㎡','areaMinM2','areaMaxM2'],
    ['year','건축','년','buildYearMin','buildYearMax'],
  ] as const;
  for(const [id,label,unit,lower,upper] of ranges){
    const min=filters[lower],max=filters[upper];
    if(!min&&!max)continue;
    const value=id==='area'&&min==='84'&&max==='84.99999'?'84㎡대':min&&max?`${min}–${max}${unit}`:min?`${min}${unit} 이상`:`${max}${unit} 이하`;
    chips.push({id,label:`${label} ${value}`,clear:{[lower]:'',[upper]:''}});
  }
  if(filters.hasTrades)chips.push({id:'hasTrades',label:'거래 있는 단지',clear:{hasTrades:false}});
  return chips;
}
