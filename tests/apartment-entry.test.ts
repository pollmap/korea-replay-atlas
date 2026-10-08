import {describe,expect,it} from 'vitest';
import {readApartmentEntry} from '../shared/apartment-entry';
describe('2D apartment entry',()=>{
  it('recognizes retired modes without changing release/deployment/location pins',()=>{
    for(const suffix of ['view=3d','mode=sun','mode=replay','mode=live']){
      const result=readApartmentEntry(`https://example.com/?deployment=pinned#release=old&regionCode=11710&position=127.1,37.5,3500&${suffix}`);
      expect(result).toMatchObject({retired:true,release:'old',deployment:'pinned',region:'11710',place:{lon:127.1,lat:37.5,range:3500}});
    }
  });
  it('does not treat old ECEF camera coordinates as a 2D location',()=>{
    expect(readApartmentEntry('https://example.com/#camera=1000000,2000000,3000000').place.id).toBe('korea');
    expect(readApartmentEntry('https://example.com/#regionCode=11710&view=2d').retired).toBe(false);
    expect(readApartmentEntry('https://example.com/?deployment=a&deployment=b').deployment).toBe('invalid');
  });
});
