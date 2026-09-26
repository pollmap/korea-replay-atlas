import {describe,it,expect} from 'vitest';
import {DecodedMapTileCache} from '../src/map-tile-cache';

describe('decoded map tile ownership and budget',()=>{
  it('survives detached MapLibre transfer buffers and caller mutation',()=>{
    const cache=new DecodedMapTileCache(10),input=new Uint8Array([1,2,3]).buffer;
    cache.set('tile',input);structuredClone(input,{transfer:[input]});
    const first=cache.get('tile')!;new Uint8Array(first)[0]=9;
    structuredClone(first,{transfer:[first]});
    expect([...new Uint8Array(cache.get('tile')!)]).toEqual([1,2,3]);
  });
  it('evicts least recently used bytes, counts replacements, and caps empty entries',()=>{
    const cache=new DecodedMapTileCache(6,2);
    cache.set('a',new ArrayBuffer(3));cache.set('b',new ArrayBuffer(3));cache.get('a');
    cache.set('c',new ArrayBuffer(3));expect(cache.get('b')).toBeUndefined();
    cache.set('a',new ArrayBuffer(1));expect(cache.snapshot().decodedTileBytes).toBe(4);
    cache.set('d',new ArrayBuffer(0));cache.set('e',new ArrayBuffer(0));
    expect(cache.snapshot().decodedTileCount).toBe(2);expect(cache.snapshot().decodedTileBytes).toBe(0);
    cache.set('large',new ArrayBuffer(7));expect(cache.get('large')).toBeUndefined();
    cache.clear();expect(cache.snapshot().decodedTileCount).toBe(0);
  });
  it('supports a measured uncached comparison without changing returned content',()=>{
    const cache=new DecodedMapTileCache(0);cache.set('tile',new ArrayBuffer(4));
    expect(cache.get('tile')).toBeUndefined();expect(cache.snapshot().decodedTileBytes).toBe(0);
  });
});
