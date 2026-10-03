import {describe,it,expect} from 'vitest';
import {parseMapPricePreset} from '../src/property-map-presets';
import {readPropertyView} from '../shared/property-view';
import example from '../src/data/map-price-presets-b87eea7c1c03dc21/11710.json';
const release='property-b87eea7c1c03dc21';
const view=readPropertyView('#regionCode=11710&trade=sale&month=202609&historyMonths=36&area=84-band',{from:'200609',to:'202610',latest_complete_month:'202609'});
describe('small map price presets',()=>{
  it('keeps the selected period, actual area and one real report per linked complex',()=>{
    const value=parseMapPricePreset(example,release,view)!;
    expect(value.rows.length).toBeGreaterThan(20);
    expect(new Set(value.rows.map(row=>row.complex_id)).size).toBe(value.rows.length);
    expect(value.rows.every(row=>Number(row.area_m2)>=84&&Number(row.area_m2)<85&&row.deal_month>='202310'&&row.deal_month<='202609'&&row.trade_type==='sale')).toBe(true);
    expect(value.partitions.filter(row=>row.trade_type==='sale')).toHaveLength(36);
    expect(JSON.stringify(example).length).toBeLessThan(140000);
  });
  it('preserves old-period and specific-area fallback rather than silently switching',()=>{
    expect(parseMapPricePreset(example,release,{...view,month:'202608'})).toBeNull();
    expect(parseMapPricePreset(example,release,{...view,area:'82.1'})).toBeNull();
  });
  it('rejects a foreign release, region, row index or mismatched filter',()=>{
    expect(()=>parseMapPricePreset(example,'property-ffffffffffffffff',view)).toThrow();
    expect(()=>parseMapPricePreset(example,release,{...view,region:'11680'})).toThrow();
    const bad=structuredClone(example);bad.views['202609|36|84-band|sale']=[999999];
    expect(()=>parseMapPricePreset(bad,release,view)).toThrow();
    const other=structuredClone(example);other.views['202609|36|84-band|sale']=other.views['202609|36||sale'];
    expect(()=>parseMapPricePreset(other,release,view)).toThrow();
  });
  it('validates all generated sale, jeonse, monthly and full-rent selections',()=>{
    for(const [key] of Object.entries(example.views)){
      const [month,length,area,kind]=key.split('|');
      const selected=readPropertyView(`#regionCode=11710&month=${month}&historyMonths=${length}&area=${area}&trade=${kind==='sale'?'sale':'rent'}&rentKind=${kind==='rent'?'all':kind}`,{from:'200609',to:'202610',latest_complete_month:'202609'});
      expect(parseMapPricePreset(example,release,selected)).not.toBeNull();
    }
  });
});