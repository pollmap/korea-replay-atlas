import {describe,expect,it} from 'vitest';
import {compileGeometry,renderFeatureProperties,resolveFeatureProperties,type GeoCollection} from '../shared/geometry';
import {displayedBuildingHeight,selectedProperties} from '../shared/selection';

const collection:GeoCollection={type:'FeatureCollection',metadata:{schema_version:1,shared:{source_id:'osm',dataset_version:'source-2026',kind:'road',description:'원본 상세 설명',quality_flags:['height_semantics_unresolved'],evidence_type:'source_attribute',height:40,render_height:null,render_eligible:false},rows:[{source_record_id:'way/row-road',kind:'road',class:'primary',description:'행 설명'},{source_record_id:'node/row-station',kind:'station',name:'행 이름',height_source:'official',height_semantics:'ground_to_top',quality_state:'verified'},{source_record_id:'way/row-building',base_height:-2.5,min_height:6,render_min_height:0,raw_height:.01,quality_state:'review_required'}]},features:[
  {type:'Feature',properties:{metadata_index:0,name:'원본 도로',highway:'motorway'},geometry:{type:'MultiLineString',coordinates:[[[127.0123456789,36.2,-1.125],[127.1123456789,36.3,5.25]],[[127.2,36.4],[127.3,36.5]]]}},
  {type:'Feature',id:'original-station',properties:{metadata_index:1,name:'원본 역',render_height:14,render_eligible:true},geometry:{type:'Point',coordinates:[127.000000001,36.000000002,7]}},
  {type:'Feature',properties:{metadata_index:2,name:'검토 건물',kind:'building',height:999},geometry:{type:'Polygon',coordinates:[[[127,36],[127.1,36],[127.1,36.1],[127,36]],[[127.01,36.01],[127.02,36.01],[127.02,36.02],[127.01,36.01]]]}},
  {type:'Feature',properties:{source_record_id:'node/no-row',name:'공유 속성',kind:'port'},geometry:{type:'Point',coordinates:[127.4,36.4]}},
  {type:'Feature',properties:{metadata_index:999},geometry:null},
]};
const renderKeys=['kind','name','class','highway','height','render_height','render_eligible','base_height','min_height','render_min_height'];

