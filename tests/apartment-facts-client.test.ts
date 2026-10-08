import {expect,it,vi} from 'vitest';
import data from '../src/data/seoul-apartment-facts.json';
import {createAsyncApartmentFacts} from '../src/apartment-facts-client';
const first=data.property_release_id,second='property-1111111111111111';
const sample=(release=first)=>({...data,property_release_id:release,rows:[data.rows[0]]});
it('requests only the exact selected release and shares concurrent work',async()=>{
  const old=vi.fn(async()=>sample()),other=vi.fn(async()=>sample(second));
  const client=createAsyncApartmentFacts({[first]:old,[second]:other});
  const a=client.load(first),b=client.load(first);expect(a).toBe(b);
  const row=await a;expect(row?.release).toBe(first);expect(old).toHaveBeenCalledTimes(1);expect(other).not.toHaveBeenCalled();
  expect(await client.load(first)).toBe(row);expect(old).toHaveBeenCalledTimes(1);
  expect(await client.load('property-0000000000000000')).toBeUndefined();expect(other).not.toHaveBeenCalled();
});
it('rejects a different release and allows a failed download to retry',async()=>{
  const load=vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(sample(second)).mockResolvedValueOnce(sample());
  const client=createAsyncApartmentFacts({[first]:load});
  await expect(client.load(first)).rejects.toThrow('offline');expect(client.peek(first)).toBeUndefined();
  await expect(client.load(first)).rejects.toThrow('버전이 다릅니다');expect(client.peek(first)).toBeUndefined();
  expect((await client.load(first))?.release).toBe(first);expect(load).toHaveBeenCalledTimes(3);
});
it('bounds parsed indexes without borrowing facts from an evicted release',async()=>{
  const load=vi.fn(async()=>sample()),client=createAsyncApartmentFacts({[first]:load,[second]:async()=>sample(second)},1);
  await client.load(first);await client.load(second);expect(client.peek(first)).toBeUndefined();expect(client.peek(second)?.release).toBe(second);
  await client.load(first);expect(load).toHaveBeenCalledTimes(2);expect(client.peek(second)).toBeUndefined();
});
it('rejects malformed facts and cache limits',async()=>{
  const client=createAsyncApartmentFacts({[first]:async()=>({...sample(),rows:[{...data.rows[0],households:'5'}]})});
  await expect(client.load(first)).rejects.toThrow();expect(client.peek(first)).toBeUndefined();
  for(const capacity of [0,9,1.5])expect(()=>createAsyncApartmentFacts({},capacity)).toThrow();
});
