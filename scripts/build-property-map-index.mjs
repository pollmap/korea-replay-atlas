import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const digest=body=>createHash('sha256').update(body).digest('hex');
const source=await readFile(new URL('../src/data/seoul-kapt-points-b63b62af834062de.geojson',import.meta.url));
const sourceSha=digest(source);
if(sourceSha!=='b63b62af834062de98b142f4caef4f8a2087bd8e713c15ba3351fcf3dc06859c')throw new Error('Point source changed; re-audit before generating navigation.');
const data=JSON.parse(source),release=data.metadata.property_release_id,seen=new Set();
const points=data.features.filter(row=>row.properties.property_complex_id).map(row=>{
  const p=row.properties,id=p.property_complex_id;
  if(seen.has(id)||p.property_release_id!==release||p.coordinate_status!=='provider_xy_crs_unconfirmed'||p.property_aptseq_join!=='unique_official_road_address_and_name')throw new Error('Ambiguous or incompatible point link');
  seen.add(id);
  return [id,p.kapt_code,...row.geometry.coordinates];
}).sort((a,b)=>a[0].localeCompare(b[0],'en'));
const body=JSON.stringify({schema_version:1,property_release_id:release,coordinate_status:'provider_xy_crs_unconfirmed',source_sha256:sourceSha,points});
const manifest={release_id:release,sha256:digest(body),bytes:Buffer.byteLength(body),source_sha256:sourceSha};
await writeFile(new URL('../src/data/seoul-property-navigation.json',import.meta.url),body);
await writeFile(new URL('../src/data/property-navigation-manifest.json',import.meta.url),JSON.stringify(manifest,null,2)+'\n');
console.log(JSON.stringify({...manifest,points:points.length}));
