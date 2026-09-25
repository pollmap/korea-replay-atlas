import {propertyReleaseArchiveUrl} from './property-release-archive';

export interface AtlasSelection<T> {state:'loading'|'ready'|'unavailable'|'error';content:T|null;error:string;}
interface VersionedProperty {property:{release_id:string};}
interface NavigationEvents {addEventListener(type:string,listener:()=>void):void;removeEventListener(type:string,listener:()=>void):void;}
const MISSING_RELEASE='공유된 부동산 자료 버전이 이 배포에 없습니다. 최신 자료로 대체하지 않습니다.';

/** Region/month changes do not invalidate the atlas; only explicit version pins do. */
function selectionKey(url:URL):string {
  const state=new URLSearchParams(url.hash.slice(1));
  return JSON.stringify([url.origin,url.pathname,url.searchParams.getAll('deployment'),state.getAll('propertyRelease'),state.getAll('release')]);
}

/** Keep the validated atlas cached while URL selection errors are shown. Never publish a stale selection. */
export function observeAtlasSelection<T extends VersionedProperty>({load,readUrl,replaceUrl,events,publish,validateDeployment}:{load:(signal:AbortSignal)=>Promise<T|null>;readUrl:()=>string;replaceUrl:(url:string)=>void;events:NavigationEvents;publish:(state:AtlasSelection<T>)=>void;validateDeployment?:(url:URL)=>void}):()=>void {
  const controller=new AbortController();let active=true,content:T|null=null,lastKey:string|null=null;
  const select=()=>{
    if(!active||!content)return;
    try {
      const href=readUrl(),url=new URL(href),key=selectionKey(url);
      if(lastKey===key)return;
      lastKey=key;
      validateDeployment?.(url);
      const requested=new URLSearchParams(url.hash.slice(1)).getAll('propertyRelease');
      if(requested.length===0||requested.length===1&&requested[0]===content.property.release_id){publish({state:'ready',content,error:''});return;}
      const archiveUrl=propertyReleaseArchiveUrl(href,content.property.release_id);
      if(archiveUrl){publish({state:'loading',content:null,error:''});replaceUrl(archiveUrl);return;}
      publish({state:'error',content:null,error:MISSING_RELEASE});
    } catch(error) {
      if(active)publish({state:'error',content:null,error:error instanceof Error?error.message:MISSING_RELEASE});
    }
  };
  events.addEventListener('hashchange',select);
  events.addEventListener('popstate',select);
  publish({state:'loading',content:null,error:''});
  void load(controller.signal).then(value=>{
    if(!active||controller.signal.aborted)return;
    if(!value){publish({state:'unavailable',content:null,error:''});return;}
    content=value;select();
  }).catch(error=>{if(active&&!controller.signal.aborted)publish({state:'error',content:null,error:error instanceof Error?error.message:'지역 자료를 불러오지 못했습니다.'});});
  return ()=>{active=false;controller.abort();events.removeEventListener('hashchange',select);events.removeEventListener('popstate',select);content=null;};
}
