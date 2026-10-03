import {atlasFetch} from './atlas-client';
export interface RegionFilterMetric {status:'complete'|'partial'|'pending'|'failed'|'source_unavailable';count:number|null;median_per_m2:number|null;covered:number;expected:number;unavailable:number;}
export interface RegionFilterMetrics {schema_version:1;kind:'property-region-filter-metrics';property_release_id:string;regions:Record<string,Record<string,RegionFilterMetric>>;}
const sources:Record<string,string>={
  'property-b87eea7c1c03dc21':new URL('./data/property-region-metrics-b87eea7c1c03dc21.json',import.meta.url).href,
};
export function parseRegionFilterMetrics(value:unknown,release:string):RegionFilterMetrics {
  const v=value as RegionFilterMetrics;
  if(!v||v.schema_version!==1||v.kind!=='property-region-filter-metrics'||v.property_release_id!==release||!v.regions||typeof v.regions!=='object'||Array.isArray(v.regions)||Object.keys(v.regions).length>400)throw Error('지역 집계 형식 오류');
  for(const [code,rows] of Object.entries(v.regions)){
    if(!/^\d{5}$/.test(code)||!rows||typeof rows!=='object'||Array.isArray(rows)||Object.keys(rows).length>256)throw Error('지역 집계 범위 오류');
    for(const [key,row] of Object.entries(rows)){
      if(!/^\d{4}(?:0[1-9]|1[0-2])\|(?:1|3|6|12|36|60|120|240)\|(?:84-band)?\|(?:sale|rent|jeonse|monthly)$/.test(key)||!row||!['complete','partial','pending','failed','source_unavailable'].includes(row.status))throw Error('지역 집계 조건 오류');
      const length=Number(key.split('|')[1]);
      if(!Number.isSafeInteger(row.covered)||!Number.isSafeInteger(row.unavailable)||row.covered<0||row.unavailable<0||row.expected!==length||row.covered+row.unavailable>length||row.count!==null&&(!Number.isSafeInteger(row.count)||row.count<0)||row.median_per_m2!==null&&(!Number.isSafeInteger(row.median_per_m2)||row.median_per_m2<0)||row.status==='complete'&&row.covered!==length||row.status==='source_unavailable'&&row.unavailable!==length||row.covered===0&&row.count!==null||row.covered>0&&row.count===null||row.count===0&&row.median_per_m2!==null)throw Error('지역 집계 값 오류');
    }
  }
  return v;
}
export async function loadRegionFilterMetrics(release:string,signal:AbortSignal):Promise<RegionFilterMetrics|null>{
  const url=sources[release];if(!url)return null;
  const response=await atlasFetch(url,{signal});if(!response.ok)throw Error('지역 집계 조회 실패');
  const body=await response.text();if(body.length>8*1024*1024)throw Error('지역 집계 용량 초과');
  return parseRegionFilterMetrics(JSON.parse(body),release);
}
export function selectedRegionMetric(data:RegionFilterMetrics|null|undefined,release:string,code:string,month:string,length=1,area='',trade:'sale'|'rent'='sale',rentKind='all'):RegionFilterMetric|undefined{
  if(data?.property_release_id!==release)return;
  const kind=trade==='sale'?'sale':rentKind==='jeonse'?'jeonse':rentKind==='monthly'?'monthly':'rent';
  return data.regions[code]?.[`${month}|${length}|${area}|${kind}`];
}