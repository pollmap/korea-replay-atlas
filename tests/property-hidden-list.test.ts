import {expect,it,vi} from 'vitest';
const fixture=vi.hoisted(()=>({calls:[] as string[]}));
vi.mock('../src/atlas-client',()=>({fetchPinnedJson:vi.fn(async(ref:{url?:string;path?:string})=>{
  const path=ref.url??ref.path??'';fixture.calls.push(path);
  const prefix='/data/property-summary/summary-1111111111111111/';
  const reference=(url:string)=>({url,sha256:'a'.repeat(64),bytes:100,from_month:'202609',to_month:'202609'});
  const rows=['selected','other'].map((id,n)=>({complex_id:`molit-apt:11710:${id}`,deal_month:'202609',trade_type:'sale',rent_kind:'sale',area_m2:'84.99',transaction_count:3+n,latest_transaction_id:`molit-sale:${'a'.repeat(64)}:${n+1}`,latest_contract_date:'2026-09-15',latest_price_krw:(n+5)*100_000_000,latest_deposit_krw:null,latest_monthly_rent_krw:null}));
  if(path.endsWith('/manifest.json'))return {schema_version:1,kind:'property-complex-summary-release',property_release_id:'property-8879dff1b31ac5f0',regions:[{lawd_code:'11710',...reference(prefix+'regions/11710.json')}]};
  if(path.includes('/regions/'))return {kind:'property-complex-summary-region',property_release_id:'property-8879dff1b31ac5f0',lawd_code:'11710',partitions:[{deal_month:'202609',trade_type:'sale',status:'complete'}],summaries:[reference(prefix+'month-packs/11710/0000.json')],complex_chunks:{'molit-apt:11710:selected':[reference(prefix+'complexes/11710/0000.json')]}};
  return {schema_version:1,kind:'property-complex-summary-pack',property_release_id:'property-8879dff1b31ac5f0',lawd_code:'11710',from_month:'202609',to_month:'202609',rows:path.includes('/complexes/')?rows.slice(0,1):rows};
})}));
import {loadComplexPriceSummaries} from '../src/property-summary-client';
import {propertyListSummaryComplex} from '../src/property-list-query';
import {discoverPropertySummaryComplexes} from '../src/property-summary-discovery';
import {EMPTY_PROPERTY_DISCOVERY_FILTERS} from '../shared/property-discovery';
import type {PropertyComplex} from '../shared/property';

it('fetches only the selected apartment while the list is hidden and restores district history when opened',async()=>{
  const selectedId='molit-apt:11710:selected';
  const query=(visible:boolean)=>loadComplexPriceSummaries('property-8879dff1b31ac5f0','11710',['202609'],new AbortController().signal,propertyListSummaryComplex(visible,selectedId),{trade:'sale',area:'84-band'});
  const hidden=(await query(false))!;
  expect(hidden.rows.map(row=>row.complex_id)).toEqual([selectedId]);
  expect(fixture.calls.some(path=>path.includes('/month-packs/'))).toBe(false);
  fixture.calls=[];
  const opened=(await query(true))!;
  expect(opened.rows).toHaveLength(2);expect(fixture.calls.some(path=>path.includes('/month-packs/'))).toBe(true);
  const complex={id:selectedId,name:'선택 단지',legal_dong_name:'가락동',observed_name_variants:[],build_year:2018} as unknown as PropertyComplex;
  const qualify=(data:typeof hidden,max:string)=>discoverPropertySummaryComplexes([complex],data.rows,data.partitions,{months:['202609'],trade:'sale',area:'84-band'},{...EMPTY_PROPERTY_DISCOVERY_FILTERS,priceMaxEok:max});
  expect(qualify(hidden,'6').items).toEqual(qualify(opened,'6').items);
  expect(qualify(hidden,'4').items).toEqual([]);
  expect(qualify(hidden,'6').items[0]).toMatchObject({count:3,latest:{latest_price_krw:500_000_000}});
});
