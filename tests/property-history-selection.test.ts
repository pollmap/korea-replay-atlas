import {expect,it} from 'vitest';
import {HISTORY_TABLE_PAGE_SIZE,historyRecordWindow,historyTableWindow,historySelectionIndex} from '../src/property-history-selection';
const initial={scope:'query',start:0,expanded:false};
it('starts with twelve rows and expands only to a bounded 48-row page',()=>{
  expect(historyTableWindow(initial,'query',20000)).toMatchObject({start:0,end:12});
  expect(historyTableWindow({...initial,expanded:true},'query',20000)).toMatchObject({start:0,end:48});
});
it('jumps directly to an old selected record without mounting its predecessors',()=>{
  const rows=Array.from({length:20000},(_,i)=>({id:`source-${i}`}));
  const target=historyRecordWindow(rows,'source-19999',initial,'query')!;
  const window=historyTableWindow(target,'query',rows.length);
  expect(window.start).toBe(19968);expect(window.end).toBe(20000);
  expect(window.end-window.start).toBeLessThanOrEqual(HISTORY_TABLE_PAGE_SIZE);
  expect(rows.slice(window.start,window.end)).toContain(rows[19999]);
  const first=historyRecordWindow(rows,'source-0',target,'query')!;
  expect(historyTableWindow(first,'query',rows.length)).toMatchObject({start:0,end:48});
});
it('all pages cover the full set exactly once and do not grow the DOM',()=>{
  const covered:number[]=[];
  for(let start=0;start<20000;start+=48){
    const window=historyTableWindow({...initial,start,expanded:true},'query',20000);
    expect(window.end-window.start).toBeLessThanOrEqual(48);
    for(let i=window.start;i<window.end;i++)covered.push(i);
  }
  expect(covered).toEqual(Array.from({length:20000},(_,i)=>i));
});
it('resets filters or release scope to twelve and clamps removed pages to remaining data',()=>{
  const old={scope:'query',start:19968,expanded:true};
  expect(historyTableWindow(old,'new-filter',20000)).toMatchObject({start:0,end:12,expanded:false});
  expect(historyTableWindow(old,'new-release',20000)).toMatchObject({start:0,end:12,expanded:false});
  expect(historyTableWindow(old,'query',49)).toMatchObject({start:48,end:49});
  expect(historyTableWindow(old,'query',0)).toMatchObject({start:0,end:0});
  expect(historyTableWindow({...old,start:-48},'query',100)).toMatchObject({start:0,end:48});
});
it('keeps duplicate-looking contracts distinct and never substitutes a removed ID',()=>{
  const rows=[{id:'first',date:'2026-06-05',price:2790000000},{id:'second',date:'2026-06-05',price:2790000000}];
  expect(historyRecordWindow(rows,'second',initial,'query')).toMatchObject({start:0,end:2});
  expect(historySelectionIndex(rows,'second')).toBe(1);
  expect(historyRecordWindow(rows,'missing',initial,'query')).toBeNull();
  expect(historySelectionIndex(rows,'missing')).toBe(-1);
  expect(historySelectionIndex([],undefined)).toBe(-1);
});
