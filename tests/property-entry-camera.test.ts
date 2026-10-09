import {expect,it} from 'vitest';
import {propertyEntryCamera} from '../src/property-entry-camera';
import type {AtlasContent} from '../src/atlas-loader';
import type {PropertyMapPoint} from '../shared/property-map-point';
import {PLACES} from '../shared/sources';
const release='property-ceeff63959643461',id='molit-apt:11710:11710-8865';
const atlas={map:{reference_dates:{sgis:'2025-06-30'}},property:{release_id:release},regions:{regions:[{lawd_code:'11710',name:'서울특별시 송파구'}]}} as unknown as AtlasContent;
const point:PropertyMapPoint={complexId:id,releaseId:release,kaptCode:'A10025850',longitude:127.11,latitude:37.5,coordinateStatus:'provider_xy_crs_unconfirmed',navigationEvidence:{method:'official_site_marker_same_kapt_code_and_coordinates',pointSemantics:'provider_map_navigation_marker',sourceUrl:'https://openapt.seoul.go.kr/',checkedAt:'2026-10-08T00:00:00Z',markerResponseSha256:'a'.repeat(64),navigationPageSha256:'b'.repeat(64)}};
const href=`https://example.com/#regionCode=11710&complex=${encodeURIComponent(id)}`;
it('starts a confirmed selected apartment at zoom 15 without a national fly-through',()=>{
  const result=propertyEntryCamera(href,atlas,PLACES[0],null,point);
  expect(result.camera).toEqual([127.11,37.5,15,0]);expect(result.place.id).toBe(id);
});
it('falls back to source-backed regional framing for unknown, stale or unverified locations',()=>{
  for(const candidate of [null,{...point,releaseId:'property-0000000000000000'},{...point,navigationEvidence:undefined},{...point,complexId:'molit-apt:11710:other'}])expect(propertyEntryCamera(href,atlas,PLACES[0],null,candidate).place.id).toBe('region-sgis-11240');
});
it('preserves an explicit shared camera and has a national fallback for an unknown region',()=>{
  expect(propertyEntryCamera(href,atlas,PLACES[0],[127,37,11,10],point).camera).toEqual([127,37,11,10]);
  expect(propertyEntryCamera(href+'&position=127,37,1000',atlas,PLACES[0],null,point).place).toBe(PLACES[0]);
  expect(propertyEntryCamera('https://example.com/#regionCode=00000',atlas,PLACES[0],null).place).toBe(PLACES[0]);
});
