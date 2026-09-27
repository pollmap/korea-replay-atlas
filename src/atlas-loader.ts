import {validateMapCatalog2D,type MapCatalog2D} from '../shared/map-tiles';
import {parsePropertyRelease,parsePropertyRegions,type PropertyRelease,type PropertyRegions} from '../shared/property';
import {fetchPinnedJson,type AtlasDescriptor} from './atlas-client';

export interface AtlasContent {origin:string;map:MapCatalog2D;property:PropertyRelease;regions:PropertyRegions;}

/** Start region metadata as soon as its verified manifest arrives, independent of map bytes. */
export async function loadAtlasContent(atlas:AtlasDescriptor,parentSignal:AbortSignal,read:typeof fetchPinnedJson=fetchPinnedJson):Promise<AtlasContent>{
  parentSignal.throwIfAborted();
  const controller=new AbortController(),signal=controller.signal,abort=()=>controller.abort();
  parentSignal.addEventListener('abort',abort,{once:true});
  try{
    const [map,{property,regions}]=await Promise.all([
      (async()=>{
        const map=validateMapCatalog2D(await read(atlas.manifest.map_catalog,atlas.origin,signal));
        if(map.release_id!==atlas.manifest.map_catalog.release_id)throw new Error('지도·부동산 자료의 버전이 일치하지 않습니다.');
        return map;
      })(),
      (async()=>{
        const property=parsePropertyRelease(await read(atlas.manifest.property_release,atlas.origin,signal));
        if(property.release_id!==atlas.manifest.property_release.release_id)throw new Error('지도·부동산 자료의 버전이 일치하지 않습니다.');
        signal.throwIfAborted();
        const regions=parsePropertyRegions(await read(property.regions,atlas.origin,signal));
        if(regions.release_id!==property.release_id)throw new Error('지역 목록의 자료 버전이 다릅니다.');
        return {property,regions};
      })(),
    ]);
    // The caller receives no partial atlas, including when a cancelled read finishes late.
    signal.throwIfAborted();
    return {origin:atlas.origin,map,property,regions};
  }catch(error){
    // Release this loader's sibling subscription; other consumers may still need the shared request.
    controller.abort();throw error;
  }finally{parentSignal.removeEventListener('abort',abort);}
}
