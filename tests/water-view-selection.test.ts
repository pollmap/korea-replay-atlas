import {describe,expect,it} from 'vitest';
import type {Asset,LayerId} from '../shared/contracts';
import {selectViewAssets} from '../shared/map-performance';

const layers=Object.fromEntries(['terrain','buildings','infrastructure','rail','bus','depth','satellite','radar','sun'].map(key=>[key,true])) as Record<LayerId,boolean>;
const original:Asset={id:'osm-water-korea',layer:'terrain',format:'geojson',url:'/data/osm/water.geojson',
  bbox:[124.5,33,132,38.7],source_id:'osm',version:'fixture',sha256:'original',count:540,feature_count:540,
  byte_length:2609440,vertex_count:97352,min_camera_height:60000};
const fragments:Asset[]=Array.from({length:162},(_,index)=>({...original,id:`water-cell-${index}`,url:`/data/water/${index}.geojson`,sha256:`cell-${index}`,
  bbox:[126+index%18*.25,33+Math.floor(index/18)*.25,126.24+index%18*.25,33.24+Math.floor(index/18)*.25],
  count:3,feature_count:3,byte_length:16000,vertex_count:600,min_camera_height:undefined,max_camera_height:60000,detail_level:'detail'}));
const view={bbox:original.bbox,height:60000,layers,mode:'sun' as const,dayStart:0,dayEnd:86400000,quality:'low' as const};

describe('scale-exclusive source-faithful water partitions',()=>{
  it('keeps the complete original at national scale despite the 36-file low-quality cap',()=>{
    for(const height of [60000,60001,1500000]){
      const result=selectViewAssets([original,...fragments],{...view,height});
      expect(result.assets.map(asset=>asset.id)).toEqual(['osm-water-korea']);
      expect(result.vertices).toBe(97352);
    }
  });
  it('uses intersecting local pieces below the boundary without drawing the original twice',()=>{
    const result=selectViewAssets([original,...fragments],{...view,height:59999,bbox:[126.01,33.01,126.23,33.23]});
    expect(result.assets.map(asset=>asset.id)).toEqual(['water-cell-0']);
    expect(result.vertices).toBe(600);
  });
  it('does not reintroduce the full national water file as an overloaded-city fallback',()=>{
    const roads:Asset[]=Array.from({length:80},(_,index)=>({...fragments[0],id:`road-${index}`,layer:'infrastructure',vertex_count:12000}));
    const result=selectViewAssets([original,...fragments,...roads],{...view,height:9000,bbox:[126.01,33.01,126.23,33.23]});
    expect(result.deferred).toBeGreaterThan(0);
    expect(result.assets.some(asset=>asset.id===original.id)).toBe(false);
    expect(result.vertices).toBeLessThanOrEqual(150000);
  });
  it('uses the complete feature bounds across partition cells, not the assignment cell',()=>{
    // A lake is assigned by its centre, but its true bounds cross a neighbouring cell.
    const crossing:Asset={...fragments[0],bbox:[126.2,33.1,126.45,33.2]};
    const result=selectViewAssets([original,crossing],{...view,height:9000,bbox:[126.4,33.11,126.44,33.19]});
    expect(result.assets.map(asset=>asset.id)).toEqual(['water-cell-0']);
  });
});
