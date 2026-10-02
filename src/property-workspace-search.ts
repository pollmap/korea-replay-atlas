import type {PropertyComplex,PropertyRegion} from '../shared/property';

type Region=Pick<PropertyRegion,'lawd_code'|'name'>;
type Complex=Pick<PropertyComplex,'id'|'name'|'legal_dong_name'|'lot_number'>;
export function workspaceSearch<R extends Region,C extends Complex>(regions:readonly R[],complexes:readonly C[],view:{query:string;propertyType:'apartment'|'officetel';region:string;dataRegion?:string;loading:boolean}){
  const query=view.query.normalize('NFC').trim().toLocaleLowerCase('ko-KR');
  if(!query)return {regions:[] as R[],complexes:[] as C[]};
  const matchingRegions=regions.filter(row=>row.name.normalize('NFC').toLocaleLowerCase('ko-KR').includes(query)).slice(0,5);
  const ready=view.propertyType==='apartment'&&!view.loading&&!!view.region&&view.dataRegion===view.region;
  return {regions:matchingRegions,complexes:ready?complexes.filter(row=>`${row.name} ${row.legal_dong_name??''} ${row.lot_number??''}`.normalize('NFC').toLocaleLowerCase('ko-KR').includes(query)).slice(0,8):[]};
}
