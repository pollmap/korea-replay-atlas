import {expect,it} from 'vitest';
import {readPropertyView,transactionCsv,moneyLabel} from '../shared/property-view';
import type {PropertyTransaction} from '../shared/property';
const period={from:'202109',to:'202609',latest_complete_month:'202608'};
it('restores valid filters and bounds malicious or unavailable share parameters',()=>{
  expect(readPropertyView('#regionCode=11110&month=202602&trade=rent&area=84.99&complex=molit-apt:11110:old-code',period)).toMatchObject({region:'11110',month:'202602',trade:'rent',area:'84.99',complex:'molit-apt:11110:old-code'});
  for(const month of ['202613','202010','hello','202699'])expect(readPropertyView('#month='+month,period).month).toBe('202608');
  expect(readPropertyView('#compareRegions=11110,11110,12345,23456,34567&area=NaN&complex=../../x',period)).toMatchObject({area:'',complex:'',compare:['11110','12345','23456']});
});
it('exports literal spreadsheet cells while retaining integer won',()=>{
  const text=transactionCsv([{complex_name:'=HYPERLINK("bad")',price_krw:123450000,contract_date:'2026-08-01'} as PropertyTransaction]);
  expect(text.startsWith('\uFEFF')).toBe(true);expect(text).toContain('"\'=HYPERLINK(""bad"")"');expect(text).toContain('"123450000"');
  expect(moneyLabel(123450000)).toBe('1억 2,345만');expect(moneyLabel(null)).toBe('자료 없음');
});
