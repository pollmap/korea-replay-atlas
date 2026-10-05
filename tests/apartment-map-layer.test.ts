import {describe,it,expect} from 'vitest';
import {validateStyleMin,createExpression} from '@maplibre/maplibre-gl-style-spec';
import {apartmentMapLayer,type ApartmentLabelMode} from '../src/apartment-map-layer';
import {regionMapLayer,provinceMapLayer,REGION_MAP_IMAGE} from '../src/region-map-layer';

const release='property-current';
function label(properties:Record<string,unknown>,trade:'sale'|'rent'='sale',selected=false,mode:ApartmentLabelMode='price-area',filterKey=''){
  const parsed=createExpression(apartmentMapLayer('apartments',release,trade,selected,mode,filterKey).layout!['text-field'] as unknown[],'layers[0].layout.text-field');
  if(parsed.result==='error')throw new Error(JSON.stringify(parsed.value));
  return parsed.value.evaluate({zoom:15},{type:1,properties}).toString();
}
describe('consistent property map labels',()=>{
  it('keeps apartment identity with missing-condition states while retaining compact confirmed prices',()=>{
    const row={name:'검증 아파트',property_filter_key:'selected',filtered_contract_date:'',filtered_label:'미수집 포함',filtered_price_label:'미수집 포함',filtered_unit_label:'미수집 포함'};
    for(const mode of ['price-area','price','unit-price'] as const){
      expect(label(row,'sale',false,mode,'selected')).toBe('검증 아파트\n미수집 포함');
      expect(label(row,'sale',true,mode,'selected')).toBe('검증 아파트\n미수집 포함');
      expect(label(row,'sale',false,mode,'next')).toBe('검증 아파트\n조건 확인 중');
    }
    expect(label({...row,filtered_contract_date:'2026-08-15',filtered_price_label:'9억'},'sale',false,'price','selected')).toBe('9억');
    expect(label(row,'sale',false,'name','selected')).toBe('검증 아파트');
  });
  it('keeps region cards through the middle zoom range until apartment cards start, retaining the shared card sprite',()=>{
    const region=regionMapLayer();expect(region.maxzoom).toBe(17);
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
  it('lets the user choose one compact apartment value while preserving identity on selection',()=>{
    const row={name:'검증 아파트',property_complex_id:'molit-apt:11710:1',property_release_id:release,recent_sale_label:'최근 신고 9억 · 84㎡',recent_sale_area_m2:'84',recent_sale_contract_date:'2026-08-15'};
    expect(label(row)).toBe('9억 · 84㎡');
    expect(label(row,'sale',false,'price')).toBe('9억');
    expect(label(row,'sale',false,'area')).toBe('84㎡');
    expect(label(row,'sale',false,'name')).toBe('검증 아파트');
    expect(label(row,'sale',true)).toBe('검증 아파트\n9억 · 84㎡');
    expect(label(row,'sale',true,'area')).toBe('검증 아파트\n84㎡');
    expect(label({name:'공식 단지'})).toBe('단지 미연결');
    expect(label({name:'공식 단지'},'sale',false,'name')).toBe('공식 단지');
  });
  it('never labels stale or sale-only prices as current rental data',()=>{
    const row={name:'검증 아파트',property_complex_id:'molit-apt:11710:1',property_release_id:release,recent_sale_label:'최근 신고 9억 · 84㎡',recent_sale_contract_date:'2026-08-15'};
    expect(label(row,'rent')).toBe('전월세 미연결');
    expect(label({...row,property_release_id:'different'})).toBe('단지 미연결');
    expect(label(row,'rent',false,'area')).toBe('전월세 미연결');
  });
});

it('hides disqualified ordinary price cards while retaining unknown state and the selected card',()=>{
  const evaluate=(filter:unknown[],properties:Record<string,unknown>)=>{
    const expression=createExpression(filter,'layers[0].filter');
    if(expression.result==='error')throw new Error(JSON.stringify(expression.value));
    return expression.value.evaluate({zoom:15},{type:1,properties});
  };
  const ordinary=apartmentMapLayer('apartments',release,'sale').filter! as unknown[];
  expect(evaluate(ordinary,{name:'조건 밖',filtered_price_match:false})).toBe(false);
  expect(evaluate(ordinary,{name:'조건 일치',filtered_price_match:true})).toBe(true);
  expect(evaluate(ordinary,{name:'확인 중'})).toBe(true);
  const selected=apartmentMapLayer('apartments',release,'sale',true).filter! as unknown[];
  // Map2D installs the actual selected kapt_code after the source is ready.
  expect(evaluate([...selected.slice(0,2),'k1'],{name:'선택 단지',kapt_code:'k1',filtered_price_match:false})).toBe(true);
});
