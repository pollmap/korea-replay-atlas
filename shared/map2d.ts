import type {Asset,BBox,LayerId} from './contracts';
import {AssetLoadQueue} from './asset-loader';
import {assertGeometryVertexBudget,resolveFeatureProperties,type GeoCollection,type GeoGeometry,type GeoMetadata,type RenderFeature} from './geometry';
import {selectViewAssets,type QualityLevel} from './map-performance';
import {selectedProperties,type MapSelection} from './selection';

export type FlatCamera=[number,number,number,number];
export const MAP2D_MAX_BYTES=24*1024*1024;
export const MAP2D_ATTRIBUTION='<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap contributors</a> · <a href="https://www.naturalearthdata.com/about/terms-of-use/" target="_blank" rel="noopener">Natural Earth</a> · <a href="https://docs.overturemaps.org/attribution/" target="_blank" rel="noopener">Overture Maps</a>';
const aborted=()=>new DOMException('Aborted','AbortError');
const safePath=(value:unknown):value is string=>typeof value==='string'&&/^\/data\/[\w./-]+$/.test(value)&&!value.includes('..');
export const map2DKey=(asset:Asset)=>`${asset.id}:${asset.sha256}`;
export function readFlatCamera(hash:string):FlatCamera|null {
  const raw=new URLSearchParams(hash.replace(/^#/,'' )).get('flatCamera');
  if(!raw)return null;
  const values=raw.split(',').map(Number);
  if(values.length!==4||raw.split(',').some(value=>!value.trim())||!values.every(Number.isFinite))return null;
  const [lon,lat,zoom,bearing]=values;
  return lon>=124&&lon<=132.5&&lat>=32&&lat<=39.5&&zoom>=3&&zoom<=19&&bearing>=-180&&bearing<=180?values as FlatCamera:null;
}
/** Equivalent overhead scale for the existing asset LOD contract, not a surveyed altitude. */
export function map2DHeight(lat:number,zoom:number,viewportHeight:number):number {
  return 40075016.68557849*Math.cos(lat*Math.PI/180)/512/2**zoom*Math.max(1,viewportHeight)/(2*Math.tan(Math.PI/6));
}
export function map2DZoom(lat:number,height:number,viewportHeight:number):number {
  return Math.max(3,Math.min(19,Math.log2(map2DHeight(lat,0,viewportHeight)/Math.max(1,height))));
}
export function map2DLayers(layers:Record<LayerId,boolean>):Record<LayerId,boolean> {
  return {...layers,bus:false,depth:false,radar:false,satellite:false,sun:false};
}
export function selectMap2DAssets(assets:Asset[],view:{bbox:BBox;height:number;layers:Record<LayerId,boolean>;quality:QualityLevel}) {
  return selectViewAssets(assets.filter(asset=>asset.format==='geojson'&&!['bus','depth','radar','satellite','sun'].includes(asset.layer)),{...view,layers:map2DLayers(view.layers),mode:'sun',dayStart:0,dayEnd:Number.MAX_SAFE_INTEGER});
}
/** Source classifications are styling hints, never statutory Korean road categories. */
export function map2DRoadClass(value:unknown):string {
  if(typeof value!=='string')return 'other';
  const name=value.replace(/_link$/,'');
  return ['motorway','trunk','primary','secondary','tertiary','residential','service','footway','path','cycleway'].includes(name)?name:'other';
}

export interface Map2DPrepared {bytes:ArrayBuffer;featureCount:number;vertexCount:number;records:RenderFeature[];metadata?:GeoMetadata;}
function validateGeometry(geometry:GeoGeometry,depth=0):number {
  if(!geometry||depth>24)throw new Error('공간 형식이 올바르지 않습니다.');
  const list=(value:unknown):unknown[]=>{if(!Array.isArray(value))throw new Error('공간 좌표가 배열이 아닙니다.');return value;};
  const point=(value:unknown):number=>{const p=list(value);if(p.length<2||!p.every(x=>typeof x==='number'&&Number.isFinite(x))||Math.abs(p[0] as number)>180||Math.abs(p[1] as number)>90)throw new Error('유효하지 않은 공간 좌표입니다.');return 1;};
  const line=(value:unknown,minimum:number):number=>{const p=list(value);if(p.length<minimum)throw new Error('공간 좌표가 부족합니다.');return p.reduce<number>((sum,item)=>sum+point(item),0);};
  const polygon=(value:unknown):number=>{const rings=list(value);if(!rings.length)throw new Error('다각형 외곽선이 없습니다.');return rings.reduce<number>((sum,ring)=>sum+line(ring,4),0);};
  switch(geometry.type){
    case 'Point':return point(geometry.coordinates);
    case 'MultiPoint':return list(geometry.coordinates).reduce<number>((sum,p)=>sum+point(p),0);
    case 'LineString':return line(geometry.coordinates,2);
    case 'MultiLineString':return list(geometry.coordinates).reduce<number>((sum,p)=>sum+line(p,2),0);
    case 'Polygon':return polygon(geometry.coordinates);
    case 'MultiPolygon':return list(geometry.coordinates).reduce<number>((sum,p)=>sum+polygon(p),0);
    case 'GeometryCollection':return list(geometry.geometries).reduce<number>((sum,g)=>sum+validateGeometry(g as GeoGeometry,depth+1),0);
    default:throw new Error('지원하지 않는 공간 형식입니다.');
  }
}
function category(asset:Asset,kind:unknown):string {
  if(asset.source_id==='natural-earth')return 'land';
  if(kind==='water'||kind==='waterway'||kind==='coastline')return 'water';
  if(asset.layer==='buildings')return 'building';
  if(asset.layer==='rail'&&(kind==='rail'||kind==='railway'||kind==='rail_line'))return 'rail';
  if(kind==='rail'||kind==='railway'||kind==='rail_line')return 'rail';
  if(kind==='road'||kind==='ferry_route')return 'road';
  return asset.layer==='rail'?'rail':'facility';
}
/** Runs in the decode Worker. JSON geometry and original feature IDs are retained verbatim. */
export async function prepareMap2DData(asset:Asset,bytes:ArrayBuffer,signal?:AbortSignal):Promise<Map2DPrepared> {
  if(signal?.aborted)throw aborted();
  if(asset.format!=='geojson'||!safePath(asset.url))throw new Error('공간 자료 경로가 올바르지 않습니다.');
  if(bytes.byteLength>MAP2D_MAX_BYTES||asset.byte_length!==undefined&&asset.byte_length!==bytes.byteLength)throw new Error('공간 자료 크기가 일치하지 않습니다.');
  if(!/^[a-f0-9]{64}$/.test(asset.sha256))throw new Error('공간 자료 해시가 없습니다.');
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');
  if(digest!==asset.sha256)throw new Error('공간 자료 해시가 일치하지 않습니다.');
  if(signal?.aborted)throw aborted();
  const data=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes)) as GeoCollection;
  if(data?.type!=='FeatureCollection'||!Array.isArray(data.features))throw new Error('공간 자료 형식이 올바르지 않습니다.');
  if(asset.feature_count!==undefined&&data.features.length!==asset.feature_count)throw new Error('공간 객체 수가 일치하지 않습니다.');
  const metadata=data.metadata;
  if(metadata&&(metadata.schema_version!==1||!Array.isArray(metadata.rows)||!metadata.shared||typeof metadata.shared!=='object'||metadata.rows.some(row=>!row||typeof row!=='object'||Array.isArray(row))))throw new Error('공간 속성 사전 형식이 올바르지 않습니다.');
  const records:RenderFeature[]=[];let vertexCount=0;
  const features=data.features.flatMap((feature,index)=>{
    if(feature?.type!=='Feature'||feature.properties!==null&&typeof feature.properties!=='object')throw new Error('공간 객체 형식이 올바르지 않습니다.');
    const raw=feature.properties??{},row=raw.metadata_index;
    if(row!==undefined&&(!metadata||!Number.isInteger(row)||Number(row)<0||Number(row)>=metadata.rows.length))throw new Error('공간 속성 행 번호가 올바르지 않습니다.');
    // Only a small render projection crosses the worker boundary. Selection expands one record.
    const read=(key:string):unknown=>Object.prototype.hasOwnProperty.call(raw,key)?raw[key]:row!==undefined&&Object.prototype.hasOwnProperty.call(metadata!.rows[Number(row)],key)?metadata!.rows[Number(row)][key]:metadata?.shared[key];
    const sourceId=String(feature.id??read('source_record_id')??`feature-${index}`);
    records.push({id:sourceId,properties:raw});
    if(!feature.geometry)return [];
    vertexCount+=validateGeometry(feature.geometry);
    return [{type:'Feature' as const,id:feature.id??sourceId,geometry:feature.geometry,properties:{map2d_index:index,map2d_category:category(asset,read('kind')),map2d_road:map2DRoadClass(read('highway')??read('class'))}}];
  });
  assertGeometryVertexBudget({vertexCount},asset);
  const encoded=new TextEncoder().encode(JSON.stringify({type:'FeatureCollection',features}));
  if(encoded.byteLength>MAP2D_MAX_BYTES)throw new Error('2D 공간 자료 표시 예산을 초과했습니다.');
  return {bytes:encoded.buffer,featureCount:features.length,vertexCount,records,metadata};
}
export function selectMap2DFeature(asset:Asset,data:Pick<Map2DPrepared,'records'|'metadata'>,index:number):MapSelection|null {
  if(!Number.isSafeInteger(index)||index<0||!data.records[index])return null;
  const record=data.records[index],properties=resolveFeatureProperties(record,data.metadata);
  return selectedProperties(record.id,properties,asset);
}

