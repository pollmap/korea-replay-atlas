import {expect,it} from 'vitest';
import type {FeatureCollection,Point} from 'geojson';
import type {PropertyComplex} from '../shared/property';
import {EMPTY_PROPERTY_DISCOVERY_FILTERS} from '../shared/property-discovery';
import {applyPropertyFilterPreset,propertyFilterPresets} from '../shared/property-filter-presets';
import {areaFromBounds} from '../shared/property-area';
import {readPropertyView} from '../shared/property-view';
import {discoverPropertySummaryComplexes} from '../src/property-summary-discovery';
import {propertyMapPrices,type ComplexPriceSummary,type SummaryPartition} from '../src/property-map-prices';

const release='property-0123456789abcdef';
const complex=(id:string,year:number|null=2018):PropertyComplex=>({id:`molit-apt:11710:${id}`,source_complex_id:id,lawd_code:'11710',name:id,legal_dong_code:'1171010700',legal_dong_name:'가락동',lot_number:'1',build_year:year,observed_name_variants:[],position:null,identity_status:'source_apt_seq',source_ids:['molit-apt-sale-detail'],source_input_sha256:['a'.repeat(64)],first_contract_month:'202607',last_contract_month:'202609',address_conflict:false});
const row=(id:string,month='202609',price=4e8,area='84.99'):ComplexPriceSummary=>({complex_id:complex(id).id,deal_month:month,trade_type:'sale',rent_kind:'sale',area_m2:area,transaction_count:2,latest_transaction_id:id+month,latest_contract_date:month.slice(0,4)+'-'+month.slice(4)+'-15',latest_price_krw:price,latest_deposit_krw:null,latest_monthly_rent_krw:null});
const complexes=[complex('yes'),complex('old',2000),complex('missing-year',null),complex('wrong-area'),complex('expensive'),complex('missing-price')];
const base:FeatureCollection<Point>={type:'FeatureCollection',features:complexes.map(item=>({type:'Feature',geometry:{type:'Point',coordinates:[127,37]},properties:{property_complex_id:item.id,property_release_id:release}}))};
const defaults=readPropertyView('#regionCode=11710&trade=sale&month=202609&historyMonths=3',{from:'200610',to:'202610',latest_complete_month:'202609'});
const months=['202607','202608','202609'];
const partition=(month:string,status='complete'):SummaryPartition=>({deal_month:month,trade_type:'sale',status});
const filters=applyPropertyFilterPreset({...EMPTY_PROPERTY_DISCOVERY_FILTERS,priceMaxEok:'5'},propertyFilterPresets('202609')[1]);
const view={...defaults,area:areaFromBounds(filters.areaMinM2,filters.areaMaxM2),latestPriceMaxEok:filters.priceMaxEok,buildYearMin:filters.buildYearMin,buildYearMax:filters.buildYearMax};
const rows=[row('yes'),row('old'),row('missing-year'),row('wrong-area','202609',4e8,'59'),row('expensive','202608',3e8),row('expensive','202609',9e8)];
function both(parts:SummaryPartition[],reports=rows){
 const list=discoverPropertySummaryComplexes(complexes,reports,parts,{months,trade:'sale',area:view.area},filters);
 const map=propertyMapPrices(base,release,view,{state:'ready',metadataState:'ready',complexes,rows:reports,partitions:parts});
 const mapIds=map.features.filter(feature=>feature.properties?.filtered_price_match).map(feature=>feature.properties!.property_complex_id).sort();
 expect(mapIds).toEqual(list.items.map(item=>item.complex.id).sort());
 return {list,map};
}
it('applies the same budget, national area and known construction years to the list and map',()=>{
 const result=both(months.map(month=>partition(month)));
 expect(result.list.items.map(item=>item.complex.id)).toEqual([complex('yes').id]);
 expect(result.list.items[0].confirmedCount).toBe(2);
 expect(complexes.every(item=>item.position===null)).toBe(true); // filtering never manufactures positions
});
it('does not borrow a failed month price and preserves genuinely unknown candidates in both views',()=>{
 const parts=months.map(month=>partition(month,month==='202609'?'failed':'complete'));
 const result=both(parts);
 expect(result.map.features.find(feature=>feature.properties!.property_complex_id===complex('expensive').id)?.properties?.filtered_price_label).toBe('3억');
 expect(result.map.features.find(feature=>feature.properties!.property_complex_id===complex('yes').id)?.properties?.filtered_label).toBe('미수집 포함');
 expect(result.list.items.every(item=>item.count===null)).toBe(true);
});
it('keeps pre-source-only periods unknown instead of turning them into zero reports',()=>{
 const result=both(months.map(month=>partition(month,'source_unavailable')),[]);
 expect(result.list.items.every(item=>item.count===null)).toBe(true);
 expect(result.map.features.every(feature=>!feature.properties!.filtered_complete)).toBe(true);
});
it('hides out-of-area candidates only after the same months have been checked',()=>{
 const areaOnly={...defaults,area:'84-band'};
 const list=discoverPropertySummaryComplexes(complexes,rows,months.map(month=>partition(month)),{months,trade:'sale',area:'84-band'},EMPTY_PROPERTY_DISCOVERY_FILTERS);
 const map=propertyMapPrices(base,release,areaOnly,{state:'ready',rows,partitions:months.map(month=>partition(month))});
 expect(map.features.filter(item=>item.properties!.filtered_price_match).map(item=>item.properties!.property_complex_id).sort()).toEqual(list.items.map(item=>item.complex.id).sort());
});
