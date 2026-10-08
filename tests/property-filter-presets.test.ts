import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import {EMPTY_PROPERTY_DISCOVERY_FILTERS} from '../shared/property-discovery';
import {applyPropertyFilterPreset,propertyFilterPresetActive,propertyFilterPresets} from '../shared/property-filter-presets';
import {propertyFilterChips} from '../shared/property-filter-chips';
import {areaFromBounds} from '../shared/property-area';
import {propertyListFilters,readPropertyView} from '../shared/property-view';
import PropertySavedFilters from '../src/PropertySavedFilters';

const current={...EMPTY_PROPERTY_DISCOVERY_FILTERS,query:'가락',dong:'가락동',priceMinEok:'3',priceMaxEok:'8',rentKind:'jeonse' as const,hasTrades:true};
it('uses existing exclusive-area and sort filters without inventing a budget or changing region constraints',()=>{
 const preset=propertyFilterPresets('202609')[0],next=applyPropertyFilterPreset(current,preset);
 expect(next).toMatchObject({...current,areaMinM2:'84',areaMaxM2:'84.99999',sort:'price-low'});
 expect(propertyFilterPresetActive(next,preset)).toBe(true);
 expect(propertyFilterChips(next,'rent').map(chip=>chip.label)).toContain('보증금 3–8억');
 const removed={...next,...propertyFilterChips(next,'rent').find(chip=>chip.id==='area')!.clear};
 expect(removed).toMatchObject({priceMinEok:'3',priceMaxEok:'8',dong:'가락동',sort:'price-low',areaMinM2:'',areaMaxM2:''});
 expect(propertyFilterPresetActive(removed,preset)).toBe(false);
 expect(current.areaMinM2).toBe('');
});
it('pins construction years to the selected contract month rather than today or an assumed completion date',()=>{
 const preset=propertyFilterPresets('202609')[1],next=applyPropertyFilterPreset(current,preset);
 expect(preset.conditions).toBe('전용 84㎡대 · 2016–2026년 건축');
 expect(next).toMatchObject({buildYearMin:'2016',buildYearMax:'2026',areaMinM2:'84',priceMaxEok:'8'});
 expect(propertyFilterPresets('201408')[1].patch).toMatchObject({buildYearMin:'2004',buildYearMax:'2014'});
 for(const invalid of [undefined,'','202613','202609extra','000101'])expect(propertyFilterPresets(invalid)).toHaveLength(1);
});
it('restores the concrete preset conditions through the existing shared state and leaves the selected period intact',()=>{
 const preset=propertyFilterPresets('202609')[1],next=applyPropertyFilterPreset(current,preset);
 const params=new URLSearchParams({regionCode:'11710',legalDong:next.dong,trade:'rent',rentKind:'jeonse',month:'202609',historyMonths:'36',area:areaFromBounds(next.areaMinM2,next.areaMaxM2),latestPriceMinEok:next.priceMinEok,latestPriceMaxEok:next.priceMaxEok,buildYearMin:next.buildYearMin,buildYearMax:next.buildYearMax,listQuery:next.query,listSort:next.sort,hasTrades:'1'});
 const view=readPropertyView('#'+params,{from:'200610',to:'202610',latest_complete_month:'202609'});
 expect(view).toMatchObject({region:'11710',legalDong:'가락동',trade:'rent',rentKind:'jeonse',month:'202609',historyMonths:36,area:'84-band',latestPriceMaxEok:'8',buildYearMin:'2016',buildYearMax:'2026'});
 expect(propertyListFilters(view)).toMatchObject({query:'가락',buildYearMin:'2016',buildYearMax:'2026',hasTrades:true});
});
it('reuses the saved-condition disclosure and does not advertise unsupported parking, households or scoring themes',()=>{
 const html=renderToStaticMarkup(createElement(PropertySavedFilters,{region:'11710',trade:'sale',month:'202609',filters:current,onApply:()=>{}}));
 expect(html).toContain('<details class="property-saved-filters">');expect(html).toContain('내 검색조건');
 expect(html).toContain('빠른 조건 묶음');expect(html).toContain('국평 · 낮은 가격순');expect(html).toContain('국평 · 최근 10년 건축');expect(html).toContain('2016–2026년 건축');
 for(const text of ['주차 테마','대단지 테마','추천 점수','상승 예측'])expect(html).not.toContain(text);
});
