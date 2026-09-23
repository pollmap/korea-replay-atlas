import {describe,expect,it} from 'vitest';
import {validateStyleMin} from '@maplibre/maplibre-gl-style-spec';
import type {PropertyRegion,RegionMetric} from '../shared/property';
import {pickedPropertyProvince,pickedPropertyRegion,provinceMapLayer,regionMapBubbleImage,regionMapData,regionMapLayer,PROVINCE_MAP_LAYER,REGION_MAP_LAYER,REGION_MAP_SOURCE,type RegionMapInput} from '../src/region-map-layer';

const release='property-0123456789abcdef';
const asset={url:`/data/property/${release}/regions.json`,sha256:'a'.repeat(64),bytes:10};
const coverage={expected:2,complete:2,empty:0,failed:0,pending:0,partial:0,historical_coverage:'current_codes_only_pending_effective_date_crosswalk' as const};
function region(overrides:Partial<PropertyRegion>={},metric:Partial<RegionMetric>={}):PropertyRegion {
  const sale:RegionMetric={lawd_code:overrides.lawd_code??'11710',deal_month:'202608',trade_type:'sale',status:'complete',source_rows:1300,eligible_rows:1234,cancelled_rows:60,invalid_rows:6,statistics_excluded_rows:66,complex_count:88,retrieved_at:'2026-09-20T00:00:00Z',median_price_per_m2_krw:10000000,statistic:'reported-row-median-price-per-m2',cancellation_policy:'exclude_cancelled_and_unknown',...metric};
  return {lawd_code:'11710',legal_code:'1171000000',name:'서울특별시 송파구',index:asset,coverage,latest:{sale,rent:{...sale,trade_type:'rent',cancellation_policy:'source_not_provided'}},...overrides};
}
function fixture(rows=[region()]):RegionMapInput {
  return {map:{reference_dates:{sgis:'2025-06-30'}},
    property:{release_id:release,period:{from:'202605',to:'202609',latest_complete_month:'202608'}},
    regions:{schema_version:1,kind:'property-regions',release_id:release,regions:rows}};
}

describe('region navigation volume labels',()=>{
  it('places the published completed-month count at the verified region anchor with explicit source and purpose',()=>{
    const result=regionMapData(fixture()),feature=result.data.features[0];
    expect(result).toMatchObject({month:'202608',releaseId:release,total:1,excluded:0});
    expect(feature.geometry).toEqual({type:'Point',coordinates:[127.10596,37.504822]});
    expect(feature.properties).toMatchObject({property_region_code:'11710',region_name:'서울특별시 송파구',display_name:'서울 송파구',count:1234,count_label:'1,234건',contract_month:'202608',trade_type:'sale',month_label:'26.08 매매',anchor_purpose:'region-navigation-only',anchor_reference_date:'2025-06-30',anchor_source_record_id:'sgis:20250630:sigungu:11240'});
    expect(result.caption).toBe('지역별 매매 거래량 · 2026.08 · 지역 탐색 위치');
    expect(result.notice).toContain('패널의 거래 유형과 연동');
    expect(result.notice).toContain('단지 좌표나 현행 법정동 경계의 통계 결합이 아닙니다');
  });
  it('uses the same completed-month trade type as the property panel',()=>{
    const input=fixture();input.trade='rent';
    const result=regionMapData(input);
    expect(result.data.features[0].properties).toMatchObject({trade_type:'rent',count:1234,month_label:'26.08 전월세'});
    expect(result.caption).toContain('전월세 거래량');
    expect(result.notice).toContain('과거 계약월 선택과는 별개');
  });
  it('aggregates published districts by province only when every member has a valid monthly count',()=>{
    const rows=[region(),region({lawd_code:'11680',name:'서울특별시 강남구'},{eligible_rows:10})];
    const current=regionMapData(fixture(rows));
    expect(current.provinces.features).toHaveLength(1);
    expect(current.provinces.features[0].properties).toMatchObject({property_province_name:'서울특별시',count_label:'1,244건',member_count:2,anchor_purpose:'province-navigation-only'});
    const hit={source:REGION_MAP_SOURCE,layer:{id:PROVINCE_MAP_LAYER},properties:{property_province_name:'서울특별시',property_release:release}};
    expect(pickedPropertyProvince([hit],current)).toEqual([126.939166,37.564879]);
    expect(pickedPropertyProvince([hit],current,'distance')).toBeNull();
    expect(regionMapData(fixture([rows[0],region({lawd_code:'11680',name:'서울특별시 강남구'},{status:'pending',eligible_rows:null})])).provinces.features).toHaveLength(0);
  });
  it('does not turn missing, failed, partial or unmatched regions into zero',()=>{
    for(const metric of [{status:'pending',eligible_rows:null},{status:'failed',eligible_rows:null},{status:'partial',eligible_rows:null}] as Partial<RegionMetric>[]){
      const result=regionMapData(fixture([region({},metric)]));expect(result.data.features).toEqual([]);expect(result.excluded).toBe(1);
    }
    for(const name of ['송파구','서울특별시 송파구 잠실동 1','전남광주통합특별시 동구','인천광역시 영종구','경기도 화성시 동탄구']){
      expect(regionMapData(fixture([region({name})])).data.features).toEqual([]);
    }
    expect(regionMapData(null).data.features).toEqual([]);
  });
  it('shows a verified empty result as zero but rejects an inconsistent empty result',()=>{
    const result=regionMapData(fixture([region({},{status:'empty',eligible_rows:0,source_rows:0})]));
    expect(result.data.features[0].properties.count_label).toBe('0건');
    expect(regionMapData(fixture([region({},{status:'empty',eligible_rows:1})])).data.features).toEqual([]);
  });
  it('rejects cross-release, mismatched month/trade/code and invalid counts',()=>{
    const changed=fixture();changed.regions.release_id='property-ffffffffffffffff';expect(regionMapData(changed).data.features).toEqual([]);
    for(const patch of [{deal_month:'202609'},{deal_month:'202607'},{trade_type:'rent'},{lawd_code:'11110'},{eligible_rows:-1},{eligible_rows:1.5},{eligible_rows:NaN},{eligible_rows:null},{eligible_rows:Number.MAX_SAFE_INTEGER+1}] as Partial<RegionMetric>[]){
      expect(regionMapData(fixture([region({},patch)])).data.features).toEqual([]);
    }
    for(const month of ['202613','202604','202610']){const input=fixture();input.property.period.latest_complete_month=month;expect(regionMapData(input).data.features).toEqual([]);}
  });
  it('does not use ambiguous duplicate codes or a different boundary release',()=>{
    const duplicate=regionMapData(fixture([region(),region()]));expect(duplicate.data.features).toEqual([]);expect(duplicate.excluded).toBe(2);
    const input=fixture();input.map.reference_dates.sgis='2026-06-30';expect(regionMapData(input).data.features).toEqual([]);
  });
  it('retains full region identity while larger transaction samples get earlier collision placement',()=>{
    const result=regionMapData(fixture([region(),region({lawd_code:'26110',name:'부산광역시 중구'},{eligible_rows:2})]));
    expect(result.data.features.map(row=>row.properties.sort_key)).toEqual([-1234,-2]);
    expect(result.data.features[1].properties.display_name).toBe('부산 중구');
    expect(result.data.features[1].geometry.coordinates[1]).toBeLessThan(36);
  });
});

