import type {PropertyDiscoveryFilters} from '../shared/property-discovery';
import {areaRangeBounds,NATIONAL_AREA} from '../shared/property-area';
import type {RentKind} from '../shared/property-rent';

/** Search, markers and detail share the explicit view conditions; the budget
 * qualifies a candidate's latest contract and never clips its history rows. */
export function generalPropertyFilters(base:PropertyDiscoveryFilters,options:{area?:string;latestPrice?:{min:string;max:string};dong?:string;rentKind?:RentKind}):PropertyDiscoveryFilters {
  const {area,latestPrice,dong,rentKind}=options,range=area?areaRangeBounds(area):null;
  return {...base,...(latestPrice?{priceMinEok:latestPrice.min,priceMaxEok:latestPrice.max}:{}),
    ...(area!==undefined?{areaMinM2:area?(range?.min??(area===NATIONAL_AREA?'84':area)):'',areaMaxM2:area?(range?.max??(area===NATIONAL_AREA?'84.99999':area)):''}:{}),
    ...(dong!==undefined?{dong}:{}),...(rentKind!==undefined?{rentKind}:{})};
}

export function propertyBudgetLabel(min:string,max:string):string {
  return min&&max?`${min}–${max}억`:min?`${min}억 이상`:max?`${max}억 이하`:'예산';
}

export type PropertyFilterPanel='region'|'price'|'area'|'period'|'more';
/** A pending edit belongs to the exact view in which it was opened. */
export function propertyFilterDraftContext(value:{region:string;trade:'sale'|'rent';month:string;range:number;filters:PropertyDiscoveryFilters}):string {
 const f=value.filters;
 return JSON.stringify([value.region,value.trade,value.month,value.range,f.query,f.dong,f.sort,
   f.priceMinEok,f.priceMaxEok,f.areaMinM2,f.areaMaxM2,f.buildYearMin,f.buildYearMax,!!f.hasTrades,f.rentKind??'all']);
}
export function currentPropertyFilterPanel(panel:PropertyFilterPanel|null,openedContext:string,currentContext:string):PropertyFilterPanel|null {
 // Region and period selectors apply immediately and stay open for the next
 // hierarchical selection. Budget, area and more are explicit pending edits.
 return panel==='region'||panel==='period'||openedContext===currentContext?panel:null;
}
