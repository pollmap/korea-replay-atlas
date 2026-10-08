import type {FeatureCollection,Point} from 'geojson';
import {areaMatches,exclusivePyeong,M2_PER_PYEONG} from '../shared/property-area';
import {historyMonths} from '../shared/property-history';
import {propertySummaryCoverage} from './property-summary-coverage';
import {latestPriceBounds,moneyLabel,propertyListFilters,type PropertyViewState} from '../shared/property-view';

import {discoverPropertyComplexes} from '../shared/property-discovery';
import type {PropertyComplex} from '../shared/property';

export interface ComplexPriceSummary {
  complex_id:string;deal_month:string;trade_type:'sale'|'rent';rent_kind:'sale'|'jeonse'|'monthly';area_m2:string;transaction_count:number;
  latest_transaction_id:string;latest_contract_date:string;latest_price_krw:number|null;latest_deposit_krw:number|null;latest_monthly_rent_krw:number|null;
  min_price_krw?:number|null;max_price_krw?:number|null;median_price_krw?:number|null;median_deposit_krw?:number|null;median_monthly_rent_krw?:number|null;
  median_price_per_m2_krw?:number|null;median_price_per_pyeong_krw?:number|null;
}
export interface SummaryPartition {deal_month:string;trade_type:'sale'|'rent';status:string;}
export interface MapPriceData {rows:readonly ComplexPriceSummary[];partitions:readonly SummaryPartition[];state:'ready'|'loading'|'missing'|'error';complexes?:readonly PropertyComplex[];metadataState?:'ready'|'loading'|'error';}
export const propertyMapFilterKey=(release:string,view:PropertyViewState)=>JSON.stringify([release,view.propertyType??'apartment',view.region,view.trade,view.rentKind??'all',view.month,view.historyMonths,view.area,view.areaBasis??'exclusive',view.latestPriceMinEok??'',view.latestPriceMaxEok??'',view.legalDong??'',view.listQuery??'',view.buildYearMin??'',view.buildYearMax??'',!!view.hasTrades]);

/** Use an actual report in the selected window. Never use a different month or
 * turn a missing partition into a zero or a market asking price. */
export function propertyMapPrices(base:FeatureCollection<Point>,release:string,view:PropertyViewState,data:MapPriceData):FeatureCollection<Point> {
  const metadataFiltered=!!view.legalDong||!!view.listQuery||!!view.buildYearMin||!!view.buildYearMax;
  const metadata=discoverPropertyComplexes(data.complexes??[],[],false,view.trade,{...propertyListFilters(view),dong:view.legalDong??''});
  const metadataIds=new Set(metadata.items.map(item=>item.complex.id));
  const bounds=latestPriceBounds(view),priceFiltered=!!view.latestPriceMinEok||!!view.latestPriceMaxEok;
  const key=propertyMapFilterKey(release,view),selectedMonths=historyMonths(view.month,view.historyMonths),months=new Set(selectedMonths);
  const coverage=propertySummaryCoverage(data.partitions,selectedMonths,view.trade);
  const complete=coverage.canEstablishNoTrade,transactionFiltered=priceFiltered||!!view.area||!!view.hasTrades||view.trade==='rent'&&!!view.rentKind&&view.rentKind!=='all';
  const rows=new Map<string,ComplexPriceSummary>();
  for(const row of data.rows){
    if(!months.has(row.deal_month)||!coverage.complete.has(row.deal_month)||row.trade_type!==view.trade||!row.complex_id.startsWith(`molit-apt:${view.region}:`)||!areaMatches(row.area_m2,view.area)||view.trade==='rent'&&view.rentKind&&view.rentKind!=='all'&&row.rent_kind!==(view.rentKind==='jeonse'?'jeonse':'monthly'))continue;
    const previous=rows.get(row.complex_id);
    if(!previous||row.latest_contract_date>previous.latest_contract_date||row.latest_contract_date===previous.latest_contract_date&&row.latest_transaction_id<previous.latest_transaction_id)rows.set(row.complex_id,row);
  }
  return {...base,features:base.features.map(feature=>{
    const p={...feature.properties},id=p.property_release_id===release&&typeof p.property_complex_id==='string'?p.property_complex_id:'',row=rows.get(id);
    const amount=row?(view.trade==='sale'?row.latest_price_krw:row.latest_deposit_krw):null;
    const priceMatch=!bounds.errors.length&&(!priceFiltered||data.state!=='ready'||!row&&!complete||!!row&&amount!==null&&(bounds.priceMin===null||amount>=bounds.priceMin)&&(bounds.priceMax===null||amount<=bounds.priceMax));
    const metadataMatch=!metadata.errors.length&&(!metadataFiltered||data.metadataState!=='ready'||metadataIds.has(id));
    const tradeMatch=!transactionFiltered||data.state!=='ready'||!!row||!complete;
    let price='',unit='',label='';
    if(view.propertyType==='officetel')label='아파트';
    else if(!id)label='거래 미연결';
    else if(view.areaBasis==='supply')label='공급면적 미연결';
    else if(!id.startsWith(`molit-apt:${view.region}:`))label='조건 미연결';
    else if(metadataFiltered&&data.metadataState!=='ready')label=data.metadataState==='error'?'조건 조회 실패':'조건 확인 중';
    else if(!metadataMatch)label='단지 조건 밖';
    else if(row&&!priceMatch)label='최근 가격 조건 밖';
    else if(row){
      const amount=view.trade==='sale'?row.latest_price_krw:row.latest_deposit_krw;
      price=moneyLabel(amount)+(row.rent_kind==='monthly'?` / 월 ${moneyLabel(row.latest_monthly_rent_krw)}`:'');
      unit=amount===null?'금액 미제공':`${moneyLabel(Math.round(amount/Number(row.area_m2)*M2_PER_PYEONG))}/전용평`;
      label=`${price}\n전용 ${exclusivePyeong(row.area_m2)}평`;
    }else label=data.state==='loading'?'조건 확인 중':data.state==='error'?'조회 실패':data.state==='missing'?'조건 미연결':complete?'해당 거래 없음':'미수집 포함';
    return {...feature,properties:{...p,property_filter_key:key,filtered_price_match:priceMatch&&metadataMatch&&tradeMatch,filtered_label:label,filtered_price_label:price||label,filtered_unit_label:unit||label,
      filtered_contract_date:row?.latest_contract_date??'',filtered_area_m2:row?.area_m2??'',filtered_complete:complete&&data.state==='ready'}};
  })};
}
