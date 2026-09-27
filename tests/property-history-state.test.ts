import {expect,it} from 'vitest';
import {commonHistoryMonths,historyMonthStatus,historyRefreshSummary,publishedHistoryCoverage,shiftHistoryEnd} from '../src/property-history-state';
import type {HistoryResult} from '../src/property-history-loader';
import type {PropertyRegionDetail} from '../shared/property';

const result=(month:string,status:HistoryResult['status'],reason?:string):HistoryResult=>({month,status,rows:[],reason});
it('compares only common complete months including confirmed empty months, not failed or budget-skipped ones',()=>{
  const months=['202605','202606','202607','202608'];
  const a=months.map(month=>result(month,'ready'));
  const b=[result('202605','missing','download_budget'),result('202606','error'),result('202607','ready'),result('202608','ready')];
  expect(commonHistoryMonths(months,{a,b},['a','b'])).toEqual(['202607','202608']);
  expect(commonHistoryMonths(months,{a,b},['a','b','unloaded'])).toEqual([]);
  expect(commonHistoryMonths(months,{},[])).toEqual([]);
});
it('separates publication coverage from downloaded coverage and keeps holes visible',()=>{
  const detail={partitions:[
    {deal_month:'202605',trade_type:'sale',status:'complete'},
    {deal_month:'202606',trade_type:'sale',status:'pending'},
    {deal_month:'202607',trade_type:'sale',status:'empty'},
    {deal_month:'202608',trade_type:'sale',status:'partial'},
    {deal_month:'202604',trade_type:'rent',status:'complete'},
  ]} as PropertyRegionDetail;
  expect(publishedHistoryCoverage(detail,'sale',['202606','202607','202608'])).toEqual({first:'202605',last:'202607',count:1});
  expect(historyMonthStatus(result('202606','missing','outside_release'))).toBe('이 버전에 미게시');
  expect(historyMonthStatus(result('202606','missing','download_budget'))).toContain('다운로드 한도');
  expect(historyMonthStatus(result('202606','error'))).toContain('재시도');
  expect(historyMonthStatus(result('202607','ready'))).toBe('자료 확인');
  expect(historyMonthStatus(result('201012','missing','before_source'))).toBe('원천 자료 제공 전');
  expect(historyMonthStatus(result('201012','missing','source_unavailable'))).toBe('원천 자료 제공 전');
  expect(historyMonthStatus(result('202607','missing','retention_budget'))).toContain('기록 수 한도');
});
it('moves contiguous period windows across year boundaries without timezone drift',()=>{
  expect(shiftHistoryEnd('202601',3,-1)).toBe('202510');
  expect(shiftHistoryEnd('202510',3,1)).toBe('202601');
  expect(shiftHistoryEnd('202608',120,-1)).toBe('201608');
  expect(shiftHistoryEnd('202608',240,-1)).toBe('200608');
  expect(shiftHistoryEnd('200608',240,1)).toBe('202608');
  expect(()=>shiftHistoryEnd('202613',3,-1)).toThrow();
});

it('labels refreshed snapshots separately from download status and only summarizes the selected trade and period',()=>{
  const detail={partitions:[
    {deal_month:'202608',trade_type:'sale',status:'complete',retrieved_at:'2026-09-19T00:00:00Z',refresh:{status:'failed',error_code:'upstream_timeout',attempted_at:'2026-09-22T00:00:00Z'}},
    {deal_month:'202607',trade_type:'sale',status:'empty',retrieved_at:'2026-09-20T00:00:00Z',refresh:{status:'pending',error_code:null,attempted_at:null}},
    {deal_month:'202606',trade_type:'sale',status:'pending',retrieved_at:null},
    {deal_month:'202608',trade_type:'rent',status:'complete',retrieved_at:'2026-09-21T00:00:00Z',refresh:{status:'partial',error_code:null,attempted_at:null}},
  ]} as PropertyRegionDetail;
  expect(historyRefreshSummary(detail,'sale',['202607','202608'])).toEqual({count:2,firstSuccess:'2026-09-19',lastSuccess:'2026-09-20'});
  expect(historyRefreshSummary(detail,'sale',['202606'])).toEqual({count:0,firstSuccess:null,lastSuccess:null});
  expect(historyMonthStatus(result('202608','ready'),detail.partitions[0])).toContain('자료 확인 · 갱신 실패 · 이전 확인본 유지');
  expect(historyMonthStatus(result('202608','error'),detail.partitions[0])).toContain('조회 실패 · 재시도 가능 · 갱신 실패');
  expect(historyMonthStatus(result('202607','ready'),detail.partitions[1])).toContain('갱신 대기');
  expect(historyMonthStatus(result('202608','ready'),detail.partitions[3])).toContain('갱신 일부 확인');
  expect(historyMonthStatus(result('202608','ready'),detail.partitions[0])).toContain('2026-09-19');
  expect(historyMonthStatus(result('202608','ready'),detail.partitions[0])).not.toContain('2026-09-22');
});
