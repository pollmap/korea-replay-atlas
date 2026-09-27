import manifestData from './data/property-poi/manifest.json';
import {nearbyPoiChunks,parsePoiChunk,poiSourceStates,type PoiCenter,type PoiManifest} from '../shared/property-poi';
import type {SurroundingsScope} from '../shared/property-surroundings';
import {fetchPinnedPoiJson} from './atlas-client';

const manifest=manifestData as PoiManifest;
const urls=import.meta.glob<string>('./data/property-poi/poi-*.json',{query:'?url&no-inline',import:'default',eager:true});
export const POI_SOURCE={...manifest.source,url:'https://www.openstreetmap.org/copyright'};
/** No country-wide request: only intersecting, hash-pinned cells, shared four-slot queue. */
export async function loadPropertyPoi(center:PoiCenter,scope:SurroundingsScope,signal:AbortSignal){
  const chunks=nearbyPoiChunks(manifest,center);
  const rows=await Promise.all(chunks.map(async chunk=>{
    const url=urls[`./data/property-poi/${chunk.file}`];
    if(!url)throw new Error('게시된 시설 파일을 찾지 못했습니다.');
    const value=await fetchPinnedPoiJson(url,{sha256:chunk.sha256,bytes:chunk.bytes},signal);
    signal.throwIfAborted();return parsePoiChunk(value,chunk,center,manifest.source);
  }));
  signal.throwIfAborted();return poiSourceStates(rows.flat(),scope,POI_SOURCE);
}
