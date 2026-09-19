export type Evidence = 'observation' | 'official_record' | 'source_attribute' | 'schedule' | 'calculation' | 'estimate' | 'unverified';
export type LayerId = 'terrain' | 'buildings' | 'infrastructure' | 'rail' | 'bus' | 'depth' | 'satellite' | 'radar' | 'sun';
export type BBox = [number, number, number, number];
export interface Source {
  id: string; title: string; url: string; license: string; description: string;
}
export interface Provenance {
  source_id: string; source_record_id: string; dataset_version: string;
  observed_at: string | null; retrieved_at: string; evidence_type: Evidence;
  input_hash: string; transform_version: string; unit?: string; vertical_datum?: string;
}
export interface Asset {
  id: string; layer: LayerId; url: string; format: 'geojson' | '3d-tiles' | 'quantized-mesh' | 'replay' | 'imagery' | 'search-index' | 'asset-index';
  bbox: BBox; source_id: string; version: string; count: number;
  from?: string; to?: string; cadence_seconds?: number; sha256: string; label?: string;
  detail_level?:'detail'|'overview'; min_camera_height?:number; max_camera_height?:number;
  byte_length?:number; vertex_count?:number; feature_count?:number;
  /** count/feature_count include rendered parts; these retain logical source identity. */
  logical_source_building_count?:number; building_parts_parent_count?:number;
  building_parts_feature_count?:number; building_parts_publication_version?:string;
  geometric_error_policy?:string;
}
export interface LayerStatus {
  id: LayerId; label: string; state: 'ready' | 'partial' | 'unavailable';
  reason: string | null; source_ids: string[]; record_count: number;
  from: string | null; to: string | null; updated_at: string | null;
}
export interface Catalog {
  schema_version: 1; release_id: string; generated_at: string | null;
  base_date: string | null; sources: Source[]; layers: LayerStatus[]; assets: Asset[];
  coverage_url?:string;
  live_transit_routes?:import('./transit-catalog').TransitCatalogRef;
}
/** Published v2 keeps spatial file lists outside the small initial manifest. */
export interface CatalogV2 extends Omit<Catalog,'schema_version'> {
  schema_version:2; indexes:Asset[]; legacy_url:string;
}
export interface SpatialAssetIndex {schema_version:1; assets:Asset[];}
export interface HeightQuality {
  raw_height:number|null; render_height:number|null;
  height_semantics:'ground_to_top'|'bottom_to_top'|'unresolved';
  height_method:string; quality_flags:string[];
}
export interface Place { id: string; name: string; region: string; lon: number; lat: number; range: number; }
export interface TrackPoint { time: string; lon: number; lat: number; height?: number; }
export interface Track {
  id: string; label: string; layer: 'bus' | 'rail'; provenance: Provenance;
  points: TrackPoint[]; max_gap_seconds: number; position_evidence: Evidence;
  description?: string;
}
export interface ReplayChunk { schema_version: 1; tracks: Track[]; }
export interface WeatherFrame {time:string;valid_until:string;url:string;bbox:BBox;unit:string;source_id:string;evidence_type:Evidence;}
export interface WeatherManifest {schema_version:1;frames:WeatherFrame[];}
export const EVIDENCE_LABEL: Record<Evidence, string> = {
  observation: '관측', official_record: '공식 기록', source_attribute: '원천 속성', schedule: '시간표',
  calculation: '계산 위치', estimate: '추정', unverified: '미확인',
};
