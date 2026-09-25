import {expect,it} from 'vitest';
import {EMPTY_PROPERTY_DISCOVERY_FILTERS} from '../shared/property-discovery';
import {checkedDiscoveryFilters,mergeSavedFilters,parseSavedFilters,savedFiltersJson,type SavedPropertyFilter} from '../shared/property-saved-filters';
const row:SavedPropertyFilter={id:'test-1',name:'국평 8억 이하',region:'11710',trade:'sale',savedAt:'2026-09-25T00:00:00Z',filters:{...EMPTY_PROPERTY_DISCOVERY_FILTERS,areaMinM2:'84',areaMaxM2:'84.99999',priceMaxEok:'8',hasTrades:true}};
it('roundtrips exact price, area, query and transaction type without changing zeros',()=>{
  expect(parseSavedFilters(savedFiltersJson([row]))).toEqual([row]);
  expect(checkedDiscoveryFilters({...row.filters,priceMaxEok:'0'},'sale').priceMaxEok).toBe('0');
  expect(parseSavedFilters(savedFiltersJson([{...row,trade:'rent'}]))[0].trade).toBe('rent');
});
it('merges imports without overwriting a locally changed search or losing unrelated regions',()=>{
  const rows=mergeSavedFilters([row],[{...row,name:'changed'},{...row,id:'test-2',region:'28110'}]);
  expect(rows.map(item=>item.name)).toEqual([row.name,row.name]);expect(rows[1].region).toBe('28110');
});
it('rejects impossible ranges, invalid sort, duplicate IDs and oversized imports before saving',()=>{
  expect(()=>checkedDiscoveryFilters({...row.filters,priceMinEok:'10',priceMaxEok:'3'},'sale')).toThrow();
  expect(()=>checkedDiscoveryFilters({...row.filters,sort:'__proto__'},'sale')).toThrow();
  expect(()=>checkedDiscoveryFilters({...row.filters,hasTrades:'false'},'sale')).toThrow();
  expect(()=>parseSavedFilters(savedFiltersJson([row,row]))).toThrow();
  expect(()=>parseSavedFilters(' '.repeat(100001))).toThrow();
  expect(()=>parseSavedFilters(savedFiltersJson(Array.from({length:51},(_,i)=>({...row,id:`test-${i}`}))))).toThrow();
});
