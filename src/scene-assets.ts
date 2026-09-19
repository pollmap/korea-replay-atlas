import type {Asset} from '../shared/contracts';

export interface SceneAssetResult {releaseId:string;assets:Asset[];}
/** Partial index reads must not evict still-needed assets of the same release. */
export function reconcileSceneAssets(previous:SceneAssetResult,releaseId:string,assets:Asset[],complete:boolean,signal:AbortSignal):SceneAssetResult {
  // React may run a queued functional updater after its request was cancelled.
  if(signal.aborted)return previous;
  const next=complete||previous.releaseId!==releaseId?assets:[...new Map([...previous.assets,...assets].map(asset=>[asset.id,asset])).values()];
  if(previous.releaseId===releaseId&&next.length===previous.assets.length&&next.every((asset,index)=>asset===previous.assets[index]))return previous;
  return {releaseId,assets:next};
}
