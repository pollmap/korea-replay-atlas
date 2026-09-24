import {beforeEach,expect,it,vi} from 'vitest';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {parsePropertyMapPoints} from '../shared/property-map-point';
import manifest from '../src/data/property-navigation-manifest.json';
const request=vi.hoisted(()=>vi.fn());
vi.mock('../src/atlas-client',()=>({atlasFetch:request}));
const raw=readFileSync(new URL('../src/data/seoul-property-navigation.json',import.meta.url),'utf8');
const source=JSON.parse(readFileSync(new URL('../src/data/seoul-kapt-points-b63b62af834062de.geojson',import.meta.url),'utf8'));
const helio='molit-apt:11710:11710-8865';
beforeEach(()=>{vi.resetModules();request.mockReset();});
it('preserves every linked source coordinate exactly, with separate provisional geometry status',()=>{
  expect(createHash('sha256').update(raw).digest('hex')).toBe(manifest.sha256);
  expect(Buffer.byteLength(raw)).toBe(manifest.bytes);expect(manifest.bytes).toBeLessThan(64*1024);
  const points=parsePropertyMapPoints(JSON.parse(raw),manifest.release_id,manifest.source_sha256);
  const linked=source.features.filter((row:{properties:Record<string,unknown>})=>row.properties.property_complex_id);
  expect(points.size).toBe(848);expect(points.size).toBe(linked.length);
  for(const row of linked){
    const point=points.get(row.properties.property_complex_id);
    expect([point?.longitude,point?.latitude]).toEqual(row.geometry.coordinates);
    expect(point?.kaptCode).toBe(row.properties.kapt_code);
    expect(point?.coordinateStatus).toBe('provider_xy_crs_unconfirmed');
  }
});
it.each(['duplicate-id','duplicate-code','bad-coordinate','mismatched-release','quality-promotion','source-changed'])('rejects %s without silently selecting a candidate',fault=>{
  const value=JSON.parse(raw);
  if(fault==='duplicate-id')value.points[1][0]=value.points[0][0];
  if(fault==='duplicate-code')value.points[1][1]=value.points[0][1];
  if(fault==='bad-coordinate')value.points[0][2]=0;
  if(fault==='mismatched-release')value.property_release_id='property-0000000000000000';
  if(fault==='quality-promotion')value.coordinate_status='verified';
  if(fault==='source-changed')value.source_sha256='0'.repeat(64);
  expect(()=>parsePropertyMapPoints(value,manifest.release_id,manifest.source_sha256)).toThrow();
});
it('does not fetch for unsupported regions or a different pinned property release',async()=>{
  const {findPropertyMapPoint}=await import('../src/property-map-points');
  expect(await findPropertyMapPoint('molit-apt:41360:41360-100',manifest.release_id)).toBeNull();
  expect(await findPropertyMapPoint(helio,'property-0000000000000000')).toBeNull();
  expect(request).not.toHaveBeenCalled();
});
it('shares the small download across selections and returns no fabricated point for an unlinked complex',async()=>{
  request.mockImplementation(async()=>new Response(raw));
  const {findPropertyMapPoint}=await import('../src/property-map-points');
  const [first,second,missing]=await Promise.all([findPropertyMapPoint(helio,manifest.release_id),findPropertyMapPoint(helio,manifest.release_id),findPropertyMapPoint('molit-apt:11710:unknown',manifest.release_id)]);
  expect(first?.kaptCode).toBe('A10025850');expect(second).toBe(first);expect(missing).toBeNull();
  expect(request).toHaveBeenCalledTimes(1);
});
it('rejects corrupted bytes and allows an explicit retry after failure',async()=>{
  request.mockResolvedValueOnce(new Response(raw.replace('A10025850','A10025851'))).mockResolvedValueOnce(new Response(raw));
  const {findPropertyMapPoint}=await import('../src/property-map-points');
  await expect(findPropertyMapPoint(helio,manifest.release_id)).rejects.toThrow('검증');
  expect((await findPropertyMapPoint(helio,manifest.release_id))?.kaptCode).toBe('A10025850');
  expect(request).toHaveBeenCalledTimes(2);
});
