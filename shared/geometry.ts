/** Transferable render geometry. Coordinates retain source precision and order. */
export type GeoProperties=Record<string,unknown>;
export interface GeoFeature {type:'Feature';id?:string|number;properties:GeoProperties|null;geometry:GeoGeometry|null;}
export interface GeoGeometry {type:string;coordinates?:unknown;geometries?:GeoGeometry[];}
export interface GeoMetadata {schema_version:number;shared:GeoProperties;rows:GeoProperties[];}
export interface GeoCollection {type:'FeatureCollection';features:GeoFeature[];metadata?:GeoMetadata;}
export interface GeometryPart {kind:'point'|'line'|'polygon';feature:number;offset:number;count:number;rings?:number[];}
export interface RenderFeature {id:string;properties:GeoProperties;}
export interface RenderGeometry {coordinates:Float64Array;parts:GeometryPart[];features:RenderFeature[];vertexCount:number;metadata?:GeoMetadata;}
const RENDER_PROPERTY_KEYS=['kind','name','class','highway','height','render_height','render_eligible','base_height','min_height','render_min_height'] as const;
const owns=(properties:GeoProperties|undefined,key:string)=>properties!==undefined&&Object.prototype.hasOwnProperty.call(properties,key);
function propertyRow(properties:GeoProperties,metadata:GeoMetadata):GeoProperties|undefined {
  return properties.metadata_index===undefined?undefined:metadata.rows[Number(properties.metadata_index)];
}
/** Restore complete source attributes only when inspected; never mutate the shared dictionary. */
export function resolveFeatureProperties(feature:RenderFeature,metadata?:GeoMetadata):GeoProperties {
  if(!metadata)return feature.properties;
  const properties={...metadata.shared,...propertyRow(feature.properties,metadata),...feature.properties};
  delete properties.metadata_index;
  return properties;
}
/** Preserve explicit null/undefined fields: their presence controls the public height contract. */
export function renderFeatureProperties(feature:RenderFeature,metadata?:GeoMetadata):GeoProperties {
  if(!metadata)return feature.properties;
  const raw=feature.properties,row=propertyRow(raw,metadata),result:GeoProperties={};
  for(const key of RENDER_PROPERTY_KEYS){
    if(owns(raw,key))result[key]=raw[key];
    else if(owns(row,key))result[key]=row![key];
    else if(owns(metadata.shared,key))result[key]=metadata.shared[key];
  }
  return result;
}
/** Older catalogs lack exact counts. Their estimate is an admission ceiling, never permission to overrun it. */
export function geometryVertexEstimate(asset:{vertex_count?:number;count:number}):number {
  const value=asset.vertex_count??asset.count*16;
  return Number.isSafeInteger(value)&&value>=0?value:Infinity;
}
export function assertGeometryVertexBudget(data:Pick<RenderGeometry,'vertexCount'>,asset:{vertex_count?:number;count:number}):void {
  const expected=geometryVertexEstimate(asset);
  if(!Number.isFinite(expected)||!Number.isSafeInteger(data.vertexCount)||data.vertexCount<0||data.vertexCount>expected)throw new Error(`공간 정점 예산 불일치 (${data.vertexCount}/${expected}). 자료 목록 갱신이 필요합니다.`);
}

