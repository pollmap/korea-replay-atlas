import {describe,expect,it} from 'vitest';
import {map2DLiveBuses,map2DLiveBusSelection} from '../src/map2d-live';
import type {LiveTransitSnapshot} from '../shared/live-transit';

const now=Date.parse('2026-09-20T02:00:00Z'),stamp=new Date(now).toISOString();
function snapshot():LiveTransitSnapshot{return {schema_version:1,mode:'live',kind:'bus',status:'available',
  target:{id:'fixture-route',label:'검증 노선'},retrieved_at:stamp,served_at:stamp,expires_at:new Date(now+90000).toISOString(),refresh_after_seconds:90,max_source_age_seconds:null,
  source:{id:'tago',page_url:'https://www.data.go.kr',license:'출처표시',access:'official-key'},coverage:{scope:'selected-route',complete:true},
  counts:{upstream:1,accepted:1,invalid:0,stale:0,duplicate:0,ambiguous:0},quota:{daily_limit:1000,local_budget:100,guard:'isolate-and-regional-cache',global_enforced:false},
  vehicles:[{kind:'bus',id:'fixture-source-vehicle',label:'검증 버스',observed_at:null,retrieved_at:stamp,source_received_at:null,
    route_id:'fixture-route',city_code:'25',position:{lon:127.4012345,lat:36.3012345,crs:'EPSG:4326',method:'provider-map-matched'},
    station_id:null,station_name:'검증 정류장',station_order:null}]};}

describe('2D current buses independent of Cesium',()=>{
  it('keeps exact source coordinates and IDs without movement, height or input mutation',()=>{
    const input=snapshot(),original=structuredClone(input),data=map2DLiveBuses(input,now);
    expect(data.features).toHaveLength(1);
    expect(data.features[0]).toEqual({type:'Feature',id:'live-bus:fixture-source-vehicle',
      properties:{live_bus_id:'live-bus:fixture-source-vehicle',label:'검증 버스'},
      geometry:{type:'Point',coordinates:[127.4012345,36.3012345]}});
    expect(map2DLiveBuses(input,now+45000)).toEqual(data);
    data.features[0].geometry.coordinates[0]=128;
    expect(input).toEqual(original);
    expect(map2DLiveBuses({...input,status:'partial'},now).features).toHaveLength(1);
  });

  it('removes points and selections at TTL and on empty, unavailable or future-dated snapshots',()=>{
    const input=snapshot(),id='live-bus:fixture-source-vehicle';
    expect(map2DLiveBuses(input,now+89999).features).toHaveLength(1);
    expect(map2DLiveBuses(input,now+90000).features).toEqual([]);
    expect(map2DLiveBusSelection(input,id,now+90000)).toBeNull();
    for(const value of [null,undefined,{...input,status:'empty' as const,vehicles:[]},
      {...input,status:'unavailable' as const},{...input,status:'stale' as const},
      {...input,retrieved_at:new Date(now+31000).toISOString()}, {...input,expires_at:null}]){
      expect(map2DLiveBuses(value,now).features).toEqual([]);
      expect(map2DLiveBusSelection(value,id,now)).toBeNull();
    }
    expect(map2DLiveBuses(input,Number.NaN).features).toEqual([]);
    expect(map2DLiveBuses({...input,vehicles:[]},now).features).toEqual([]);
  });

  it('preserves official-record selection semantics and never invents subway GPS',()=>{
    const input=snapshot(),pick=map2DLiveBusSelection(input,'live-bus:fixture-source-vehicle',now);
    expect(pick).toMatchObject({name:'검증 버스',rawHeight:null,properties:{live:true,expires_at:input.expires_at},
      provenance:{source_id:'tago',source_record_id:'fixture-source-vehicle',observed_at:null,retrieved_at:stamp,evidence_type:'official_record'}});
    expect(pick?.detail).toContain('제공기관의 도로 보정 위치');
    expect(pick?.detail).toContain('원천 측정 시각과 고도는 제공되지 않습니다');
    expect(pick).not.toHaveProperty('height');
    expect(map2DLiveBusSelection(input,'fixture-source-vehicle',now)).toBeNull();
    expect(map2DLiveBusSelection(input,'live-bus:another-vehicle',now)).toBeNull();
    const subway:LiveTransitSnapshot={...input,kind:'subway',vehicles:[{kind:'subway',id:'train-fixture',label:'검증 열차',
      observed_at:null,retrieved_at:stamp,source_received_at:stamp,line_id:'fixture-line',train_no:'fixture-train',direction:'up',station_id:'fixture-station',
      station_name:'검증역',terminal_name:null,reported_status:'arrived',position:null,station_mapping:'unresolved'}]};
    expect(map2DLiveBuses(subway,now).features).toEqual([]);
    expect(map2DLiveBusSelection(subway,'live-bus:train-fixture',now)).toBeNull();
  });
});
