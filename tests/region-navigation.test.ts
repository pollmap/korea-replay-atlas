import {describe,expect,it} from 'vitest';
import {regionNavigation,regionNavigationPlace,REGION_NAVIGATION_SOURCE} from '../src/region-navigation';

const map={reference_dates:{sgis:'2025-06-30'}};
describe('source-backed region camera navigation',()=>{
  it('frames Songpa by its full name without treating the legal code as an SGIS code',()=>{
    const region={name:'서울특별시 송파구',lawd_code:'11710'};
    const result=regionNavigation(region,map)!;
    expect(result.sourceRecordId).toBe('sgis:20250630:sigungu:11240');
    expect(result.place.id).toBe('region-sgis-11240');
    expect(result.place.lon).toBeGreaterThan(127.05);expect(result.place.lon).toBeLessThan(127.2);
    expect(result.place.lat).toBeGreaterThan(37.4);expect(result.place.lat).toBeLessThan(37.6);
    expect(result.place.range).toBeGreaterThanOrEqual(4000);expect(result.place.range).toBeLessThan(50000);
    expect(result.purpose).toBe('camera-framing');
    expect(result.boundaryReferenceDate).toBe(REGION_NAVIGATION_SOURCE.referenceDate);
  });
  it('disambiguates identically named boroughs using the exact province name',()=>{
    const seoul=regionNavigationPlace({name:'서울특별시 중구'},map)!;
    const busan=regionNavigationPlace({name:'부산광역시 중구'},map)!;
    expect(seoul.lat).toBeGreaterThan(37);expect(busan.lat).toBeLessThan(36);
    expect(seoul.id).not.toBe(busan.id);
    expect(regionNavigationPlace({name:'중구'},map)).toBeNull();
  });
  it('handles published trailing whitespace and the Sejong province-level name',()=>{
    expect(regionNavigationPlace({name:'경기도 부천시 원미구 '},map)).toEqual(regionNavigationPlace({name:'경기도 부천시 원미구'},map));
    expect(regionNavigationPlace({name:'경기도 부천시 원미구'},map)).not.toBeNull();
    expect(regionNavigation({name:'세종특별자치시'},map)?.sourceRecordId).toBe('sgis:20250630:sido:29');
  });
  it('refuses renamed/new districts, partial names and apartment addresses',()=>{
    for(const name of ['인천광역시 영종구','경기도 화성시 동탄구','전남광주통합특별시 동구','송파구','서울특별시 송파구 잠실동 1']){
      expect(regionNavigationPlace({name},map)).toBeNull();
    }
  });
  it('does not silently mix this snapshot with a different boundary release',()=>{
    expect(regionNavigationPlace({name:'서울특별시 송파구'},{reference_dates:{sgis:'2026-01-01'}})).toBeNull();
    expect(regionNavigationPlace({name:'서울특별시 송파구'},{reference_dates:{}})).toBeNull();
  });
  it('keeps the navigation point inside its published framing bounds across mainland and island regions',()=>{
    for(const name of ['서울특별시 송파구','부산광역시 중구','충청북도 청주시 상당구','제주특별자치도 제주시','경상북도 울릉군']){
      const result=regionNavigation({name},map)!;expect(result).not.toBeNull();
      const [w,s,e,n]=result.bounds;
      expect(result.place.lon).toBeGreaterThanOrEqual(w);expect(result.place.lon).toBeLessThanOrEqual(e);
      expect(result.place.lat).toBeGreaterThanOrEqual(s);expect(result.place.lat).toBeLessThanOrEqual(n);
      expect(result.place.range).toBeLessThanOrEqual(600000);
    }
  });
});
