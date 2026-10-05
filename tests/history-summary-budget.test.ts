import {expect,it} from 'vitest';
import {assertSummaryReadBudget,retainSelectedSummaries} from '../src/property-summary-client';
import type {ComplexPriceSummary} from '../src/property-map-prices';

const MiB=1024*1024;
const compressed=(bytes:number,wire=bytes/8)=>({bytes,transport:{encoding:'gzip' as const,bytes:wire,sha256:'a'.repeat(64)}});
it('allows the observed dense 20-year regional gzip size but keeps both byte ceilings',()=>{
  expect(()=>assertSummaryReadBudget(Array.from({length:32},()=>compressed(4*MiB)),true)).not.toThrow();
  expect(()=>assertSummaryReadBudget(Array.from({length:33},()=>compressed(4*MiB)),true)).toThrow();
  expect(()=>assertSummaryReadBudget(Array.from({length:12},()=>compressed(4*MiB,3*MiB)),true)).toThrow();
});
it('preserves the smaller per-complex and legacy query bounds and the request-count ceiling',()=>{
  expect(()=>assertSummaryReadBudget([compressed(17*MiB)],false)).toThrow();
  expect(()=>assertSummaryReadBudget([{bytes:17*MiB}],true)).toThrow();
  expect(()=>assertSummaryReadBudget(Array.from({length:513},()=>compressed(1024)),true)).toThrow();
});
it('drops other periods, apartments, areas and rent kinds without changing a report count or price',()=>{
  const row={complex_id:'molit-apt:11710:11710-8865',deal_month:'202609',trade_type:'rent',rent_kind:'jeonse',area_m2:'84.99',transaction_count:7,latest_deposit_krw:800000000} as ComplexPriceSummary;
  const rows=[row,{...row,deal_month:'202608'},{...row,complex_id:'molit-apt:11710:other'},{...row,area_m2:'59.99'},{...row,rent_kind:'monthly' as const}];
  const selected=retainSelectedSummaries(rows,new Set(['202609']),row.complex_id,{trade:'rent',area:'84-band',rentKind:'jeonse'});
  expect(selected).toEqual([row]);expect(selected[0]).toBe(row);
  expect(retainSelectedSummaries(rows,new Set(['202609']),undefined,{trade:'sale'})).toEqual([]);
});
