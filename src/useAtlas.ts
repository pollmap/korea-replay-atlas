import {useEffect,useState} from 'react';
import {assertPinnedDeploymentV2,type RuntimeV2} from '../shared/runtime-v2';
import {fetchAtlas} from './atlas-client';
import {loadAtlasContent,type AtlasContent} from './atlas-loader';
import {observeAtlasSelection,type AtlasSelection} from './atlas-selection';

export type {AtlasContent} from './atlas-loader';
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
      return loadAtlasContent(atlas,signal);
    }});
  },[runtime,checked,runtimeError]);
  return result;
}
