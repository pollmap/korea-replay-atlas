import {describe,expect,it} from 'vitest';
import {vectorLayers} from '../src/vector-style';
import {mapCoverageLabel,validateMapCatalog2D,type MapTileTopic,type MapCatalog2D} from '../shared/map-tiles';

const topic=(id:string):MapTileTopic=>({id,source_layer:id,minzoom:14,maxzoom:14,
  feature_count:1,bounds:[126,37,128,38],chunks:[],details:[],description:'test',geometry_precision:'display'});

describe('regional detailed roads',()=>{
  it('uses road classification and line labels with its own source identity',()=>{
    const base=vectorLayers(topic('roads'),'vector-roads');
    const detail=vectorLayers(topic('detail-roads'),'vector-detail-roads');
    expect(detail.map(l=>l.type)).toEqual(base.map(l=>l.type));
    expect(new Set([...base,...detail].map(l=>l.id)).size).toBe(base.length+detail.length);
    for(let i=0;i<detail.length;i++){
      expect(detail[i].paint).toEqual(base[i].paint);
      expect(detail[i].layout).toEqual(base[i].layout);
      expect(detail[i]).toMatchObject({source:'vector-detail-roads','source-layer':'detail-roads'});
    }
    expect(detail.find(l=>l.type==='symbol')?.layout).toMatchObject({'symbol-placement':'line'});
  });
  it('retains the existing building fill and official boundary styles',()=>{
    expect(vectorLayers(topic('buildings'),'buildings').some(l=>l.type==='fill')).toBe(true);
    expect(vectorLayers(topic('buildings'),'buildings').find(l=>l.type==='line')).toMatchObject({filter:['==',['geometry-type'],'LineString']});
    expect(vectorLayers(topic('admin-sido'),'admin').map(l=>l.type)).toEqual(['line','symbol']);
  });
});

describe('regional detail coverage disclosure',()=>{
  const catalog=():MapCatalog2D=>({schema_version:1,release_id:'map2d-'+'a'.repeat(20),bounds:[124,33,132,39],
    source_release_id:'original',sources:[],reference_dates:{sgis:'2025-06-30'},attribution:'sources',
    topics:[{...topic('buildings'),feature_count:0}],regional_detail:{regions:[{name:'서울특별시',sgis_code:'11',native_geometry_sha256:'a'.repeat(64)},
      {name:'인천광역시',sgis_code:'23',native_geometry_sha256:'b'.repeat(64)}],reference_date:'2025-06-30',topics:['buildings'],
      source_assets_complete:true,selection_rule:'intersects',coverage_claim:'source coverage only'}});
  it('labels nationwide base coverage separately from regional detail',()=>{
    const c=validateMapCatalog2D(catalog());expect(mapCoverageLabel(c)).toBe('전국 기본 지도 · 서울특별시·인천광역시 상세');
    delete c.regional_detail;expect(mapCoverageLabel(c)).toBe('전국 벡터 지도 · 1개 주제');
  });
  it('rejects unbuilt detail topics and ambiguous region provenance',()=>{
    for(const mutate of [(c:MapCatalog2D)=>{c.regional_detail!.topics=['absent'];},
      (c:MapCatalog2D)=>{c.regional_detail!.regions.push(c.regional_detail!.regions[0]);},
      (c:MapCatalog2D)=>{c.regional_detail!.source_assets_complete=false;},
      (c:MapCatalog2D)=>{c.regional_detail!.regions[0].native_geometry_sha256='';}]){
      const c=catalog();mutate(c);expect(()=>validateMapCatalog2D(c)).toThrow('regional detail');
    }
  });
});
