import {describe,expect,it} from 'vitest';
import {OFFICIAL_FEES_URL,OFFICIAL_SCHOOLS_URL,parseOfficialFees,parseOfficialSchools,officialSchoolsAround} from '../shared/official-property-context';
const schoolData={schema_version:1,source:OFFICIAL_SCHOOLS_URL,rows:[['B000002123','학교','elementary','서울특별시 송파구',127.1204994,37.51343915,'2026-10-01']]};
const feeData={schema_version:1,source:OFFICIAL_FEES_URL,unit:'KRW',semantics:'reported_complex_line_items',month:'202607',rows:[['A10025850','단지',[['급여',10000],['공동전기료',0],['정정',-50]]]]};
describe('official school and fee source contracts',()=>{
  it('uses official school identifiers and straight-line metres without claiming assignment',()=>{
    const rows=parseOfficialSchools(schoolData),view=officialSchoolsAround(rows,{longitude:127.1204994,latitude:37.51343915},{complexId:'selected',releaseId:'pinned'});
    expect(view.status).toBe('ready');if(view.status!=='ready')return;
    expect(view.scope).toEqual({complexId:'selected',releaseId:'pinned'});expect(view.complete).toBe(false);
    expect(view.records[0].distanceMeters).toBe(0);expect(view.records[0].position?.method).toBe('official_school_location');
    expect(officialSchoolsAround(rows,{longitude:126.5,latitude:37.5},view.scope)).toMatchObject({records:[]});
  });
  it('rejects duplicate schools, invalid coordinates and the wrong provider',()=>{
    expect(()=>parseOfficialSchools({...schoolData,rows:[...schoolData.rows,...schoolData.rows]})).toThrow();
    expect(()=>parseOfficialSchools({...schoolData,source:'https://example.com/'})).toThrow();
    expect(()=>parseOfficialSchools({...schoolData,rows:[['B000002123','학교','elementary','주소',NaN,37,'2026-10-01']]})).toThrow();
  });
  it('keeps actual zero and signed adjustments, indexed by official code only',()=>{
    const data=parseOfficialFees(feeData);expect(data.month).toBe('202607');expect(data.rows.get('A10025850')?.items).toEqual([['급여',10000],['공동전기료',0],['정정',-50]]);
    expect(data.rows.get('같은 단지명')).toBeUndefined();
  });
  it('rejects per-square-metre substitution, duplicate items and unsafe amounts',()=>{
    expect(()=>parseOfficialFees({...feeData,semantics:'per_square_metre'})).toThrow();
    expect(()=>parseOfficialFees({...feeData,rows:[['A10025850','단지',[['급여',1],['급여',2]]]]})).toThrow();
    expect(()=>parseOfficialFees({...feeData,rows:[['A10025850','단지',[['급여',Number.MAX_SAFE_INTEGER+1]]]]})).toThrow();
  });
});