export function compileGeometry(data:GeoCollection,omitRoads=false,options:{preserveMetadata?:boolean}={}):RenderGeometry {
  if(data?.type!=='FeatureCollection'||!Array.isArray(data.features))throw new Error('공간 자료 형식이 올바르지 않습니다.');
  const metadata=data.metadata;
  if(metadata&&(metadata.schema_version!==1||!Array.isArray(metadata.rows)||!metadata.shared))throw new Error('공간 속성 사전 형식이 올바르지 않습니다.');
  const preserveMetadata=!!(metadata&&options.preserveMetadata);
  const referencedRows=preserveMetadata?new Set<number>():undefined;
  const values:number[]=[],parts:GeometryPart[]=[],features:RenderFeature[]=[];
  const positions=(input:unknown):number=>{
    if(!Array.isArray(input))throw new Error('공간 좌표가 배열이 아닙니다.');
    const start=values.length/3;
    for(const point of input){
      if(!Array.isArray(point)||point.length<2||!point.every(Number.isFinite)||Math.abs(point[0])>180||Math.abs(point[1])>90)throw new Error('유효하지 않은 공간 좌표입니다.');
      values.push(point[0],point[1],point[2]??0);
    }
    return values.length/3-start;
  };
  const geometry=(g:GeoGeometry,feature:number)=>{
    const offset=values.length/3;
    switch(g.type){
      case 'Point':positions([g.coordinates]);parts.push({kind:'point',feature,offset,count:1});break;
      case 'MultiPoint':for(const point of g.coordinates as unknown[])geometry({type:'Point',coordinates:point},feature);break;
      case 'LineString':{const count=positions(g.coordinates);if(count<2)throw new Error('선 좌표가 부족합니다.');parts.push({kind:'line',feature,offset,count});break;}
      case 'MultiLineString':for(const line of g.coordinates as unknown[])geometry({type:'LineString',coordinates:line},feature);break;
      case 'Polygon':{const rings=(g.coordinates as unknown[]).map(ring=>{const count=positions(ring);if(count<4)throw new Error('다각형 좌표가 부족합니다.');return count;});if(!rings.length)throw new Error('다각형 외곽선이 없습니다.');parts.push({kind:'polygon',feature,offset,count:rings.reduce((a,b)=>a+b,0),rings});break;}
      case 'MultiPolygon':for(const polygon of g.coordinates as unknown[])geometry({type:'Polygon',coordinates:polygon},feature);break;
      case 'GeometryCollection':for(const child of g.geometries??[])geometry(child,feature);break;
      default:throw new Error(`지원하지 않는 공간 형식: ${g.type}`);
    }
  };
  for(let i=0;i<data.features.length;i++){
    const f=data.features[i];if(!f.geometry)continue;
    const raw=f.properties??{},row=raw.metadata_index;
    if(row!==undefined&&(!metadata||!Number.isInteger(row)||Number(row)<0||Number(row)>=metadata.rows.length))throw new Error('공간 속성 행 번호가 올바르지 않습니다.');
    const properties=metadata&&!preserveMetadata?{...metadata.shared,...(row===undefined?{}:metadata.rows[Number(row)]),...raw}:raw;
    if(metadata&&!preserveMetadata)delete properties.metadata_index;
    const record=preserveMetadata&&row!==undefined?metadata!.rows[Number(row)]:undefined;
    const property=(key:string)=>!preserveMetadata||owns(raw,key)?properties[key]:owns(record,key)?record![key]:metadata!.shared[key];
    if(omitRoads&&['road','ferry_route'].includes(String(property('kind'))))continue;
    if(row!==undefined)referencedRows?.add(Number(row));
    const id=String(f.id??property('source_record_id')??`feature-${i}`),index=features.length;
    features.push({id,properties});geometry(f.geometry,index);
  }
  let transferredMetadata=preserveMetadata?metadata:undefined;
  if(transferredMetadata&&referencedRows!.size<transferredMetadata.rows.length){
    // Far views may keep only a few facilities. Do not clone the omitted roads' records.
    const remap=new Map<number,number>(),rows:GeoProperties[]=[];
    for(const row of referencedRows!){remap.set(row,rows.length);rows.push(transferredMetadata.rows[row]);}
    for(const feature of features){
      const row=feature.properties.metadata_index;
      if(row===undefined)continue;
      const mapped=remap.get(Number(row))!;
      if(mapped!==row)feature.properties={...feature.properties,metadata_index:mapped};
    }
    transferredMetadata={...transferredMetadata,rows};
  }
  return {coordinates:Float64Array.from(values),parts,features,vertexCount:values.length/3,...(transferredMetadata?{metadata:transferredMetadata}:{})};
}
