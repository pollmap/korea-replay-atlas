import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import type {PropertyTransaction} from '../shared/property';
import {defaultDetailArea,detailSectionAtScroll,equalLoanPayment,readComplexNote} from '../src/property-desktop';
import {DetailUnavailable,PropertyLoanCalculator,PropertyRegionAnalysis} from '../src/PropertyDetailTools';

const row=(area:string,overrides:Partial<PropertyTransaction>={})=>({area_m2:area,trade_type:'sale',cancellation:'not_reported',statistics_eligible:true,quality:'valid',...overrides}) as PropertyTransaction;
it('keeps a short final comparison selected when the scroll limit prevents top alignment',()=>{
  const sections=[{id:'costs',top:160},{id:'compare',top:490}] as const;
  expect(detailSectionAtScroll(sections,{top:197,height:883,scrollTop:2400,scrollHeight:3283})).toBe('compare');
  expect(detailSectionAtScroll(sections,{top:197,height:883,scrollTop:2390,scrollHeight:3283})).toBe('costs');
  expect(detailSectionAtScroll([{id:'trades',top:200},{id:'compare',top:490}],{top:197,height:883,scrollTop:0,scrollHeight:883})).toBe('trades');
  expect(detailSectionAtScroll([],{top:197,height:883,scrollTop:2400,scrollHeight:3283})).toBeUndefined();
});
it('defaults to reported 84㎡ band and does not derive a supply-area label or price',()=>{
  expect(defaultDetailArea([row('59'),row('59'),row('84.99')])).toBe('84-band');
  expect(defaultDetailArea([row('85'),row('83.99'),row('83.99')])).toBe('83.99');
  expect(defaultDetailArea([row('84.99',{cancellation:'cancelled'}),row('59')])).toBe('59');
  expect(defaultDetailArea([row('84.99',{cancellation:'unknown'}),row('59')])).toBe('59');
  expect(defaultDetailArea([row('84.99',{statistics_eligible:false}),row('59')])).toBe('59');
});
it('uses all distinct reports for the modal area and resolves ties deterministically',()=>{
  const rows=[row('102'),row('59'),row('59'),row('102')];
  expect(defaultDetailArea(rows)).toBe('59');expect(defaultDetailArea([...rows].reverse())).toBe('59');
  expect(defaultDetailArea([])).toBe('');expect(defaultDetailArea([row('0'),row('NaN')])).toBe('');
});
it('calculates zero-interest and fixed-rate amortization without guessing lending eligibility',()=>{
  expect(equalLoanPayment(120000000,0,120)).toEqual({monthly:1000000,interest:0,total:120000000});
  expect(equalLoanPayment(100000000,4,360)?.monthly).toBe(477415);
  expect(equalLoanPayment(100000000,0.00000001,360)?.monthly).toBe(277778);
  for(const args of [[0,4,360],[-1,4,360],[1e8,-1,360],[1e8,101,360],[1e8,4,601],[1e8,4,12.5],[NaN,4,12]] as const)expect(equalLoanPayment(args[0],args[1],args[2])).toBeNull();
});
it('validates stored notes without silently replacing malformed existing content',()=>{
  expect(readComplexNote(null)).toBe('');expect(readComplexNote('{"version":1,"text":"국평 방문 기록"}')).toBe('국평 방문 기록');
  for(const raw of ['{broken','[]','{"version":2,"text":"기존 메모"}',JSON.stringify({version:1,text:'x'.repeat(4001)})])expect(()=>readComplexNote(raw)).toThrow();
});
it('shows empty loan inputs and missing source states without fabricating numbers',()=>{
  const loan=renderToStaticMarkup(createElement(PropertyLoanCalculator));
  expect(loan).toContain('금액과 금리를 입력하면');expect(loan).not.toContain('0원');
  const pending=renderToStaticMarkup(createElement(DetailUnavailable,{title:'관리비'}));
  expect(pending).toContain('자료 연결 전');expect(pending).not.toContain('0원');
  const region=renderToStaticMarkup(createElement(PropertyRegionAnalysis,{region:'송파구',month:'202608',metrics:[]}));
  expect(region).toContain('미수집');expect(region).not.toContain('0건');
});
