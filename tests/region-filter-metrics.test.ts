import {describe,it,expect} from 'vitest';
import {parseRegionFilterMetrics,selectedRegionMetric} from '../src/region-filter-metrics';
const release='property-0123456789abcdef';
const row={status:'complete' as const,count:3,median_per_m2:10000000,covered:36,expected:36,unavailable:0};
const fixture=()=>({schema_version:1 as const,kind:'property-region-filter-metrics' as const,property_release_id:release,regions:{'11710':{'202609|36|84-band|sale':{...row}}}});

describe('release-pinned region filters',()=>{
  it('selects exact period, area and trade and never substitutes latest month',()=>{
    const data=parseRegionFilterMetrics(fixture(),release);
    expect(selectedRegionMetric(data,release,'11710','202609',36,'84-band','sale')).toEqual(row);
    for(const end of ['202608','202610'])expect(selectedRegionMetric(data,release,'11710',end,36,'84-band','sale')).toBeUndefined();
    expect(selectedRegionMetric(data,'property-ffffffffffffffff','11710','202609',36,'84-band','sale')).toBeUndefined();
    expect(selectedRegionMetric(data,release,'11710','202609',36,'84-band','rent','jeonse')).toBeUndefined();
    expect(selectedRegionMetric(data,release,'11710','202609',36,'84','sale')).toBeUndefined();
  });
  it('rejects inconsistent coverage, unsafe counts and zero with fabricated price',()=>{
    for(const patch of [{count:-1},{count:1.5},{count:Number.MAX_SAFE_INTEGER+1},{covered:35},{unavailable:1},{count:0},{median_per_m2:NaN},{expected:240}]){
      const value=fixture();Object.assign(value.regions['11710']['202609|36|84-band|sale'],patch);
      expect(()=>parseRegionFilterMetrics(value,release)).toThrow();
    }
    expect(()=>parseRegionFilterMetrics(fixture(),'property-ffffffffffffffff')).toThrow();
  });
  it('keeps unavailable and partial periods explicit',()=>{
    const value=fixture();Object.assign(value.regions['11710']['202609|36|84-band|sale'],{status:'partial',covered:30});
    expect(parseRegionFilterMetrics(value,release).regions['11710']['202609|36|84-band|sale'].status).toBe('partial');
  });
});