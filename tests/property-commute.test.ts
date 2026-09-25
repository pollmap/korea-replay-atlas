import {expect,it} from 'vitest';
import {commuteUrl,parseCommuteDestinations} from '../shared/property-commute';
it('accepts only bounded versioned destination records and recovers from unavailable storage',()=>{
  for(const raw of [null,'{bad','{}','[]',JSON.stringify({version:2,items:[]})])expect(parseCommuteDestinations(raw)).toEqual([]);
  const items=[{label:'직장',address:'서울특별시 강남구'},{label:'',address:'bad'},{label:'학교',address:'x'.repeat(201)}];
  expect(parseCommuteDestinations(JSON.stringify({version:1,items}))).toEqual([items[0]]);
  expect(parseCommuteDestinations(JSON.stringify({version:1,items:Array(10).fill(items[0])}))).toHaveLength(3);
});
it('encodes text queries, fixes provider and modes, and never substitutes the user current location',()=>{
  const url=new URL(commuteUrl('서울 송파구 가락동 단지','강남구 A&B #학교','transit')!);
  expect(url.origin).toBe('https://www.google.com');expect(url.searchParams.get('api')).toBe('1');
  expect(url.searchParams.get('origin')).toBe('서울 송파구 가락동 단지');expect(url.searchParams.get('destination')).toBe('강남구 A&B #학교');
  expect(url.searchParams.get('travelmode')).toBe('transit');expect(url.hash).toBe('');
  expect(commuteUrl('','학교','transit')).toBeNull();expect(commuteUrl('단지','\n','walking')).toBeNull();
});
