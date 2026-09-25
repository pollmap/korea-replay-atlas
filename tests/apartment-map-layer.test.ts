import {describe,it,expect} from 'vitest';
import {validateStyleMin,createExpression} from '@maplibre/maplibre-gl-style-spec';
import {apartmentMapLayer} from '../src/apartment-map-layer';
import {regionMapLayer,provinceMapLayer,REGION_MAP_IMAGE} from '../src/region-map-layer';

const release='property-current';
function label(properties:Record<string,unknown>,trade:'sale'|'rent'='sale'){
  const parsed=createExpression(apartmentMapLayer('apartments',release,trade).layout!['text-field'] as unknown[],'layers[0].layout.text-field');
  if(parsed.result==='error')throw new Error(JSON.stringify(parsed.value));
  return parsed.value.evaluate({zoom:15},{type:1,properties}).toString();
}
describe('consistent property map labels',()=>{
  it('keeps district names above zoom 10, using the same card sprite at every level',()=>{
    const region=regionMapLayer();expect(region.maxzoom??24).toBeGreaterThan(19);
    for(const layer of [provinceMapLayer(),region,apartmentMapLayer('apartments',release,'sale')]){
      expect(layer.type).toBe('symbol');
      if(layer.type==='symbol')expect(layer.layout?.['icon-image']).toBe(REGION_MAP_IMAGE);
    }
  });
  it('validates ordinary and selected symbol styles without circles or anonymous cluster labels',()=>{
    for(const trade of ['sale','rent'] as const)for(const selected of [false,true]){
      const layer=apartmentMapLayer('apartments',release,trade,selected);
      expect(validateStyleMin({version:8,sources:{apartments:{type:'geojson',data:{type:'FeatureCollection',features:[]}}},layers:[layer]})).toEqual([]);
      expect(JSON.stringify(layer)).not.toContain('point_count');
      expect(layer.layout?.['text-allow-overlap']).toBe(selected);
    }
  });
  it('always preserves the apartment name and identifies actual contract date',()=>{
    expect(label({name:'검증 아파트',property_complex_id:'molit-apt:11710:1',property_release_id:release,recent_sale_label:'최근 신고 9억 · 84㎡',recent_sale_contract_date:'2026-08-15'})).toBe('검증 아파트\n최근 신고 9억 · 84㎡\n2026-08-15');
    expect(label({name:'공식 단지'})).toBe('공식 단지\n공식 단지 정보');
  });
  it('never labels stale or sale-only prices as current rental data',()=>{
    const row={name:'검증 아파트',property_complex_id:'molit-apt:11710:1',property_release_id:release,recent_sale_label:'최근 신고 9억 · 84㎡',recent_sale_contract_date:'2026-08-15'};
    expect(label(row,'rent')).toBe('검증 아파트\n실거래 보기');
    expect(label({...row,property_release_id:'different'})).toBe('검증 아파트\n공식 단지 정보');
  });
});
