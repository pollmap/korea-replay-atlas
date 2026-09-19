import type {SearchEntry} from '../shared/search';
import type {SearchManifestV2,SearchShard} from '../shared/search-v2';
export function buildSearchFiles(entries:SearchEntry[],options?:{candidate_count?:number;source_sha256?:string}):{
  files:Map<string,Uint8Array>;
  manifest:SearchManifestV2&{source_sha256:string};
  descriptor:SearchShard;
};
export function writeSearchRelease(catalogPath?:string):Promise<Record<string,unknown>>;
export function auditSearchRelease():Promise<Record<string,unknown>>;
export function benchmarkSearchCpu(iterations?:number,responseMode?:string):Promise<Record<string,unknown>>;
