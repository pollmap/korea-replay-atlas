import type {Place} from './contracts';
import {PLACES} from './sources';

export interface SearchEntry extends Place {alt_names?:string[];kind_label?:string;}
interface PreparedEntry {place:Place;name:string;text:string;rank:number;}
export interface PlaceSearchIndex {readonly rows:readonly PreparedEntry[];readonly names:readonly PreparedEntry[];readonly entryCount:number;}
const preparedByEntries=new WeakMap<readonly SearchEntry[],PlaceSearchIndex>();
const collator=new Intl.Collator('ko');
const normalize=(value:string)=>value.normalize('NFKC').toLocaleLowerCase().replace(/\s/g,'');

/** Published entries are immutable. Normalize and alphabetize once per source version. */
export function preparePlaceSearch(entries:readonly SearchEntry[]):PlaceSearchIndex {
  const cached=preparedByEntries.get(entries);
  if(cached)return cached;
  const rows:PreparedEntry[]=[];
  const candidates:SearchEntry[]=[...PLACES,...entries];
  for(const p of candidates){
    if(!p||typeof p.id!=='string'||typeof p.name!=='string'||!Number.isFinite(p.lon)||!Number.isFinite(p.lat)||p.lon<124||p.lon>132.5||p.lat<32||p.lat>39.5)continue;
    const region=typeof p.region==='string'?p.region:'';
    const aliases=Array.isArray(p.alt_names)?p.alt_names.filter((alias):alias is string=>typeof alias==='string'):[];
    rows.push({place:{id:p.id,name:p.name,region:region||(typeof p.kind_label==='string'&&p.kind_label)||'공개 지도 시설',lon:p.lon,lat:p.lat,range:Number.isFinite(p.range)?Math.min(2500000,Math.max(500,p.range)):5000},name:normalize(p.name),text:normalize(`${p.name} ${region} ${aliases.join(' ')}`),rank:0});
  }
  // Stable sort preserves the former source-order tie break, including presets.
  rows.sort((a,b)=>collator.compare(a.place.name,b.place.name));
  rows.forEach((row,rank)=>{row.rank=rank;});
  const names=rows.slice().sort((a,b)=>a.name<b.name?-1:a.name>b.name?1:a.rank-b.rank);
  const result={rows,names,entryCount:entries.length};
  preparedByEntries.set(entries,result);
  return result;
}

export function searchPreparedPlaces(index:PlaceSearchIndex,query:string):Place[]{
  const q=normalize(query);
  if(!q)return PLACES;
  const selected:{row:PreparedEntry;score:number}[]=[];
  const byId=new Map<string,{row:PreparedEntry;score:number}>();
  const compare=(a:{row:PreparedEntry;score:number},b:{row:PreparedEntry;score:number})=>a.score-b.score||a.row.rank-b.row.rank;
  let low=0,high=index.names.length;
  while(low<high){const middle=(low+high)>>>1;if(index.names[middle].name<q)low=middle+1;else high=middle;}
  // Binary-search the prefix range before considering substring/alias matches.
  for(let at=low;at<index.names.length;at++){
    const row=index.names[at];if(!row.name.startsWith(q))break;
    const score=row.name===q?0:1,candidate={row,score};
    if(selected.length===20&&compare(candidate,selected[19])>=0)continue;
    const previous=byId.get(row.place.id);
    if(previous){
      if(compare(previous,candidate)<=0)continue;
      selected.splice(selected.indexOf(previous),1);byId.delete(row.place.id);
    }
    if(selected.length===20){
      const removed=selected.pop()!;byId.delete(removed.row.place.id);
    }
    const insert=selected.findIndex(item=>compare(item,candidate)>0);
    if(insert<0)selected.push(candidate);else selected.splice(insert,0,candidate);
    byId.set(row.place.id,candidate);
    if(selected.length===20&&selected[19].score===0)break;
  }
  if(selected.length<20){
    // Alphabetical traversal makes the first remaining substring hits final;
    // no full result array, full result sort or quadratic deduplication exists.
    for(const row of index.rows){
      if(!row.text.includes(q)||byId.has(row.place.id))continue;
      const candidate={row,score:2};selected.push(candidate);byId.set(row.place.id,candidate);
      if(selected.length===20)break;
    }
  }
  return selected.map(({row})=>({...row.place}));
}

export function searchPlaces(entries:readonly SearchEntry[],query:string):Place[]{
  if(!normalize(query))return PLACES;
  return searchPreparedPlaces(preparePlaceSearch(entries),query);
}
