import type {Place} from './contracts';
import {PLACES} from './sources';
/** Keep pinned versions and apartment conditions when retiring old map modes. */
export function readApartmentEntry(href:string){
  const url=new URL(href),params=new URLSearchParams(url.hash.slice(1));
  let place:Place=PLACES.find(value=>value.id===params.get('place'))??PLACES[0];
  const coordinates=params.get('position')?.split(',').map(Number);
  if(coordinates?.length===3&&coordinates.every(Number.isFinite)&&coordinates[0]>=124&&coordinates[0]<=132.5&&coordinates[1]>=32&&coordinates[1]<=39.5&&coordinates[2]>=500&&coordinates[2]<=2500000)place={id:params.get('place')?.slice(0,180)||'shared',name:params.get('name')?.slice(0,100)||'공유한 장소',region:params.get('region')?.slice(0,100)||'',lon:coordinates[0],lat:coordinates[1],range:coordinates[2]};
  const versions=url.searchParams.getAll('deployment'),parsed=Date.parse(params.get('time')??'');
  return {place,region:params.get('regionCode')??'',release:params.get('release'),deployment:versions.length>1?'invalid':versions[0]??null,time:Number.isFinite(parsed)?parsed:Date.now(),retired:params.get('view')==='3d'||['sun','replay','live'].includes(params.get('mode')??'')};
}