describe('compact geometry worker transfer',()=>{
  it('keeps shared/row records encoded and restores every original attribute after buffer transfer',()=>{
    const source=structuredClone(collection),expanded=compileGeometry(source),encoded=compileGeometry(source,false,{preserveMetadata:true});
    const positions=Array.from(expanded.coordinates);
    expect(encoded.metadata).toBe(source.metadata);expect(encoded.features[0].properties).toBe(source.features[0].properties);
    expect(encoded.features[0].properties).not.toHaveProperty('description');
    const transferred=structuredClone(encoded,{transfer:[encoded.coordinates.buffer as ArrayBuffer]});
    expect(encoded.coordinates.byteLength).toBe(0);expect(Array.from(transferred.coordinates)).toEqual(positions);
    expect(transferred.parts).toEqual(expanded.parts);expect(transferred.vertexCount).toBe(expanded.vertexCount);
    expect(transferred.features.map(feature=>feature.id)).toEqual(expanded.features.map(feature=>feature.id));
    for(let i=0;i<expanded.features.length;i++){
      const properties=resolveFeatureProperties(transferred.features[i],transferred.metadata);
      expect(properties).toEqual(expanded.features[i].properties);expect(properties).not.toHaveProperty('metadata_index');
      expect(selectedProperties(transferred.features[i].id,properties,{source_id:'fallback',version:'fallback'})).toEqual(selectedProperties(expanded.features[i].id,expanded.features[i].properties,{source_id:'fallback',version:'fallback'}));
    }
    expect(source).toEqual(collection);
  });

  it('projects only rendering fields while preserving row/raw precedence and the height contract',()=>{
    const expanded=compileGeometry(collection),encoded=compileGeometry(collection,false,{preserveMetadata:true});
    for(let i=0;i<expanded.features.length;i++){
      const original=expanded.features[i].properties,render=renderFeatureProperties(encoded.features[i],encoded.metadata);
      expect(render).toEqual(Object.fromEntries(renderKeys.filter(key=>Object.hasOwn(original,key)).map(key=>[key,original[key]])));
      expect(displayedBuildingHeight(render)).toBe(displayedBuildingHeight(original));
      expect(render).not.toHaveProperty('description');expect(render).not.toHaveProperty('quality_flags');
    }
    expect(renderFeatureProperties(encoded.features[1],encoded.metadata)).toMatchObject({kind:'station',name:'원본 역',height:40,render_height:14,render_eligible:true});
    expect(displayedBuildingHeight(renderFeatureProperties(encoded.features[2],encoded.metadata))).toBeUndefined();
  });

  it.each([null,undefined,0])('does not fall back to raw height when render_height is explicitly %s',renderHeight=>{
    const encoded=compileGeometry({type:'FeatureCollection',metadata:{schema_version:1,shared:{height:90,render_height:50,render_eligible:true},rows:[{render_height:20}]},features:[{type:'Feature',properties:{metadata_index:0,render_height:renderHeight},geometry:{type:'Point',coordinates:[127,36]}}]},false,{preserveMetadata:true});
    const render=renderFeatureProperties(encoded.features[0],encoded.metadata);
    expect(Object.hasOwn(render,'render_height')).toBe(true);expect(render.render_height).toBe(renderHeight);expect(displayedBuildingHeight(render)).toBeUndefined();
  });

  it('preserves source IDs and part ownership while omitting only unreferenced dictionary rows',()=>{
    const source=structuredClone(collection);
    source.features.push({type:'Feature',properties:{kind:'ferry_route'},geometry:{type:'Point',coordinates:[127,36]}});
    const expanded=compileGeometry(source,true),encoded=compileGeometry(source,true,{preserveMetadata:true});
    expect(encoded.features.map(feature=>feature.id)).toEqual(['original-station','way/row-building','node/no-row']);
    expect(encoded.features[0].properties.metadata_index).toBe(0);expect(encoded.metadata?.rows).toEqual([source.metadata!.rows[1],source.metadata!.rows[2]]);
    expect(source.features[1].properties?.metadata_index).toBe(1);expect(source.metadata?.rows).toHaveLength(3);
    expect(encoded.parts).toEqual(expanded.parts);expect(encoded.coordinates).toEqual(expanded.coordinates);
    expect(encoded.features.map(feature=>resolveFeatureProperties(feature,encoded.metadata))).toEqual(expanded.features.map(feature=>feature.properties));
  });

  it('does not clone source attributes belonging only to hidden roads, including an empty far view',()=>{
    let omittedReads=0;
    const source:GeoCollection={type:'FeatureCollection',metadata:{schema_version:1,shared:{source_id:'osm'},rows:[{kind:'road',get description(){omittedReads++;return '숨긴 도로 설명';}},{kind:'station',source_record_id:'node/kept'}]},features:[{type:'Feature',properties:{metadata_index:0},geometry:{type:'LineString',coordinates:[[127,36],[127.1,36.1]]}},{type:'Feature',properties:{metadata_index:1},geometry:{type:'Point',coordinates:[127,36]}}]};
    const compact=compileGeometry(source,true,{preserveMetadata:true}),transferred=structuredClone(compact,{transfer:[compact.coordinates.buffer as ArrayBuffer]});
    expect(omittedReads).toBe(0);expect(transferred.metadata?.rows).toEqual([{kind:'station',source_record_id:'node/kept'}]);
    expect(resolveFeatureProperties(transferred.features[0],transferred.metadata)).toEqual({source_id:'osm',kind:'station',source_record_id:'node/kept'});
    const empty=compileGeometry({...source,features:[source.features[0]]},true,{preserveMetadata:true});
    expect(empty.features).toEqual([]);expect(empty.metadata?.rows).toEqual([]);expect(structuredClone(empty).vertexCount).toBe(0);expect(omittedReads).toBe(0);
  });

  it('keeps a retained row shared across multiple source features after remapping',()=>{
    const source:GeoCollection={type:'FeatureCollection',metadata:{schema_version:1,shared:{source_id:'osm'},rows:[{kind:'road'},{kind:'station',description:'공유 행'}]},features:['one','two'].map(id=>({type:'Feature',id,properties:{metadata_index:1,name:id},geometry:{type:'Point',coordinates:[127,36]}}))};
    const compact=compileGeometry(source,false,{preserveMetadata:true});
    expect(compact.metadata?.rows).toHaveLength(1);expect(compact.features.map(feature=>feature.properties.metadata_index)).toEqual([0,0]);
    expect(compact.features.map(feature=>resolveFeatureProperties(feature,compact.metadata))).toEqual(compileGeometry(source).features.map(feature=>feature.properties));
    expect(source.features.map(feature=>feature.properties?.metadata_index)).toEqual([1,1]);
  });

  it('retains the default expanded contract and ordinary v1 GeoJSON behavior',()=>{
    const expanded=compileGeometry(collection);expect(expanded).not.toHaveProperty('metadata');expect(expanded.features[0].properties.description).toBe('행 설명');
    const plain:GeoCollection={type:'FeatureCollection',features:[{type:'Feature',properties:{name:'기존 자료',height:12},geometry:{type:'Point',coordinates:[127,36]}}]};
    const current=compileGeometry(plain,false,{preserveMetadata:true});expect(current).toEqual(compileGeometry(plain));expect(current).not.toHaveProperty('metadata');
    expect(resolveFeatureProperties(current.features[0],current.metadata)).toBe(plain.features[0].properties);
    expect(renderFeatureProperties(current.features[0],current.metadata)).toBe(plain.features[0].properties);
  });

  it.each([-1,3,1.5,'0',NaN])('rejects an invalid dictionary row %s before transfer',row=>{
    const source:GeoCollection={...collection,features:[{...collection.features[0],properties:{metadata_index:row}}]};
    expect(()=>compileGeometry(source,false,{preserveMetadata:true})).toThrow('행 번호');
  });
});
