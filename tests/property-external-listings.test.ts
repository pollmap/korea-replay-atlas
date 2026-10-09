import {expect,it} from 'vitest';
import {verifiedPropertyListing,VERIFIED_PROPERTY_LINKS} from '../shared/property-external-listings';
import type {PropertyComplex} from '../shared/property';
const complex={id:'molit-apt:11710:11710-8865',lawd_code:'11710',legal_dong_name:'가락동',lot_number:'913',name:'헬리오시티',address_conflict:false} as PropertyComplex;
it('opens the verified provider complex without transferring private or unsupported filters',()=>{
 expect(verifiedPropertyListing(complex)?.href).toBe('https://fin.land.naver.com/complexes/111515');
});
it('does not join namesakes, ambiguous records, changed addresses or untrusted URLs',()=>{
 for(const change of [{id:'molit-apt:41190:other'},{lawd_code:'41190'},{legal_dong_name:'작동'},{lot_number:'914'},{address_conflict:true}])expect(verifiedPropertyListing({...complex,...change})).toBeNull();
 expect(verifiedPropertyListing(complex,[...VERIFIED_PROPERTY_LINKS,...VERIFIED_PROPERTY_LINKS])).toBeNull();
 expect(verifiedPropertyListing(complex,[{...VERIFIED_PROPERTY_LINKS[0],providerComplexId:'111515?token=secret'}])).toBeNull();
});
