import {describe,it,expect} from 'vitest';
import {parseTago,normalizeTago,replayChunk} from '../worker/collectors';
import {trackPosition} from '../shared/time';
describe('TAGO source boundary',()=>{
  it('does not treat HTTP-200 authentication failures as empty valid data',()=>expect(()=>parseTago({response:{header:{resultCode:'30'},body:{totalCount:0}}})).toThrow());
  it('accepts singleton and empty item responses',()=>{
    expect(parseTago({response:{header:{resultCode:'00'},body:{totalCount:1,items:{item:{gpslong:127}}}}}).items).toHaveLength(1);
    expect(parseTago({response:{header:{resultCode:'00'},body:{totalCount:0,items:''}}}).items).toHaveLength(0);
  });
  it('rejects invalid positions and never invents a source observation time',async()=>{
    const result=await normalizeTago([{vehicleno:'fixture-only',gpslong:127.4,gpslati:36.3},{vehicleno:'invalid',gpslong:0,gpslati:0}],{city_code:'25',route_id:'fixture'},'2026-09-16T01:00:00Z','fixture-hash');
    expect(result.rejected).toBe(1);expect(result.observations[0].observed_at).toBeNull();expect(result.observations[0].entity_id).not.toContain('fixture-only');
  });
  it('sorts real observations while preserving gaps and labels interpolation as calculation',()=>{
    const row={entity_id:'fixture',route_id:'r',label:'fixture',lon:127,lat:36,observed_at:null,input_hash:'h'};
    const track=replayChunk([{...row,retrieved_at:'2026-09-16T01:10:00Z'},{...row,retrieved_at:'2026-09-16T01:00:00Z'}]).tracks[0];
    expect(track.position_evidence).toBe('calculation');expect(trackPosition(track,Date.parse('2026-09-16T01:05:00Z'))).toBeNull();
  });
});
