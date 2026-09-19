import {useEffect,useState} from 'react';
import {validateMapCatalog2D,type MapCatalog2D} from '../shared/map-tiles';
import {parsePropertyRelease,parsePropertyRegions,type PropertyRelease,type PropertyRegions} from '../shared/property';
import type {RuntimeV2} from '../shared/runtime-v2';
import {fetchAtlas,fetchPinnedJson} from './atlas-client';

export interface AtlasContent {origin:string;map:MapCatalog2D;property:PropertyRelease;regions:PropertyRegions;}
export function useAtlas(runtime:RuntimeV2|null,checked:boolean,runtimeError=''){
  const [result,setResult]=useState<{state:'loading'|'ready'|'unavailable'|'error';content:AtlasContent|null;error:string}>({state:'loading',content:null,error:''});
  useEffect(()=>{
    if(!checked)return;
    if(runtimeError){setResult({state:'error',content:null,error:runtimeError});return;}
    const controller=new AbortController();
    void(async()=>{
      const atlas=await fetchAtlas(runtime,controller.signal);
      if(!atlas){if(!controller.signal.aborted)setResult({state:'unavailable',content:null,error:''});return;}
      const [rawMap,rawProperty]=await Promise.all([fetchPinnedJson(atlas.manifest.map_catalog,atlas.origin,controller.signal),fetchPinnedJson(atlas.manifest.property_release,atlas.origin,controller.signal)]);
      const map=validateMapCatalog2D(rawMap),property=parsePropertyRelease(rawProperty);
      if(map.release_id!==atlas.manifest.map_catalog.release_id||property.release_id!==atlas.manifest.property_release.release_id)throw new Error('지도·부동산 자료의 버전이 일치하지 않습니다.');
      const requested=new URLSearchParams(location.hash.slice(1)).get('propertyRelease');
      if(requested&&requested!==property.release_id)throw new Error('공유된 부동산 자료 버전이 이 배포에 없습니다. 최신 자료로 대체하지 않습니다.');
      const regions=parsePropertyRegions(await fetchPinnedJson(property.regions,atlas.origin,controller.signal));
      if(regions.release_id!==property.release_id)throw new Error('지역 목록의 자료 버전이 다릅니다.');
      if(!controller.signal.aborted)setResult({state:'ready',content:{origin:atlas.origin,map,property,regions},error:''});
    })().catch(error=>{if(!controller.signal.aborted)setResult({state:'error',content:null,error:error instanceof Error?error.message:'지역 자료를 불러오지 못했습니다.'});});
    return()=>controller.abort();
  },[runtime,checked,runtimeError]);
  return result;
}
