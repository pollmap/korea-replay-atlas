import SunCalc from 'suncalc';
import type {Asset,LayerId,Track} from './contracts';
export const KST_OFFSET = 9 * 3600_000;
export function kstDate(instant: string | number | Date): string {
  return new Date(new Date(instant).getTime() + KST_OFFSET).toISOString().slice(0,10);
}
export function kstInstant(date: string, seconds: number): number {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(seconds) || seconds<0 || seconds>86399) throw new Error('올바르지 않은 한국시간');
  const result = Date.parse(`${date}T00:00:00+09:00`) + seconds * 1000;
  if (!Number.isFinite(result) || kstDate(result) !== date) throw new Error('존재하지 않는 날짜');
  return result;
}
export function kstSeconds(instant: number): number {
  const d = new Date(instant + KST_OFFSET); return d.getUTCHours()*3600 + d.getUTCMinutes()*60 + d.getUTCSeconds();
}
export function recordingDays(assets:Asset[]) {
  const days=new Map<string,{date:string;from:number;to:number;layers:LayerId[];labels:string[]}>();
  for(const a of assets){
    if(!a.from||!a.to||!['replay','imagery'].includes(a.format))continue;
    const from=Date.parse(a.from),to=Date.parse(a.to);
    if(!Number.isFinite(from)||!Number.isFinite(to)||to<from||to-from>32*86400_000)continue;
    for(let start=kstInstant(kstDate(from),0);start<=to;start+=86400_000){
      const date=kstDate(start),lower=Math.max(start,from),upper=Math.min(start+86399_999,to);
      const existing=days.get(date)??{date,from:lower,to:upper,layers:[],labels:[]};
      existing.from=Math.min(existing.from,lower);existing.to=Math.max(existing.to,upper);
      if(!existing.layers.includes(a.layer))existing.layers.push(a.layer);
      if(a.label&&!existing.labels.includes(a.label))existing.labels.push(a.label);
      days.set(date,existing);
    }
  }
  return [...days.values()].sort((a,b)=>a.from-b.from);
}
export function solarPosition(instant: number, lat: number, lon: number) {
  const p=SunCalc.getPosition(new Date(instant),lat,lon);
  return {altitude:p.altitude*180/Math.PI, azimuth:(p.azimuth*180/Math.PI+180)%360};
}
export function trackPosition(track: Track, instant: number) {
  const pts = track.points;
  let lo=0, hi=pts.length-1;
  while(lo<=hi){const mid=(lo+hi)>>1; if(Date.parse(pts[mid].time)<=instant) lo=mid+1; else hi=mid-1;}
  const a=pts[hi], b=pts[hi+1];
  if (!a) return null;
  const at=Date.parse(a.time);
  if (at===instant) return {lon:a.lon,lat:a.lat,height:a.height??0};
  if (!b) return null;
  const bt=Date.parse(b.time), gap=(bt-at)/1000;
  if(gap<=0 || gap>track.max_gap_seconds) return null;
  const f=(instant-at)/(bt-at);
  return {lon:a.lon+(b.lon-a.lon)*f,lat:a.lat+(b.lat-a.lat)*f,height:(a.height??0)+((b.height??0)-(a.height??0))*f};
}
