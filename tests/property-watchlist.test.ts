import {expect,it} from 'vitest';
import {parseWatchlist,watchlistJson,mergeWatchlist,type WatchedComplex} from '../shared/property-watchlist';
import {historyMonths,historyTick} from '../shared/property-history';
import {readPropertyView} from '../shared/property-view';
const row:WatchedComplex={id:'molit-apt:11710:11710-8865',regionCode:'11710',name:'단지',address:'가락동 913',savedAt:'2026-09-25T00:00:00Z'};
it('imports supported identifiers without cross-region or malformed references',()=>{
  expect(parseWatchlist(watchlistJson([row]))).toEqual([row]);
  for(const invalid of [{...row,regionCode:'11110'},{...row,id:'molit-villa:11710:test'},{...row,savedAt:'invalid'},null])expect(()=>parseWatchlist(JSON.stringify({schema_version:1,items:[invalid]}))).toThrow();
  expect(()=>parseWatchlist(watchlistJson([row,row]))).toThrow();
});
it('merges without overwriting existing names and enforces the storage bound',()=>{
  expect(mergeWatchlist([row],[{...row,name:'덮어쓸 이름'}])).toEqual([row]);
  const many=Array.from({length:100},(_,i)=>({...row,id:`molit-apt:11710:x${i}`}));
  expect(()=>mergeWatchlist(many,[row])).toThrow('100');
  expect(()=>parseWatchlist(' '.repeat(100_001))).toThrow('100KB');
});
it('restores ten-year history and keeps sparse chart labels readable',()=>{
  const months=historyMonths('202608',120);expect(months).toHaveLength(120);expect(months[0]).toBe('201609');expect(months.at(-1)).toBe('202608');
  expect(months.filter((_,i)=>historyTick(i,months.length)).length).toBeLessThanOrEqual(6);
  expect(readPropertyView('#historyMonths=120',{from:'201609',to:'202609',latest_complete_month:'202608'}).historyMonths).toBe(120);
});
