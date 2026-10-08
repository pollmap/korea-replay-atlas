import {expect,it} from 'vitest';
import {PROPERTY_PRIMARY_CODES,isPrimaryPropertyRegion,propertyExplorationRegions,propertyProvinceLabel} from '../shared/property-scope';
it('uses the same nine-area scope as the audited apartment search index',()=>{
 expect(PROPERTY_PRIMARY_CODES.size).toBe(112);
 for(const code of ['11710','28177','41171','44131','44200','36110','43111','30110','26110'])expect(isPrimaryPropertyRegion(code)).toBe(true);
 for(const code of ['27110','50110','43130','44230'])expect(isPrimaryPropertyRegion(code)).toBe(false);
});
it('keeps historical selected regions reachable without adding them to new discovery',()=>{
 const regions=[{lawd_code:'11710'},{lawd_code:'50110'}];
 expect(propertyExplorationRegions(regions)).toEqual([regions[0]]);
 expect(propertyExplorationRegions(regions,'50110')).toEqual(regions);
 expect(regions).toHaveLength(2);
 expect(propertyProvinceLabel('충청남도')).toBe('천안·아산');
 expect(propertyProvinceLabel('충청북도')).toBe('청주');
});

it('does not mislabel an old out-of-scope district as the current city group',()=>{
 expect(propertyProvinceLabel('충청남도',[{lawd_code:'44230',name:'충청남도 논산시'}])).toBe('충청남도');
});
