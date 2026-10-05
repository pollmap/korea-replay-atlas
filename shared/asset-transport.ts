/** Explicit file compression, independent of HTTP Content-Encoding. */
export interface AssetTransport {encoding:'gzip';sha256:string;bytes:number;}
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
export function validTransport(value:unknown):value is AssetTransport {
  if(!value||typeof value!=='object')return false;
  const v=value as Record<string,unknown>;
  return Object.keys(v).length===3&&v.encoding==='gzip'&&hash(v.sha256)&&Number.isSafeInteger(v.bytes)&&Number(v.bytes)>0&&Number(v.bytes)<=24*1024*1024;
}
export async function decodeGzip(body:ArrayBuffer,limit:number,signal:AbortSignal):Promise<ArrayBuffer>{
  if(!Number.isSafeInteger(limit)||limit<=0||limit>24*1024*1024)throw new Error('자료 크기 제한을 확인하지 못했습니다.');
  if(signal.aborted)throw new DOMException('Aborted','AbortError');
  const reader=new Response(body).body!.pipeThrough(new DecompressionStream('gzip')).getReader();
  const parts:Uint8Array[]=[];let size=0;
  const abort=()=>{void reader.cancel().catch(()=>undefined);};signal.addEventListener('abort',abort,{once:true});
  try{
    for(;;){const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>limit)throw new Error('압축 해제 크기 제한을 초과했습니다.');parts.push(part.value);}
    if(signal.aborted)throw new DOMException('Aborted','AbortError');
    const result=new Uint8Array(size);let offset=0;for(const part of parts){result.set(part,offset);offset+=part.byteLength;}return result.buffer;
  }catch(error){await reader.cancel().catch(()=>undefined);throw error;}
  finally{signal.removeEventListener('abort',abort);reader.releaseLock();}
}
