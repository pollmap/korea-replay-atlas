import {expect,it} from 'vitest';
import {commonSummaryMonths} from '../src/PropertySummaryComparison';
const value=(statuses:string[])=>({rows:[],partitions:statuses.map((status,i)=>({deal_month:['201012','201101','201102'][i],trade_type:'rent' as const,status}))});
it('compares only common verified months and preserves pre-source and failed months',()=>{
  const months=['201012','201101','201102'],data={a:value(['complete','complete','complete']),b:value(['complete','empty','failed'])};
  expect(commonSummaryMonths(months,data,['a','b'],'rent','201101')).toEqual(['201101']);
  expect(commonSummaryMonths(months,data,['a','missing'],'rent','201101')).toEqual([]);
  expect(commonSummaryMonths(months,data,['a','b'],'sale',null)).toEqual([]);
  expect(commonSummaryMonths(months,data,[],'rent',null)).toEqual([]);
});
