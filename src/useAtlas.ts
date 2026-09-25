import {useEffect,useState} from 'react';
import {validateMapCatalog2D,type MapCatalog2D} from '../shared/map-tiles';
import {parsePropertyRelease,parsePropertyRegions,type PropertyRelease,type PropertyRegions} from '../shared/property';
import {assertPinnedDeploymentV2,type RuntimeV2} from '../shared/runtime-v2';
import {fetchAtlas,fetchPinnedJson} from './atlas-client';
import {observeAtlasSelection,type AtlasSelection} from './atlas-selection';

export interface AtlasContent {origin:string;map:MapCatalog2D;property:PropertyRelease;regions:PropertyRegions;}
export function useAtlas(runtime:RuntimeV2|null,checked:boolean,runtimeError=''){
  const [result,setResult]=useState<AtlasSelection<AtlasContent>>({state:'loading',content:null,error:''});
  useEffect(()=>{
    if(!checked)return;
    if(runtimeError){setResult({state:'error',content:null,error:runtimeError});return;}
    return observeAtlasSelection({events:window,readUrl:()=>location.href,replaceUrl:url=>location.replace(url),publish:setResult,validateDeployment:url=>{
      if(url.searchParams.getAll('deployment').length>1)throw new Error('공유 링크의 배포 버전이 중복 지정되어 있습니다.');
      if(runtime)assertPinnedDeploymentV2(url.searchParams.get('deployment'),runtime,url.origin);
    },load:async signal=>{
      const atlas=await fetchAtlas(runtime,signal);
      if(!atlas)return null;
      const [rawMap,rawProperty]=await Promise.all([fetchPinnedJson(atlas.manifest.map_catalog,atlas.origin,signal),fetchPinnedJson(atlas.manifest.property_release,atlas.origin,signal)]);
      const map=validateMapCatalog2D(rawMap),property=parsePropertyRelease(rawProperty);
      if(map.release_id!==atlas.manifest.map_catalog.release_id||property.release_id!==atlas.manifest.property_release.release_id)throw new Error('지도·부동산 자료의 버전이 일치하지 않습니다.');
      const regions=parsePropertyRegions(await fetchPinnedJson(property.regions,atlas.origin,signal));
      if(regions.release_id!==property.release_id)throw new Error('지역 목록의 자료 버전이 다릅니다.');
      return {origin:atlas.origin,map,property,regions};
    }});
  },[runtime,checked,runtimeError]);
  return result;
}
