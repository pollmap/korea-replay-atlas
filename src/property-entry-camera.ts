import type {Place} from '../shared/contracts';
import type {FlatCamera} from '../shared/map2d';
import type {AtlasContent} from './atlas-loader';
import {confirmedPropertyNavigationPoint,type PropertyMapPoint} from '../shared/property-map-point';
import {regionNavigationPlace} from './region-navigation';

/** Resolve only audited locations from the exact selected release. Explicit shared cameras win. */
export function propertyEntryCamera(href:string,atlas:AtlasContent,fallback:Place,flat:FlatCamera|null,point?:PropertyMapPoint|null):{place:Place;camera:FlatCamera|null}{
  const params=new URLSearchParams(new URL(href).hash.slice(1));
  if(flat||params.has('position'))return {place:fallback,camera:flat};
  const verified=confirmedPropertyNavigationPoint(point,atlas.property.release_id,params.get('complex')??'');
  if(verified)return {place:{id:verified.complexId,name:'선택한 단지',region:params.get('regionCode')??'',lon:verified.longitude,lat:verified.latitude,range:1000},camera:[verified.longitude,verified.latitude,15,0]};
  const region=atlas.regions.regions.find(row=>row.lawd_code===params.get('regionCode'));
  return {place:regionNavigationPlace(region??{name:params.get('regionQuery')??''},atlas.map)??fallback,camera:null};
}
