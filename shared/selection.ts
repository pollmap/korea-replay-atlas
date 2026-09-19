import type {Evidence,Provenance} from './contracts';

export interface MapSelection {
  name:string;detail:string;provenance?:Partial<Provenance>;height?:number;
  sourceId?:string;properties?:Record<string,unknown>;rawHeight?:number|null;
  qualityState?:string;qualityFlags?:string[];heightSemantics?:string;
}
const EVIDENCE=new Set<Evidence>(['observation','official_record','source_attribute','schedule','calculation','estimate','unverified']);
const firstText=(...values:unknown[])=>values.find((value):value is string=>typeof value==='string'&&value.trim().length>0);
export function finiteProperty(value:unknown):number|undefined {
  if(typeof value==='number')return Number.isFinite(value)?value:undefined;
  if(typeof value==='string'&&value.trim()) {const number=Number(value);return Number.isFinite(number)?number:undefined;}
  return undefined;
}
function flagsProperty(value:unknown):string[]{
  if(typeof value==='string'){try{return flagsProperty(JSON.parse(value));}catch{return [];}}
  return Array.isArray(value)?value.filter((flag):flag is string=>typeof flag==='string'):[];
}
/** The public display contract takes precedence over retained source height. */
export function displayedBuildingHeight(p:Record<string,unknown>):number|undefined {
  if(p.render_eligible===false||p.render_eligible==='False'||p.render_eligible==='false')return undefined;
  const height=finiteProperty('render_height' in p?p.render_height:p.height);
  return height!==undefined&&height>0?height:undefined;
}
export function selectedProperties(id:string,p:Record<string,unknown>,fallback:{source_id:string;version:string}):MapSelection {
  const quality='render_height' in p||'quality_state' in p;
  const evidence=EVIDENCE.has(p.evidence_type as Evidence)?p.evidence_type as Evidence:'unverified';
  const source=firstText(p.height_source,p.source_id,fallback.source_id)!;
  return {
    sourceId:id,properties:p,
    name:typeof p.name==='string'&&p.name?p.name:'지도 객체',
    detail:typeof p.description==='string'&&p.description?p.description:'원천 자료의 형상과 속성입니다.',
    height:quality?displayedBuildingHeight(p):finiteProperty(p.height),
    provenance:(p.provenance&&typeof p.provenance==='object'?p.provenance as Partial<Provenance>:undefined)??{
      source_id:source,source_record_id:firstText(typeof p.source_record_id==='number'?String(p.source_record_id):p.source_record_id,id),
      dataset_version:source==='ghsl'?'2018 / R2023A':firstText(p.dataset_version,fallback.version),evidence_type:evidence,
    },
    ...(quality?{rawHeight:finiteProperty(p.raw_height)??null,qualityState:String(p.quality_state??'review_required'),qualityFlags:flagsProperty(p.quality_flags),heightSemantics:String(p.height_semantics??'unresolved')}:{})
  };
}

export const QUALITY_NOTICES:Record<string,string>={
  height_unknown:'원천 높이가 없습니다.',height_invalid:'원천 높이를 확인해야 합니다.',
  source_height_below_1m_review:'원천 높이가 1m 미만이라 확인이 필요합니다.',
  height_semantics_unresolved:'높이가 건물 상단인지 두께인지 확인되지 않았습니다.',
  bottom_not_below_top_review:'건물 하단과 상단 값이 충돌합니다.',
  ellipsoidal_base_below_minus_30m_review:'해안 지표고를 독립 자료와 비교 중입니다.',
  ground_height_invalid:'지표고를 확인해야 합니다.',
  ghsl_2018_grid_mean_estimate:'2018년 100m 격자 평균 높이로 표현했습니다.',
  floor_height_estimate:'층수로 추정한 높이입니다.',
  upstream_schema_semantics_conflict:'해당 버전의 OSM 원천값과 높이 의미를 대조했습니다.',
};
