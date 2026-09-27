export const SURROUNDINGS_CATEGORIES=[['transport','교통'],['school','학교'],['life','생활'],['development','개발계획']] as const;
export type SurroundingsCategory=typeof SURROUNDINGS_CATEGORIES[number][0];
export const SURROUNDINGS_RADII=[500,1000,3000] as const;
export type SurroundingsRadius=typeof SURROUNDINGS_RADII[number];
export const SURROUNDINGS_TYPES={
  transport:[['subway','지하철'],['bus','버스'],['rail','철도'],['road','도로']],
  school:[['elementary','초등학교'],['middle','중학교'],['high','고등학교'],['university','대학교']],
  life:[['shopping','장보기'],['medical','의료'],['park','공원'],['public','공공시설']],
  development:[['housing','주택'],['transport_plan','교통'],['industrial','산업'],['urban','도시정비']],
} as const;
export type SurroundingsType=typeof SURROUNDINGS_TYPES[SurroundingsCategory][number][0];
export const DEVELOPMENT_STAGES={planned:'계획',announced:'고시',permitted:'인허가',construction:'착공',completed:'준공'} as const;
export interface SurroundingsScope {complexId:string;releaseId:string;}
export interface SurroundingsSource {id:string;label:string;url:string;asOf:string;}
interface SurroundingsRecordBase {
  id:string;name:string;address?:string;
  /** Measured straight-line distance from this exact complex; never an ETA. */
  distanceMeters:number|null;source:SurroundingsSource;
  development?:{stage:keyof typeof DEVELOPMENT_STAGES;effectiveDate:string;documentUrl:string};
}
export type SurroundingsRecord={[C in SurroundingsCategory]:SurroundingsRecordBase&{category:C;type:typeof SURROUNDINGS_TYPES[C][number][0]}}[SurroundingsCategory];
export type SurroundingsSourceState=
  |{status:'unconnected'}
  |{status:'loading';scope:SurroundingsScope}
  |{status:'error';scope:SurroundingsScope}
  |{status:'ready';scope:SurroundingsScope;category:SurroundingsCategory;coverageRadius:SurroundingsRadius;complete:boolean;records:readonly SurroundingsRecord[];source:SurroundingsSource};
export interface SurroundingsFilter {category:SurroundingsCategory;radius:SurroundingsRadius;type:SurroundingsType|'all';sort:'distance'|'name';}
export const DEFAULT_SURROUNDINGS_FILTER:SurroundingsFilter={category:'transport',radius:1000,type:'all',sort:'distance'};
export type SurroundingsFilterAction={category:SurroundingsCategory}|{radius:SurroundingsRadius}|{type:SurroundingsType|'all'}|{sort:'distance'|'name'};
export function surroundingsFilterChange(filter:SurroundingsFilter,change:SurroundingsFilterAction):SurroundingsFilter{
  const next={...filter,...change,...('category' in change?{type:'all' as const}:{})};
  if(!SURROUNDINGS_CATEGORIES.some(([id])=>id===next.category)||!SURROUNDINGS_RADII.includes(next.radius)||!['distance','name'].includes(next.sort))throw new Error('invalid_surroundings_filter');
  if(next.type!=='all'&&!SURROUNDINGS_TYPES[next.category].some(([id])=>id===next.type))throw new Error('invalid_surroundings_type');
  return next;
}
export const surroundingsRadiusLabel=(radius:number)=>radius===500?'500m':`${radius/1000}km`;
export function surroundingsSourceUrl(value:string):string|undefined {
  try{const url=new URL(value);return ['https:','http:'].includes(url.protocol)&&!url.username&&!url.password?url.href:undefined;}catch{return undefined;}
}
export interface SurroundingsView {state:'unconnected'|'loading'|'error'|'empty'|'ready'|'partial';records:SurroundingsRecord[];count:number|null;unknownDistances:number;source?:SurroundingsSource;}
/** Only a complete, matching source covering the radius can establish a real empty result. */
export function surroundingsView(source:SurroundingsSourceState|undefined,scope:SurroundingsScope,filter:SurroundingsFilter):SurroundingsView {
  const unknown=(state:SurroundingsView['state']):SurroundingsView=>({state,records:[],count:null,unknownDistances:0});
  if(!source||source.status==='unconnected')return unknown('unconnected');
  if(source.scope.complexId!==scope.complexId||source.scope.releaseId!==scope.releaseId)return unknown('unconnected');
  if(source.status!=='ready')return unknown(source.status);
  if(source.category!==filter.category)return unknown('unconnected');
  const ids=new Set<string>();
  for(const row of source.records){
    if(ids.has(row.id)||row.category!==source.category||!SURROUNDINGS_TYPES[source.category].some(([id])=>id===row.type))return unknown('error');
    ids.add(row.id);
  }
  const candidates=source.records.filter(row=>row.category===filter.category&&(filter.type==='all'||row.type===filter.type));
  const unknownDistances=candidates.filter(row=>row.distanceMeters===null||!Number.isFinite(row.distanceMeters)||row.distanceMeters<0).length;
  const records=candidates.filter(row=>row.distanceMeters!==null&&Number.isFinite(row.distanceMeters)&&row.distanceMeters>=0&&row.distanceMeters<=filter.radius);
  const names=(a:SurroundingsRecord,b:SurroundingsRecord)=>a.name.localeCompare(b.name,'ko',{numeric:true})||a.id.localeCompare(b.id);
  records.sort((a,b)=>filter.sort==='name'?names(a,b):a.distanceMeters!-b.distanceMeters!||names(a,b));
  const complete=source.complete&&source.coverageRadius>=filter.radius&&unknownDistances===0;
  return {state:complete?(records.length?'ready':'empty'):'partial',records,count:complete?records.length:null,unknownDistances,source:source.source};
}
