import {describe,it,expect} from 'vitest';
import {kstDate,kstInstant,kstSeconds,recordingDays,solarPosition,trackPosition} from '../shared/time';
import type {Asset,Track} from '../shared/contracts';

describe('KST and solar clock',()=>{
  it('maps Korean midnight to the previous UTC day',()=>{
    expect(new Date(kstInstant('2026-09-16',0)).toISOString()).toBe('2026-09-15T15:00:00.000Z');
    expect(kstDate('2026-09-15T15:00:00Z')).toBe('2026-09-16');
    expect(kstSeconds(kstInstant('2026-09-16',43123))).toBe(43123);
  });
  it('rejects impossible dates and seconds',()=>{
    expect(()=>kstInstant('2026-02-30',0)).toThrow();
    expect(()=>kstInstant('2026-09-16',86400)).toThrow();
    expect(()=>kstInstant('invalid',0)).toThrow();
  });
  it('keeps nighttime below the horizon and expresses bearing from north',()=>{
    const noon=solarPosition(kstInstant('2026-09-16',12*3600),36.332,127.433);
    const midnight=solarPosition(kstInstant('2026-09-16',0),36.332,127.433);
    expect(noon.altitude).toBeGreaterThan(40);expect(noon.azimuth).toBeGreaterThan(150);expect(noon.azimuth).toBeLessThan(200);
    expect(midnight.altitude).toBeLessThan(0);
  });
});
const track:Track={id:'test-only',label:'test fixture',layer:'bus',position_evidence:'calculation',max_gap_seconds:120,provenance:{source_id:'test',source_record_id:'test',dataset_version:'test',observed_at:null,retrieved_at:'2026-09-16T00:00:00Z',evidence_type:'observation',input_hash:'test',transform_version:'test'},points:[{time:'2026-09-16T00:00:00Z',lon:127,lat:36},{time:'2026-09-16T00:01:00Z',lon:127.001,lat:36.001},{time:'2026-09-16T00:10:00Z',lon:128,lat:37}]};
describe('observation-bounded replay',()=>{
  it('interpolates only between sufficiently close observations',()=>expect(trackPosition(track,Date.parse('2026-09-16T00:00:30Z'))?.lon).toBeCloseTo(127.0005));
  it('does not extrapolate before or after observations',()=>{
    expect(trackPosition(track,Date.parse('2026-09-15T23:59:59Z'))).toBeNull();
    expect(trackPosition(track,Date.parse('2026-09-16T00:10:01Z'))).toBeNull();
  });
  it('keeps a nine-minute collection outage empty',()=>expect(trackPosition(track,Date.parse('2026-09-16T00:05:00Z'))).toBeNull());
  it('preserves the exact final observed point',()=>expect(trackPosition(track,Date.parse('2026-09-16T00:10:00Z'))).toMatchObject({lon:128,lat:37}));
});
it('lists only recorded Korean dates, splitting midnight without filling a multi-year gap',()=>{
  const base={id:'fixture',layer:'satellite',format:'imagery',url:'/fixture',bbox:[124,33,132,39],source_id:'test',version:'test',count:1,sha256:'test'} as const;
  const assets=[{...base,bbox:[...base.bbox],from:'2022-01-01T00:00:32Z',to:'2022-01-01T00:10:32Z'},{...base,bbox:[...base.bbox],from:'2026-09-09T14:55:00Z',to:'2026-09-09T15:10:00Z'}] as Asset[];
  const days=recordingDays(assets);
  expect(days.map(d=>d.date)).toEqual(['2022-01-01','2026-09-09','2026-09-10']);
  expect(days[2].from).toBe(kstInstant('2026-09-10',0));
  expect(days[1].to).toBe(kstInstant('2026-09-10',0)-1);
});
