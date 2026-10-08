/** One release, bounded relationships, and explicit evidence. Never a valuation. */
export type PropertyEntityKind='complex'|'building'|'address'|'region'|'transaction'|'station'|'school'|'facility'|'development_plan'|'source';
export type RelationVerification='verified'|'unverified'|'inferred';
export type RelationPredicate='in_region'|'reported_address'|'reported_transaction'|'official_identity'|'contains_building'|'nearby_straight_line'|'related_plan'|'documented_by';
export interface PropertyRelationEvidence {
  id:string;source_id:string;source_url:string;source_sha256:string;record_id:string;method:string;
  observed_on:string;valid_from:string|null;valid_to:string|null;
  verification:RelationVerification;claim_type:'source'|'calculated'|'inferred';
}
export interface PropertyRelationNode {
  id:string;kind:PropertyEntityKind;label:string;attributes:Record<string,unknown>;evidence_ids:string[];
}
export interface PropertyRelationEdge {id:string;subject:string;predicate:RelationPredicate;object:string;evidence_id:string;}
export interface PropertyRelationGraph {
  schema_version:1;release_id:string;root_id:string;as_of:string;nodes:PropertyRelationNode[];
  edges:PropertyRelationEdge[];evidence:PropertyRelationEvidence[];truncated:boolean;
}
export type PropertyThemeField='households'|'parking'|'buildings'|'build_year'|'approved_year'|'parking_per_household';
export type PropertyThemeConditions=Partial<Record<PropertyThemeField,{min?:number;max?:number}>>;
export interface PropertyThemeResult {
  release_id:string;status:'ready'|'unavailable';conditions:PropertyThemeConditions;
  unavailable_fields:PropertyThemeField[];records:{id:string;label:string;evidence:(PropertyRelationEvidence&{field:string;numeric_value:number})[]}[];
  truncated:boolean;
}
const KINDS=new Set(['complex','building','address','region','transaction','station','school','facility','development_plan','source']);
const PREDICATES=new Set(['in_region','reported_address','reported_transaction','official_identity','contains_building','nearby_straight_line','related_plan','documented_by']);
const STATES=new Set(['verified','unverified','inferred']);
function object(value:unknown):value is Record<string,unknown>{return !!value&&typeof value==='object'&&!Array.isArray(value);}
function text(value:unknown,max=512):value is string{return typeof value==='string'&&!!value.trim()&&value.length<=max&&![...value].some(char=>char.charCodeAt(0)<32);}
function day(value:unknown):value is string{return typeof value==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value)&&Number.isFinite(Date.parse(value))&&new Date(value).toISOString().slice(0,10)===value;}
function fail():never{throw new Error('연결 정보의 출처와 버전을 확인하지 못했습니다.');}

/** Validate response context before rendering; reject broken references and future evidence. */
export function parsePropertyRelationGraph(value:unknown,releaseId:string,rootId:string):PropertyRelationGraph {
  if(!object(value)||value.schema_version!==1||value.release_id!==releaseId||!/^property-[a-f0-9]{16}$/.test(releaseId)
    ||value.root_id!==rootId||!text(rootId)||!day(value.as_of)||typeof value.truncated!=='boolean'
    ||!Array.isArray(value.nodes)||value.nodes.length>100||!Array.isArray(value.edges)||value.edges.length>300
    ||!Array.isArray(value.evidence)||value.evidence.length>512)return fail();
  const evidenceIds=new Set<string>();
  for(const row of value.evidence){
    if(!object(row)||!text(row.id)||!/^evidence:[a-f0-9]{64}$/.test(row.id)||evidenceIds.has(row.id)
      ||!text(row.source_id)||!text(row.record_id)||!text(row.method)||!text(row.source_url,2048)||!/^https:\/\/\S+$/.test(row.source_url)
      ||typeof row.source_sha256!=='string'||!/^[a-f0-9]{64}$/.test(row.source_sha256)||!day(row.observed_on)||row.observed_on>value.as_of
      ||!(row.valid_from===null||day(row.valid_from)&&row.valid_from<=value.as_of)
      ||!(row.valid_to===null||day(row.valid_to)&&row.valid_to>=value.as_of)
      ||!STATES.has(String(row.verification))||!['source','calculated','inferred'].includes(String(row.claim_type))
      ||(row.verification==='inferred')!==(row.claim_type==='inferred')||row.verification==='unverified')return fail();
    evidenceIds.add(row.id);
  }
  const nodeIds=new Set<string>();
  for(const node of value.nodes){
    if(!object(node)||!text(node.id)||nodeIds.has(node.id)||!KINDS.has(String(node.kind))||!text(node.label)||!object(node.attributes)
      ||JSON.stringify(node.attributes).length>8192||!Array.isArray(node.evidence_ids)||node.evidence_ids.length>8
      ||new Set(node.evidence_ids).size!==node.evidence_ids.length||!node.evidence_ids.every(id=>typeof id==='string'&&evidenceIds.has(id)))return fail();
    nodeIds.add(node.id);
  }
  if(value.nodes.length&&!nodeIds.has(rootId))return fail();
  const edgeIds=new Set<string>();
  for(const edge of value.edges){
    if(!object(edge)||!text(edge.id)||!/^relation:[a-f0-9]{64}$/.test(edge.id)||edgeIds.has(edge.id)
      ||!PREDICATES.has(String(edge.predicate))||typeof edge.subject!=='string'||typeof edge.object!=='string'
      ||!nodeIds.has(edge.subject)||!nodeIds.has(edge.object)||edge.subject===edge.object
      ||typeof edge.evidence_id!=='string'||!evidenceIds.has(edge.evidence_id))return fail();
    edgeIds.add(edge.id);
  }
  return value as unknown as PropertyRelationGraph;
}
