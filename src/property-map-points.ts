import {atlasFetch} from './atlas-client';
import {parsePropertyMapPoints,type PropertyMapPoint} from '../shared/property-map-point';
import manifest from './data/property-navigation-manifest.json';
const url=new URL('./data/seoul-property-navigation.json',import.meta.url).href;
let pending:Promise<Map<string,PropertyMapPoint>>|undefined;
export function supportsPropertyMapPoint(id:string,release:string){return release===manifest.release_id&&/^molit-apt:11\d{3}:[A-Za-z0-9_-]{1,64}$/.test(id);}
/** One bounded, shared download, only after a supported Seoul complex is selected. */
export async function findPropertyMapPoint(id:string,release:string):Promise<PropertyMapPoint|null>{
  if(!supportsPropertyMapPoint(id,release))return null;
  if(!pending){
    pending=(async()=>{
      const response=await atlasFetch(url);
      if(!response.ok)throw new Error('단지 제공 위치를 불러오지 못했습니다.');
      const body=await response.arrayBuffer();
      if(body.byteLength!==manifest.bytes||body.byteLength>256*1024)throw new Error('단지 위치 자료의 크기가 다릅니다.');
      const sha=[...new Uint8Array(await crypto.subtle.digest('SHA-256',body))].map(v=>v.toString(16).padStart(2,'0')).join('');
      if(sha!==manifest.sha256)throw new Error('단지 위치 자료의 내용을 검증하지 못했습니다.');
      return parsePropertyMapPoints(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(body)),release,manifest.source_sha256);
    })().catch(error=>{pending=undefined;throw error;});
  }
  return (await pending).get(id)??null;
}
