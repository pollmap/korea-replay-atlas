import {readFile,writeFile} from 'node:fs/promises';
import process from 'node:process';

// CDP page events omit some dedicated-worker requests. Keep that scope explicit.
const [input,output]=process.argv.slice(2);
if(!input||!output)throw new Error('Expected input CDP JSON and output report paths');
const capture=JSON.parse(await readFile(input,'utf8')),requests=new Map();
for(const event of capture.events){
  const p=event.params;
  if(event.method==='Network.requestWillBeSent'){
    const pathname=new URL(p.request.url).pathname;
    if(!pathname.startsWith('/data/')||pathname==='/data/catalog.json')continue;
    requests.set(p.requestId,{path:pathname,start:p.timestamp});
  }
  const row=requests.get(p.requestId);if(!row)continue;
  if(event.method==='Network.responseReceived'){
    const response=p.response,headers=Object.fromEntries(Object.entries(response.headers).map(([key,value])=>[key.toLowerCase(),value]));
    Object.assign(row,{status:response.status,decoded_bytes:Number(headers['content-length']??0),from_service_worker:!!response.fromServiceWorker,from_disk_cache:!!response.fromDiskCache});
  }
  if(event.method==='Network.loadingFinished')row.end=p.timestamp;
  if(event.method==='Network.loadingFailed')row.error=p.errorText;
}
const rows=[...requests.values()],finished=rows.filter(row=>row.end!==undefined),successful=finished.filter(row=>row.status>=200&&row.status<300);
const group=filter=>{const chosen=successful.filter(filter);return {requests:chosen.length,decoded_bytes:chosen.reduce((sum,row)=>sum+row.decoded_bytes,0)};};
const report={scope:'Captured page data requests excluding background catalog refresh; some geometry-worker requests are absent. Content-Length after the service worker is decoded size, not network transfer size.',
  requests:rows.length,completed:finished.length,successful:successful.length,failed:rows.filter(row=>row.error||row.status>=400).length,
  elapsed_ms:finished.length?Math.round((Math.max(...finished.map(row=>row.end))-Math.min(...rows.map(row=>row.start)))*1000):null,
  total:group(()=>true),detail_glb:group(row=>row.path.includes('/detail/')&&row.path.endsWith('.glb')),overview_glb:group(row=>!row.path.includes('/detail/')&&row.path.endsWith('.glb')),
  slowest:successful.map(row=>({...row,elapsed_ms:Math.round((row.end-row.start)*1000)})).sort((a,b)=>b.elapsed_ms-a.elapsed_ms).slice(0,8)};
await writeFile(output,JSON.stringify(report,null,2)+'\n');
process.stdout.write(JSON.stringify({...report,slowest:undefined})+'\n');
