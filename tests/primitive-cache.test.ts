import {describe,expect,it} from 'vitest';
import {heapUnderPressure,PRIMITIVE_CACHE_LIMITS,primitiveCacheCost,primitiveCacheKey,PrimitiveReuseCache} from '../shared/primitive-cache';
import type {Asset} from '../shared/contracts';

const asset:Asset={id:'roads',sha256:'source-hash',layer:'infrastructure',format:'geojson',url:'/data/roads.geojson',source_id:'osm',version:'snapshot',count:2,bbox:[126,35,128,37],byte_length:1000};
type Resource={id:string;release:string;layer:string;destroyed:boolean};
const make=(id:string,layer='infrastructure',release='release-a'):Resource=>({id,layer,release,destroyed:false});

describe('bounded primitive ownership',()=>{
  it('reuses returning objects before parking departing objects and stays bounded over city round trips',()=>{
    let builds=0,disposals=0;
    const cache=new PrimitiveReuseCache<Resource>({bytes:12,vertices:12,files:2},r=>{expect(r.destroyed).toBe(false);r.destroyed=true;disposals++;});
    const build=(id:string)=>{builds++;return make(id);};
    const transition=(ids:string[],active:Resource[])=>{
      const returning=ids.map(id=>active.find(r=>r.id===id)??cache.take(id)??build(id));
      for(const resource of active)if(!ids.includes(resource.id))cache.put(resource.id,resource,{bytes:6,vertices:6});
      return returning;
    };
    let active=transition(['city-a-1','city-a-2'],[]);const originals=[...active];
    for(let iteration=0;iteration<10;iteration++){
      active=transition(['city-b-1','city-b-2'],active);
      expect(cache.snapshot()).toMatchObject({files:2,bytes:12,vertices:12});
      active=transition(['city-a-1','city-a-2'],active);
      expect(active[0]).toBe(originals[0]);expect(active[1]).toBe(originals[1]);
    }
    expect(builds).toBe(4);expect(disposals).toBe(0);expect(cache.snapshot().hits).toBe(38);
    cache.clear();expect(disposals).toBe(2);expect(active.every(r=>!r.destroyed)).toBe(true);
  });
  it.each([
    {limits:{bytes:10,vertices:100,files:10},cost:{bytes:6,vertices:1}},
    {limits:{bytes:100,vertices:10,files:10},cost:{bytes:1,vertices:6}},
    {limits:{bytes:100,vertices:100,files:1},cost:{bytes:1,vertices:1}},
  ])('evicts the oldest dormant object for each independent budget: $limits',({limits,cost})=>{
    const oldest=make('oldest'),newest=make('newest');
    const cache=new PrimitiveReuseCache<Resource>(limits,r=>{r.destroyed=true;});
    cache.put('oldest',oldest,cost);cache.put('newest',newest,cost);
    expect(oldest.destroyed).toBe(true);expect(newest.destroyed).toBe(false);expect(cache.take('newest')).toBe(newest);
    expect(cache.snapshot()).toMatchObject({files:0,bytes:0,vertices:0,evictions:1});
  });
  it('rejects oversized objects and frees dormant objects on quality or memory pressure reductions',()=>{
    const disposed:string[]=[],cache=new PrimitiveReuseCache<Resource>(PRIMITIVE_CACHE_LIMITS.high,r=>disposed.push(r.id));
    cache.put('large',make('large'),{bytes:PRIMITIVE_CACHE_LIMITS.high.bytes+1,vertices:1});
    cache.put('mid',make('mid'),{bytes:PRIMITIVE_CACHE_LIMITS.low.bytes+1,vertices:2000});
    expect(disposed).toEqual(['large']);cache.setLimits(PRIMITIVE_CACHE_LIMITS.low);expect(disposed).toEqual(['large','mid']);
    cache.put('small',make('small'),{bytes:100,vertices:2});cache.setLimits({bytes:0,vertices:0,files:0});
    cache.put('pressure',make('pressure'),{bytes:0,vertices:0});
    expect(disposed).toEqual(['large','mid','small','pressure']);expect(cache.snapshot()).toMatchObject({files:0,bytes:0,vertices:0});
  });
  it('retains the observed return and overhead working sets together across repeated low-quality visits',()=>{
    const mib=1024*1024,disposed:string[]=[];
    const cache=new PrimitiveReuseCache<Resource>(PRIMITIVE_CACHE_LIMITS.low,r=>disposed.push(r.id));
    // Source-coordinate/payload observations, not simulated frame-time claims.
    const observed=[
      {resource:make('return'),cost:{bytes:Math.ceil(4.75*mib),vertices:35038}},
      ...[10536,10536,10535].map((vertices,i)=>({resource:make(`overhead-${i}`),cost:{bytes:Math.ceil(7.24*mib/3),vertices}})),
    ];
    for(const {resource,cost} of observed)cache.put(resource.id,resource,cost);
    expect(cache.snapshot()).toMatchObject({files:4,vertices:66645,evictions:0});
    for(let visit=0;visit<3;visit++)for(const group of [observed.slice(0,1),observed.slice(1)]){
      const returning=group.map(item=>({...item,retained:cache.take(item.resource.id)}));
      for(const {resource,retained,cost} of returning){expect(retained).toBe(resource);cache.put(resource.id,resource,cost);}
    }
    expect(cache.snapshot()).toMatchObject({files:4,vertices:66645,hits:12,evictions:0});expect(disposed).toEqual([]);
    expect(cache.snapshot().bytes).toBeLessThanOrEqual(PRIMITIVE_CACHE_LIMITS.low.bytes);
    for(const [lower,higher] of [['low','balanced'],['balanced','high']] as const){
      expect(PRIMITIVE_CACHE_LIMITS[lower].bytes).toBeLessThan(PRIMITIVE_CACHE_LIMITS[higher].bytes);
      expect(PRIMITIVE_CACHE_LIMITS[lower].vertices).toBeLessThan(PRIMITIVE_CACHE_LIMITS[higher].vertices);
      expect(PRIMITIVE_CACHE_LIMITS[lower].files).toBeLessThan(PRIMITIVE_CACHE_LIMITS[higher].files);
    }
  });
  it('purges both disabled layers and old releases without destroying retained objects',()=>{
    const cache=new PrimitiveReuseCache<Resource>({bytes:100,vertices:100,files:10},r=>{r.destroyed=true;});
    const old=make('old','rail','release-old'),disabled=make('disabled','buildings'),kept=make('kept','rail');
    for(const r of [old,disabled,kept])cache.put(r.id,r,{bytes:1,vertices:1});
    cache.retain(r=>r.release==='release-a'&&r.layer==='rail');
    expect(old.destroyed&&disabled.destroyed).toBe(true);expect(cache.take('kept')).toBe(kept);expect(kept.destroyed).toBe(false);
  });
  it('separates exact release, payload and overview variants while retaining all rail geometry',()=>{
    expect(new Set([primitiveCacheKey('a',asset,false),primitiveCacheKey('a',asset,true),primitiveCacheKey('b',asset,false),primitiveCacheKey('a',{...asset,sha256:'new'},false)]).size).toBe(4);
    expect(primitiveCacheKey('a',{...asset,layer:'rail'},true)).toBe(primitiveCacheKey('a',{...asset,layer:'rail'},false));
    expect(primitiveCacheCost(asset,{featureCount:2,vertexCount:10,pointCount:1})).toEqual({bytes:1736,vertices:10});
    expect(primitiveCacheCost({...asset,byte_length:undefined},{featureCount:3,vertexCount:10,pointCount:0})).toEqual({bytes:1680,vertices:10});
  });
  it('uses heap pressure only for a valid supported measurement',()=>{
    expect(heapUnderPressure()).toBe(false);expect(heapUnderPressure({usedJSHeapSize:100,jsHeapSizeLimit:0})).toBe(false);
    expect(heapUnderPressure({usedJSHeapSize:NaN,jsHeapSizeLimit:100})).toBe(false);expect(heapUnderPressure({usedJSHeapSize:74,jsHeapSizeLimit:100})).toBe(false);
    expect(heapUnderPressure({usedJSHeapSize:75,jsHeapSizeLimit:100})).toBe(true);
  });
});