describe('region volume map integration contracts',()=>{
  it('accepts only displayed source/layer/release picks and preserves measurement priority',()=>{
    const current=regionMapData(fixture());
    const hit={source:REGION_MAP_SOURCE,layer:{id:REGION_MAP_LAYER},properties:{property_region_code:'11710',property_release:release}};
    expect(pickedPropertyRegion([hit],current)).toBe('11710');
    for(const mode of ['distance','area'])expect(pickedPropertyRegion([hit],current,mode)).toBeNull();
    for(const changed of [{...hit,source:'vector-roads'},{...hit,layer:{id:'map2d-1-line'}},{...hit,properties:{...hit.properties,property_release:'property-ffffffffffffffff'}},{...hit,properties:{...hit.properties,property_region_code:'26110'}}])expect(pickedPropertyRegion([changed],current)).toBeNull();
    expect(pickedPropertyRegion([{source:'vector-roads',properties:{stable_id:'road-1'}},hit],current)).toBe('11710');
    expect(pickedPropertyRegion([hit],regionMapData(null))).toBeNull();
  });
  it('uses one valid collision-aware symbol layer and one fixed GeoJSON source',()=>{
    const layer=regionMapLayer();
    expect(validateStyleMin({version:8,sources:{[REGION_MAP_SOURCE]:{type:'geojson',data:regionMapData(fixture()).data}},layers:[layer]})).toEqual([]);
    expect(layer.type).toBe('symbol');
    if(layer.type==='symbol')expect(layer.layout).toMatchObject({'text-allow-overlap':false,'icon-allow-overlap':false,'icon-text-fit':'both'});
    expect(validateStyleMin({version:8,sources:{[REGION_MAP_SOURCE]:{type:'geojson',data:regionMapData(fixture()).provinces}},layers:[provinceMapLayer()]})).toEqual([]);
  });
  it('provides a bounded shared white bubble sprite with blue outline and transparent corners',()=>{
    const sprite=regionMapBubbleImage();expect(sprite.data.byteLength).toBe(sprite.width*sprite.height*4);expect(sprite.data.byteLength).toBeLessThan(32*1024);
    const pixel=(x:number,y:number)=>Array.from(sprite.data.slice((y*sprite.width+x)*4,(y*sprite.width+x)*4+4));
    expect(pixel(0,0)).toEqual([0,0,0,0]);expect(pixel(40,32)).toEqual([255,255,255,255]);expect(pixel(40,0)).toEqual([37,99,235,255]);
  });
});
