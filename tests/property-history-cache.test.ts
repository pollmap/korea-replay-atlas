import {expect,it} from 'vitest';
import {HistoryMonthCache,historyMonthCacheKey} from '../src/property-history-cache';
import type {PropertyPartition,PropertyTransaction} from '../shared/property';

const row=(id:string)=>({id,issues:[],complex_name:'테스트'} as unknown as PropertyTransaction);
it('bounds retained rows, serialized bytes and empty-month entries with LRU eviction',()=>{
  const cache=new HistoryMonthCache({bytes:10000,rows:2,months:2});
  cache.set('a',[row('a')]);cache.set('b',[row('b')]);cache.get('a');cache.set('c',[row('c')]);
  expect(cache.get('b')).toBeUndefined();expect(cache.snapshot()).toMatchObject({months:2,rows:2});
  cache.set('large',[row('d'),row('e'),row('f')]);expect(cache.get('large')).toBeUndefined();
  cache.set('empty-1',[]);cache.set('empty-2',[]);cache.set('empty-3',[]);
  expect(cache.snapshot()).toMatchObject({months:2,rows:0});expect(cache.get('empty-1')).toBeUndefined();
  const small=new HistoryMonthCache({bytes:100,rows:20,months:256});
  small.set('one',[row('one')]);small.set('two',[row('two')]);
  expect(small.snapshot().serializedBytes).toBeLessThanOrEqual(100);expect(small.snapshot().months).toBe(1);
});
it('owns immutable rows and issues while letting callers edit their array',()=>{
  const cache=new HistoryMonthCache(),source=row('source');cache.set('month',[source]);
  source.complex_name='changed';source.issues.push({field:'name',code:'changed'});
  const got=cache.get('month')!;expect(got[0]).toMatchObject({complex_name:'테스트',issues:[]});
  got.pop();expect(cache.get('month')).toHaveLength(1);
  expect(()=>{cache.get('month')![0].complex_name='modified';}).toThrow();
});
it('accounts replacement entries correctly and keeps checked empty results distinct from absence',()=>{
  const cache=new HistoryMonthCache();cache.set('a',[row('a')]);cache.set('a',[]);
  expect(cache.snapshot()).toMatchObject({months:1,rows:0});expect(cache.get('a')).toEqual([]);expect(cache.get('b')).toBeUndefined();
});
it('keys identical selections independently of order and separates provenance and count changes',()=>{
  const partition={deal_month:'202608',trade_type:'sale',status:'complete',source_rows:2,eligible_rows:2,retrieved_at:'2026-09-28',transactions:[{url:'/data/month.json',sha256:'a'.repeat(64),bytes:42}]} as PropertyPartition;
  const key=(p=partition)=>historyMonthCacheKey('https://example.com','property-one','11110',p,new Set(['b','a']));
  expect(key()).toBe(historyMonthCacheKey('https://example.com','property-one','11110',partition,new Set(['a','b'])));
  expect(key({...partition,source_rows:3})).not.toBe(key());
  expect(key({...partition,transactions:[{...partition.transactions[0],bytes:43}]})).not.toBe(key());
});
