import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {expect,it,vi} from 'vitest';
import {confirmPropertyNavigationPoints,confirmedPropertyNavigationPoint,parsePropertyMapPoints,propertyNavigationCamera} from '../shared/property-map-point';
import manifest from '../src/data/property-navigation-manifest-ceeff63959643461.json';
import evidenceManifest from '../src/data/property-navigation-evidence-manifest-ceeff63959643461.json';
import {createPropertyMapPointLookup} from '../src/property-map-points';
const raw=readFileSync(new URL('../src/data/seoul-property-navigation-ceeff63959643461.json',import.meta.url),'utf8');
const evidenceRaw=readFileSync(new URL('../src/data/seoul-property-navigation-evidence-ceeff63959643461.json',import.meta.url),'utf8');
const helio='molit-apt:11710:11710-8865';
const points=()=>parsePropertyMapPoints(JSON.parse(raw),manifest.release_id,manifest.source_sha256);
it('corroborates every current linked point without turning it into a footprint or a different raw CRS status',()=>{
  expect(createHash('sha256').update(evidenceRaw).digest('hex')).toBe(evidenceManifest.sha256);
  expect(Buffer.byteLength(evidenceRaw)).toBe(evidenceManifest.bytes);
  const result=confirmPropertyNavigationPoints(JSON.parse(evidenceRaw),points(),manifest.release_id,manifest.source_sha256);
  expect(result.size).toBe(832);
  for(const point of result.values()){
    expect(confirmedPropertyNavigationPoint(point,manifest.release_id,point.complexId)).toBe(point);
    expect(point.coordinateStatus).toBe('provider_xy_crs_unconfirmed');
    expect(point.navigationEvidence?.pointSemantics).toBe('provider_map_navigation_marker');
  }
  expect(result.get(helio)).toMatchObject({kaptCode:'A10025850',longitude:127.10249742,latitude:37.497609869});
  expect(confirmedPropertyNavigationPoint(points().get(helio),manifest.release_id)).toBeNull();
});
it.each(['id','coordinate','release','duplicate','source','name-only','same-name-other-id'])('rejects evidence mismatch: %s',fault=>{
  const value=JSON.parse(evidenceRaw);
  if(fault==='id')value.points[0][1]='A99999999';
  if(fault==='coordinate')value.points[0][2]+=.001;
  if(fault==='release')value.property_release_id='property-'+'0'.repeat(16);
  if(fault==='duplicate')value.points.push(value.points[0]);
  if(fault==='source')value.source_sha256='0'.repeat(64);
  if(fault==='name-only')value.identity_rule='same_name';
  if(fault==='same-name-other-id')value.points[0][0]='molit-apt:11710:11710-999999';
  expect(()=>confirmPropertyNavigationPoints(value,points(),manifest.release_id,manifest.source_sha256)).toThrow();
});
it('does not lend confirmed coordinates to another release or complex',()=>{
  const point=confirmPropertyNavigationPoints(JSON.parse(evidenceRaw),points(),manifest.release_id,manifest.source_sha256).get(helio)!;
  expect(confirmedPropertyNavigationPoint(point,'property-'+'0'.repeat(16))).toBeNull();
  expect(confirmedPropertyNavigationPoint(point,manifest.release_id,'other')).toBeNull();
});
it('shares the two small evidence downloads across simultaneous selections and fails closed on corruption',async()=>{
  const source={manifest,url:'/points.json',evidence:{manifest:evidenceManifest,url:'/evidence.json'}};
  const fetcher=vi.fn(async(input:RequestInfo|URL)=>new Response(String(input)==='/points.json'?raw:evidenceRaw));
  const lookup=createPropertyMapPointLookup([source],fetcher);
  const [one,two]=await Promise.all([lookup.find(helio,manifest.release_id),lookup.find(helio,manifest.release_id)]);
  expect(one).toBe(two);expect(one?.navigationEvidence).toBeDefined();expect(fetcher).toHaveBeenCalledTimes(2);
  const corrupt=createPropertyMapPointLookup([source],async(input)=>new Response(String(input)==='/points.json'?raw:evidenceRaw.replace('A10025850','A10025851')));
  await expect(corrupt.find(helio,manifest.release_id)).rejects.toThrow('검증');
});

it('opens two separately corroborated complexes at neighborhood zoom and refuses an unconfirmed point',()=>{
  const verified=confirmPropertyNavigationPoints(JSON.parse(evidenceRaw),points(),manifest.release_id,manifest.source_sha256);
  for(const point of [verified.get(helio)!,[...verified.values()].find(p=>p.complexId!==helio)!]){
    expect(propertyNavigationCamera(point,manifest.release_id)).toEqual({key:`${manifest.release_id}:${point.complexId}`,center:[point.longitude,point.latitude],zoom:15});
  }
  expect(propertyNavigationCamera(points().get(helio),manifest.release_id)).toBeNull();
  expect(propertyNavigationCamera(verified.get(helio),'other')).toBeNull();
});
