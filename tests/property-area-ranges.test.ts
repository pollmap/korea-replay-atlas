import {expect,it} from 'vitest';
import {areaMatches,areaFromBounds,areaLabel,validAreaFilter} from '../shared/property-area';
import {readPropertyView,propertyAreaOptions} from '../shared/property-view';
const period={from:'200610',to:'202610',latest_complete_month:'202609'};
it('retains open ended bounds and exact areas through a shared link',()=>{
  for(const [min,max,expected] of [['60','85','range:60:85'],['','60','range::60'],['102','','range:102:'],['84','84.99999','84-band'],['84.99','84.99','84.99']]){
    const area=areaFromBounds(min,max),parsed=readPropertyView('#area='+encodeURIComponent(area),period);
    expect(area).toBe(expected);expect(parsed.area).toBe(expected);
  }
  expect(areaLabel('range:60:85')).toBe('전용 60–85㎡');
  expect(propertyAreaOptions(['59','84.99'],'range:60:85',true)).toContainEqual({value:'range:60:85',label:'전용 60–85㎡'});
});
it('keeps the 84 band distinct from an inclusive 85 endpoint',()=>{
  expect(areaMatches('85','84-band')).toBe(false);expect(areaMatches('85','range:60:85')).toBe(true);
  expect(areaMatches('59.99','range::60')).toBe(true);expect(areaMatches('60.01','range::60')).toBe(false);
  expect(areaMatches(null,'range::60')).toBe(false);
});
it('rejects malformed, inverted and oversized shared bounds instead of broadening the selection',()=>{
  for(const filter of ['range:85:60','range::','range:-1:60','range:60:Infinity','range:0:10001','range:1:2:3']){
    expect(validAreaFilter(filter)).toBe(false);expect(areaMatches('84',filter)).toBe(false);expect(readPropertyView('#area='+encodeURIComponent(filter),period).area).toBe('');
  }
});