/** One body-lifetime queue covers both regional indexes and GeoJSON downloads. */
export function createMap2DFetcher(base:typeof fetch=fetch) {
  const queue=new AssetLoadQueue<Response>(4);let sequence=0,active=0,peak=0,disposed=false;
  const fetcher:typeof fetch=async(input,init)=>{
    if(disposed)throw aborted();
    if(!safePath(input))throw new Error('공간 자료 경로가 올바르지 않습니다.');
    const controller=new AbortController(),external=init?.signal;
    const abort=()=>controller.abort();external?.addEventListener('abort',abort,{once:true});if(external?.aborted)abort();
    const timeout=setTimeout(()=>controller.abort(),60000);
    try{
      const result=await queue.enqueue(String(++sequence),async signal=>{
        if(signal.aborted)throw aborted();active++;peak=Math.max(peak,active);
        try{
          const response=await base(input,{...init,signal});
          if(!response.ok){await response.body?.cancel();return new Response(null,{status:response.status,statusText:response.statusText,headers:response.headers});}
          const reader=response.body?.getReader(),chunks:Uint8Array[]=[];let size=0;
          const cancelBody=()=>{void reader?.cancel().catch(()=>undefined);};signal.addEventListener('abort',cancelBody,{once:true});
          try{
            if(reader)while(true){const next=await reader.read();if(next.done)break;size+=next.value.byteLength;if(size>MAP2D_MAX_BYTES)throw new Error('공간 자료 다운로드 예산을 초과했습니다.');chunks.push(next.value);}
            if(signal.aborted)throw aborted();
          }catch(error){await reader?.cancel().catch(()=>undefined);throw error;}
          finally{signal.removeEventListener('abort',cancelBody);reader?.releaseLock();}
          const body=new Uint8Array(size);let offset=0;for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.byteLength;}
          const headers=new Headers(response.headers);headers.delete('content-encoding');headers.delete('content-length');
          return new Response(body,{status:response.status,statusText:response.statusText,headers});
        }finally{active--;}
      },controller.signal);
      if(!result||controller.signal.aborted)throw aborted();return result;
    }finally{clearTimeout(timeout);external?.removeEventListener('abort',abort);}
  };
  return {fetcher,stats:()=>({active,peak}),dispose:()=>{disposed=true;queue.cancelAll();}};
}
