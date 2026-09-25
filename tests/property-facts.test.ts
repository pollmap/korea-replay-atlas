import {expect,it} from 'vitest';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import data from '../src/data/seoul-apartment-facts.json';
import navigation from '../src/data/seoul-property-navigation.json';
import {apartmentFactsIndex,parkingPerHousehold} from '../shared/property-facts';
import ApartmentFacts,{createApartmentFactsLookup} from '../src/ApartmentFacts';

it('ties every published fact to exactly the existing audited identity without upgrading coordinates',()=>{
  const index=apartmentFactsIndex(data),ids=new Map(navigation.points.map(row=>[row[0],row[1]]));
  expect(index.rows).toHaveLength(842);
  for(const row of index.rows)expect(ids.get(row.complex_id)).toBe(row.kapt_code);
  expect(data.coordinate_verification).toBe('not_performed');
  expect(JSON.stringify(data)).not.toMatch(/TELNO|FXNO|XCRD|YCRD|HMPG/);
});
it('renders the actual official Helio City counts and retains unknown vs zero',()=>{
  const row=apartmentFactsIndex(data).rows.find(row=>row.complex_id==='molit-apt:11710:11710-8865')!;
  expect(row).toMatchObject({households:9510,parking:12096,buildings:84,heating:'지역난방',approved_on:'2018-12-28'});
  expect(parkingPerHousehold(row)).toBeCloseTo(1.27192429);
  expect(parkingPerHousehold({...row,households:0})).toBeNull();
  expect(parkingPerHousehold({...row,parking:null})).toBeNull();
  expect(parkingPerHousehold({...row,parking:0})).toBe(0);
  const html=renderToStaticMarkup(createElement(ApartmentFacts,{complexId:row.complex_id,release:data.property_release_id}));
  expect(html).toContain('9,510세대');expect(html).toContain('12,096대');expect(html).toContain('사용승인일');
  expect(renderToStaticMarkup(createElement(ApartmentFacts,{complexId:row.complex_id,release:'property-0000000000000000'}))).toBe('');
});
it('rejects duplicate identity, numeric coercion, and invalid source dates',()=>{
  for(const rows of [[data.rows[0],data.rows[0]],[{...data.rows[0],households:'12'}],[{...data.rows[0],approved_on:'2026-02-31'}]])expect(()=>apartmentFactsIndex({...data,rows})).toThrow();
});
it('indexes facts independently by audited property release and rejects duplicate release registrations',()=>{
  const nextRelease='property-1111111111111111',next={...data,property_release_id:nextRelease,rows:[{...data.rows[0],households:1234}]};
  const lookup=createApartmentFactsLookup([data,next]);
  expect(lookup(data.property_release_id)?.rows.size).toBe(842);
  expect(lookup(nextRelease)?.rows.size).toBe(1);
  expect(lookup(nextRelease)?.rows.get(data.rows[0].complex_id)?.households).toBe(1234);
  expect(lookup('property-2222222222222222')).toBeUndefined();
  expect(lookup(nextRelease)).toBe(lookup(nextRelease));
  expect(()=>createApartmentFactsLookup([data,data])).toThrow('버전 목록');
});
it('does not let invalid facts in one release poison another release cache',()=>{
  const nextRelease='property-1111111111111111',bad={...data,property_release_id:nextRelease,rows:[{...data.rows[0],households:'1234'}]};
  const lookup=createApartmentFactsLookup([data,bad]);
  expect(()=>lookup(nextRelease)).toThrow();
  expect(lookup(data.property_release_id)?.rows.size).toBe(842);
  expect(()=>lookup(nextRelease)).toThrow();
});
