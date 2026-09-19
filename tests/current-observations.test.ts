import {createHash} from 'node:crypto';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,expect,it,vi} from 'vitest';
import CurrentObservations,{busCityAvailability,filterBusCities,nationalBusRouteOptions,readBusCatalogSelection} from '../src/CurrentObservations';
import {createTransitCatalogReader,type TransitCatalogRef,type TransitCity,type TransitRoute} from '../shared/transit-catalog';

function fixture(){
  const prefix='/data/live-transit/routes/1234567890abcdef/',retrieved_at='2026-09-19T00:00:00Z';
  const bodies=new Map<string,string>();
  const reference=(name:string,value:unknown):TransitCatalogRef=>{
    const body=JSON.stringify(value),url=prefix+name;bodies.set(url,body);
    return {url,sha256:createHash('sha256').update(body).digest('hex'),byte_length:Buffer.byteLength(body)};
  };
  const route:TransitRoute={id:'tago-21-bsb1001',city_code:'21',route_id:'BSB1001',label:'부산 1001 · 종점 방면',route_no:'1001',route_type:null,start_station_name:'기점',end_station_name:'종점'};
  const city=(city_code:string,city_name:string,supported:boolean,routes:TransitRoute[]):TransitCity=>({
    city_code,city_name,status:'complete',location_service_supported:supported,route_count:routes.length,retrieved_at,
    ...reference(`${city_code}.json`,{schema_version:1,city_code,city_name,retrieved_at,routes}),
  });
  const busan=city('21','부산광역시',true,[route]),seoul=city('11','서울특별시',false,[]),empty=city('39000','제주특별자치도',true,[]);
  const catalog={schema_version:1,cities:[seoul,busan,empty]},ref=reference('manifest.json',catalog);
  const fetcher=vi.fn<typeof fetch>(async input=>{
    const body=bodies.get(String(input));if(body===undefined)throw new Error('Unexpected fixture request');
    return new Response(body,{headers:{'Content-Type':'application/json'}});
  });
  return {route,busan,seoul,empty,ref,fetcher,catalog};
}

describe('national current-observation city selection',()=>{
  it('starts with an explicit city choice and no regional recommendation fallback',()=>{
    const html=renderToStaticMarkup(createElement(CurrentObservations,{onTransit:vi.fn(),onLocate:vi.fn(),onClose:vi.fn()}));
    expect(html).toContain('id="live-bus-city-search"');
    expect(html).toContain('도시를 선택하세요');
    expect(html).toContain('도시를 먼저 선택하세요');
    expect(html).not.toContain('추천 노선');
    expect(html).not.toMatch(/value="(?:sejong-b2|daejeon-202|cheongju-747)"/);
    expect(html).toContain('class="current-observations panel"');
  });

  it('loads only the chosen supported city and never replaces an unsupported or missing choice',async()=>{
    const f=fixture(),reader=createTransitCatalogReader(f.fetcher);
    const initial=await readBusCatalogSelection(reader,f.ref,'');
    expect(initial.routes).toEqual([]);expect(f.fetcher).toHaveBeenCalledTimes(1);
    for(const code of ['11','39000','99999']){
      expect((await readBusCatalogSelection(reader,f.ref,code)).routes).toEqual([]);
      expect(f.fetcher).toHaveBeenCalledTimes(1);
    }
    expect(busCityAvailability(f.seoul)).toBe('unsupported');
    const selected=await readBusCatalogSelection(reader,f.ref,'21');
    expect(selected.routes).toEqual([f.route]);
    expect(f.fetcher.mock.calls.map(call=>String(call[0]))).toEqual([f.ref.url,f.busan.url]);
    expect(nationalBusRouteOptions(undefined,selected.routes,true)).toEqual([]);
    expect(nationalBusRouteOptions(f.seoul,selected.routes,true)).toEqual([]);
    expect(nationalBusRouteOptions(f.busan,[f.route,{...f.route,id:'sejong-b2'},{...f.route,city_code:'11'}],true).map(value=>value.id)).toEqual([f.route.id]);
  });

  it('bounds a national city list while keeping unsupported Seoul searchable and an explicit selection visible',()=>{
    const f=fixture(),cities=Array.from({length:138},(_,index):TransitCity=>({...f.busan,city_code:String(1000+index),city_name:`도시 ${String(index).padStart(3,'0')}`}));
    cities[137]=f.seoul;
    const all=filterBusCities(cities,'');
    expect(all.matchCount).toBe(138);expect(all.options).toHaveLength(24);
    expect(filterBusCities(cities,'  서울  ').options).toEqual([f.seoul]);
    expect(filterBusCities(cities,'도시 ０１２').options.map(city=>city.city_name)).toEqual(['도시 012']);
    expect(filterBusCities(cities,'없는지역').matchCount).toBe(0);
    const selected=filterBusCities(cities,'','11');
    expect(selected.options).toHaveLength(24);expect(selected.options).toContain(f.seoul);
    expect(busCityAvailability(selected.options.find(city=>city.city_code==='11'))).toBe('unsupported');
  });
});
