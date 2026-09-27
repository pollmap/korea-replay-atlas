import {expect,it} from 'vitest';
import {addressMapLinks,complexMapLinks,kakaoPointLinks} from '../shared/external-maps';
import type {PropertyComplex} from '../shared/property';

it('uses latitude first in official roadview and destination links, without a synthetic starting point',()=>{
  const links=kakaoPointLinks({lat:37.501,lon:127.108});
  expect(links[0].href).toBe('https://map.kakao.com/link/roadview/37.501000,127.108000');
  expect(links[2].href).toContain('/link/to/');expect(links[2].href).not.toContain('/from/');
  for(const point of [{lat:127.108,lon:37.501},{lat:NaN,lon:127},{lat:37,lon:Infinity},{lat:0,lon:0}])expect(kakaoPointLinks(point)).toEqual([]);
});

it('encodes the entire address as one search segment without injecting parameters or fragments',()=>{
  const query='서울 송파구 A/B & C #학교?x=1';
  for(const link of addressMapLinks(query)){
    const url=new URL(link.href);
    expect(url.hash).toBe('');expect(url.search).toBe('');
    expect(decodeURIComponent(url.pathname.split('/').at(-1)!)).toBe(query);
  }
  for(const bad of ['', ' ', '\n서울', 'x'.repeat(401), '서울\u007f'])expect(addressMapLinks(bad)).toEqual([]);
});

const complex={name:'단지',legal_dong_name:'가락동',lot_number:'123',position:null,address_conflict:false} as PropertyComplex;
it('does not turn unknown/candidate/conflicting apartment positions into roadview coordinates',()=>{
  expect(complexMapLinks(complex,'서울 송파구').map(link=>link.label)).toEqual(['네이버 지도','카카오맵']);
  const verified={...complex,position:{longitude:127.108,latitude:37.501,crs:'EPSG:4326',evidence:{verified_at:'2026-09-26'}}} as PropertyComplex;
  expect(complexMapLinks(verified,'서울 송파구')).toHaveLength(4);
  expect(complexMapLinks({...verified,address_conflict:true},'서울 송파구')).toHaveLength(2);
  expect(complexMapLinks({...verified,position:{...verified.position!,crs:'EPSG:5179'} as never},'서울 송파구')).toHaveLength(2);
});
