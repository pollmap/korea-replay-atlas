import {describe,expect,it} from 'vitest';
import type {Asset,LayerId} from '../shared/contracts';
import {selectViewAssets} from '../shared/map-performance';
import {reconcileSceneAssets,type SceneAssetResult} from '../src/scene-assets';

const release='pub-0123456789abcdef',nextRelease='pub-1111111111111111';
const core:Asset={id:'buildings',layer:'buildings',format:'3d-tiles',url:'/data/root.json',bbox:[124,32,133,39],detail_level:'overview',count:1000,source_id:'test',version:'1',sha256:'core'};
const shared:Asset={...core,id:'road-shared',layer:'infrastructure',format:'geojson',detail_level:'detail',url:'/data/shared.geojson',bbox:[127,36,127.2,36.2],count:1,vertex_count:10,byte_length:100,sha256:'shared'};
const far:Asset={...shared,id:'road-far',bbox:[129,36,129.2,36.2],sha256:'far'};
const added:Asset={...shared,id:'road-next',sha256:'next'};
const previous:SceneAssetResult={releaseId:release,assets:[core,shared,far]};
const layers:Record<LayerId,boolean>={terrain:true,buildings:true,infrastructure:true,rail:false,bus:false,depth:false,radar:false,satellite:false,sun:true};

describe('progressive scene asset reconciliation',()=>{
  it('keeps shared geometry and its identity through a new view core-only callback',()=>{
    const state=reconcileSceneAssets(previous,release,[core],false,new AbortController().signal);
    expect(state).toBe(previous);
    const selection=selectViewAssets(state.assets,{bbox:[127,36,127.1,36.1],height:3000,layers,mode:'sun',dayStart:0,dayEnd:86400000,quality:'low'});
    expect(selection.assets.map(asset=>asset.id)).toEqual(['buildings','road-shared']);
    expect(selection.assets).toContain(shared);expect(selection.assets).not.toContain(far);
  });
  it('merges partial descriptors once by ID while preserving unresolved same-release neighbors',()=>{
    const state=reconcileSceneAssets(previous,release,[core,shared,added],false,new AbortController().signal);
    expect(state.assets).toEqual([core,shared,far,added]);expect(new Set(state.assets.map(asset=>asset.id)).size).toBe(4);
  });
  it('replaces the accumulated list with the exact new list only on completion',()=>{
    const signal=new AbortController().signal,partial=reconcileSceneAssets(previous,release,[core,added],false,signal);
    const exact=[core,added],complete=reconcileSceneAssets(partial,release,exact,true,signal);
    expect(complete.assets).toBe(exact);expect(complete.assets).not.toContain(shared);expect(complete.assets).not.toContain(far);
  });
  it('never merges old-release descriptors into a new-release partial response',()=>{
    const nextCore={...core,sha256:'new-core'},state=reconcileSceneAssets(previous,nextRelease,[nextCore],false,new AbortController().signal);
    expect(state).toEqual({releaseId:nextRelease,assets:[nextCore]});
  });
  it('ignores both partial and final React updates queued before their view or release was cancelled',()=>{
    const oldRequest=new AbortController(),newState={releaseId:nextRelease,assets:[{...core,sha256:'new-core'}]};
    oldRequest.abort();
    for(const complete of [false,true])expect(reconcileSceneAssets(newState,release,[core,shared],complete,oldRequest.signal)).toBe(newState);
  });
  it('preserves already accepted new-release data when a parallel region fails and later results arrive',()=>{
    const request=new AbortController(),newCore={...core,sha256:'new-core'};
    const accepted=reconcileSceneAssets(previous,nextRelease,[newCore],false,request.signal);
    request.abort();const late=reconcileSceneAssets(accepted,nextRelease,[newCore,added],false,request.signal);
    expect(late).toBe(accepted);expect(late.assets).toEqual([newCore]);
  });
});
