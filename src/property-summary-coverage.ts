/** Only completed partitions may supply prices. Missing and pre-source months
 * are distinct; zero can be established only if at least one month was provided. */
export function propertySummaryCoverage(partitions:readonly {deal_month:string;trade_type:'sale'|'rent';status:string}[],months:readonly string[],trade:'sale'|'rent'){
  const selected=new Set(months),complete=new Set<string>(),unavailable=new Set<string>();
  for(const partition of partitions){
    if(partition.trade_type!==trade||!selected.has(partition.deal_month))continue;
    if(partition.status==='complete'||partition.status==='empty')complete.add(partition.deal_month);
    else if(partition.status==='source_unavailable')unavailable.add(partition.deal_month);
  }
  const missingMonths=months.filter(month=>!complete.has(month)&&!unavailable.has(month)).length;
  return {complete,unavailable,missingMonths,canEstablishNoTrade:missingMonths===0&&complete.size>0};
}
