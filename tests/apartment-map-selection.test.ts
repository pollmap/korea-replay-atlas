import {describe,expect,it} from 'vitest';
import {isApartmentMapSelection,type MapSelection} from '../shared/selection';

describe('apartment exploration selection boundary',()=>{
  it('keeps official apartment information available without claiming a transaction match',()=>{
    const value:MapSelection={name:'헬리오시티',detail:'제공 점 · 실거래 연결 미확인',sourceId:'kapt-fixture',provenance:{source_id:'seoul-openaptinfo'}};
    expect(isApartmentMapSelection(value)).toBe(true);
    expect(value.detail).toContain('미확인');
  });
  it.each(['osm','overture',undefined])('does not infer apartment identity from a background object name or tag (%s)',source=>{
    expect(isApartmentMapSelection({name:'아파트 앞 도로',detail:'배경',sourceId:'way/fixture',properties:{building:'apartments'},provenance:{source_id:source}})).toBe(false);
  });
  it('does not select an apartment source record without its identifier',()=>{
    expect(isApartmentMapSelection({name:'동명 아파트',detail:'ID 없음',provenance:{source_id:'seoul-openaptinfo'}})).toBe(false);
  });
});
