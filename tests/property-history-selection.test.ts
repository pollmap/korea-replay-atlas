import {expect,it} from 'vitest';
import {historyRecordLimit,historySelectionIndex} from '../src/property-history-selection';
it('opens a selected contract beyond the collapsed table without losing already expanded rows',()=>{
  const rows=Array.from({length:60},(_,i)=>({id:`source-${i}`}));
  expect(historyRecordLimit(rows,'source-23',12)).toBe(24);
  expect(historyRecordLimit(rows,'source-5',36)).toBe(36);
});
it('keeps identical-looking transactions distinct by source row identity',()=>{
  const rows=[{id:'first',date:'2026-06-05',price:2790000000},{id:'second',date:'2026-06-05',price:2790000000}];
  expect(historyRecordLimit(rows,'second',1)).toBe(2);
  expect(historySelectionIndex(rows,'second')).toBe(1);
});
it('never selects the first remaining transaction when a filter removes the target',()=>{
  expect(historyRecordLimit([{id:'visible'}],'hidden',12)).toBeNull();
  expect(historySelectionIndex([{id:'visible'}],'hidden')).toBe(-1);
  expect(historySelectionIndex([],undefined)).toBe(-1);
});
