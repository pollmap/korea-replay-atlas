import type {BBox,Source} from './contracts';

export interface MapTileFile {url:string;sha256:string;byte_length:number;}
export interface MapTileChunk extends MapTileFile {first_tile_id:number;last_tile_id:number;tile_count:number;}
export interface MapDetailChunk extends MapTileFile {first_id:string;last_id:string;record_count:number;}
export interface MapTileTopic {
  id:string;source_layer:string;minzoom:number;maxzoom:number;feature_count:number;
  bounds:BBox;chunks:MapTileChunk[];details:MapDetailChunk[];
  description:string;geometry_precision:string;
  /** Display keys are collision-audited prefixes; selection records keep full hashes. */
  display_id_hex_length?:16|64;
  /** Fixed low-zoom partitions share the original building selection archive. */
  detail_topic_id?:'buildings';
}
export interface MapCatalog2D {
  schema_version:1;release_id:string;bounds:BBox;topics:MapTileTopic[];sources:Source[];
  source_release_id:string;reference_dates:Record<string,string>;attribution:string;
  regional_detail?:{regions:{name:string;sgis_code:string;native_geometry_sha256:string}[];reference_date:string;topics:string[];source_assets_complete:boolean;selection_rule:string;coverage_claim:string};
}
export interface MapTileRecord {stable_id:string;source_record_id:string;source_id:string;version:string;properties:Record<string,unknown>;geometry_sha256:string;source_asset_id:string;}
export const MAP_TILE_LIMIT=1024*1024;
const hex=/^[a-f0-9]{64}$/;
const requireValue=(value:unknown,message:string):void=>{if(!value)throw new Error(message);};
export const validMapTileId=(value:unknown):value is string=>typeof value==='string'&&/^[a-z][a-z0-9-]{0,47}$/.test(value);
function bounds(value:unknown):value is BBox {return Array.isArray(value)&&value.length===4&&value.every(Number.isFinite)&&value[0]>=-180&&value[2]<=180&&value[1]>=-85.1&&value[3]<=85.1&&value[0]<value[2]&&value[1]<value[3];}
/** Only immutable same-release paths are accepted; origins are separately pinned by the caller. */
export function validateMapCatalog2D(value:unknown):MapCatalog2D {
  requireValue(value&&typeof value==='object','2D catalog is not an object');const c=value as MapCatalog2D;
  requireValue(c.schema_version===1&&/^map2d-[a-f0-9]{20,64}$/.test(c.release_id)&&bounds(c.bounds),'Invalid 2D catalog identity');
  requireValue(Array.isArray(c.topics)&&c.topics.length>0&&c.topics.length<=32&&Array.isArray(c.sources),'Invalid 2D topics');
  requireValue(typeof c.source_release_id==='string'&&typeof c.attribution==='string'&&c.reference_dates&&typeof c.reference_dates==='object','Missing 2D provenance');
  const topics=new Set<string>(),urls=new Set<string>();let files=0;
  const file=(f:MapTileFile,extension:string)=>{
    requireValue(f&&typeof f.url==='string'&&f.url.startsWith(`/data/map-tiles/${c.release_id}/`)&&/^\/data\/[a-zA-Z0-9_./-]+$/.test(f.url)&&!f.url.split('/').some(p=>p==='.'||p==='..')&&f.url.endsWith(extension),'Invalid 2D asset path');
    requireValue(hex.test(f.sha256)&&Number.isSafeInteger(f.byte_length)&&f.byte_length>0&&f.byte_length<=MAP_TILE_LIMIT,'Invalid 2D asset hash or size');
    requireValue(!urls.has(f.url),'Duplicate 2D asset path');urls.add(f.url);files++;
  };
  for(const t of c.topics){
    requireValue(validMapTileId(t.id)&&!topics.has(t.id)&&validMapTileId(t.source_layer)&&bounds(t.bounds),'Invalid 2D topic');topics.add(t.id);
    requireValue(Number.isInteger(t.minzoom)&&Number.isInteger(t.maxzoom)&&t.minzoom>=0&&t.maxzoom<=16&&t.minzoom<=t.maxzoom&&Number.isSafeInteger(t.feature_count)&&t.feature_count>=0,'Invalid 2D zoom/count');
    requireValue(Array.isArray(t.chunks)&&Array.isArray(t.details)&&typeof t.description==='string'&&typeof t.geometry_precision==='string','Invalid 2D topic metadata');
    requireValue(t.display_id_hex_length===undefined||t.display_id_hex_length===16||t.display_id_hex_length===64,'Invalid display identity policy');
    let last=-1;for(const f of t.chunks){file(f,'.pmtiles');requireValue(Number.isSafeInteger(f.first_tile_id)&&Number.isSafeInteger(f.last_tile_id)&&f.first_tile_id>last&&f.last_tile_id>=f.first_tile_id&&Number.isSafeInteger(f.tile_count)&&f.tile_count>0,'Overlapping or invalid tile ranges');last=f.last_tile_id;}
    let lastId='';let count=0;for(const f of t.details){file(f,'.json.gz');requireValue(hex.test(f.first_id)&&hex.test(f.last_id)&&f.first_id>lastId&&f.last_id>=f.first_id&&Number.isSafeInteger(f.record_count)&&f.record_count>0,'Overlapping or invalid detail ranges');lastId=f.last_id;count+=f.record_count;}
    if(t.detail_topic_id!==undefined){
      requireValue(t.detail_topic_id==='buildings'&&/^buildings-overview-[0-3]$/.test(t.id)&&t.details.length===0&&t.minzoom===12&&t.maxzoom===13,'Invalid 2D detail alias');
    }else requireValue(count===t.feature_count,'2D detail records do not cover every source feature');
  }
  const aliases=c.topics.filter(t=>t.detail_topic_id!==undefined);
  if(aliases.length){
    const original=c.topics.find(t=>t.id==='buildings');
    requireValue(original&&original.detail_topic_id===undefined&&original.minzoom===14&&aliases.reduce((sum,t)=>sum+t.feature_count,0)===original.feature_count&&aliases.every(t=>t.display_id_hex_length===original.display_id_hex_length),'Invalid 2D building partition coverage');
  }
  if(c.regional_detail!==undefined){
    const detail=c.regional_detail;
    requireValue(detail&&Array.isArray(detail.regions)&&detail.regions.length>0&&detail.regions.length<=17,'Invalid regional detail scope');
    const codes=new Set<string>();for(const region of detail.regions){requireValue(region&&typeof region.name==='string'&&region.name.length>0&&region.name.length<=50&&/^\d{2}$/.test(region.sgis_code)&&!codes.has(region.sgis_code)&&hex.test(region.native_geometry_sha256),'Invalid regional detail region');codes.add(region.sgis_code);}
    requireValue(/^\d{4}-\d{2}-\d{2}$/.test(detail.reference_date)&&Array.isArray(detail.topics)&&detail.topics.length>0&&new Set(detail.topics).size===detail.topics.length&&detail.topics.every(t=>topics.has(t))&&detail.source_assets_complete===true&&typeof detail.selection_rule==='string'&&typeof detail.coverage_claim==='string','Invalid regional detail provenance');
  }
  requireValue(files<=18000,'2D file budget exceeded');return c;
}
export function mapCoverageLabel(catalog:MapCatalog2D):string {
  return catalog.regional_detail?`전국 기본 지도 · ${catalog.regional_detail.regions.map(r=>r.name).join('·')} 상세`:`전국 벡터 지도 · ${catalog.topics.length}개 주제`;
}
export function findTileChunk(topic:MapTileTopic,id:number):MapTileChunk|undefined {
  let lo=0,hi=topic.chunks.length-1;while(lo<=hi){const mid=(lo+hi)>>>1,c=topic.chunks[mid];if(id<c.first_tile_id)hi=mid-1;else if(id>c.last_tile_id)lo=mid+1;else return c;}return undefined;
}
export function findDetailChunk(topic:MapTileTopic,id:string):MapDetailChunk|undefined {
  if(!hex.test(id)&&!(topic.display_id_hex_length===16&&/^[a-f0-9]{16}$/.test(id)))return undefined;
  let lo=0,hi=topic.details.length-1;while(lo<=hi){const mid=(lo+hi)>>>1,c=topic.details[mid];if(id<c.first_id.slice(0,id.length))hi=mid-1;else if(id>c.last_id.slice(0,id.length))lo=mid+1;else return c;}return undefined;
}
