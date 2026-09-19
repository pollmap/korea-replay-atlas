import type {Track} from './contracts';
export interface PreparedTrack {track:Track;times:Float64Array;}
export function prepareTrack(track:Track):PreparedTrack{
  if(!Array.isArray(track.points)||!Number.isFinite(track.max_gap_seconds)||track.max_gap_seconds<=0)throw new Error('관측 경로 형식이 올바르지 않습니다.');
  let previous=-Infinity;
  const times=Float64Array.from(track.points,p=>{
    const time=Date.parse(p.time);
    if(!Number.isFinite(time)||time<=previous||!Number.isFinite(p.lon)||Math.abs(p.lon)>180||!Number.isFinite(p.lat)||Math.abs(p.lat)>90||(p.height!==undefined&&!Number.isFinite(p.height)))throw new Error('관측 경로의 시각 또는 좌표가 올바르지 않습니다.');
    previous=time;return time;
  });
  return {track,times};
}
/** Same gap/endpoint semantics as trackPosition, with timestamps parsed once. */
export function preparedTrackPosition({track,times}:PreparedTrack,instant:number){
  let lo=0,hi=times.length-1;
  while(lo<=hi){const mid=(lo+hi)>>>1;if(times[mid]<=instant)lo=mid+1;else hi=mid-1;}
  const a=track.points[hi],b=track.points[hi+1];if(!a)return null;
  const at=times[hi];if(at===instant)return {lon:a.lon,lat:a.lat,height:a.height??0};
  if(!b)return null;const gap=(times[hi+1]-at)/1000;if(gap<=0||gap>track.max_gap_seconds)return null;
  const f=(instant-at)/(times[hi+1]-at);return {lon:a.lon+(b.lon-a.lon)*f,lat:a.lat+(b.lat-a.lat)*f,height:(a.height??0)+((b.height??0)-(a.height??0))*f};
}
