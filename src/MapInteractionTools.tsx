import {useRef,useState,type RefObject} from 'react';
import type {Place} from '../shared/contracts';
import type {MapHandle} from './MapScene';
import {formatArea,formatDistance,mergeBookmarks,validateBookmarks,type MapBookmark,type Measurement,type MeasurementMode} from '../shared/map-tools';

const KEY='korea-replay-bookmarks-v1';
function stored():MapBookmark[]{try{const text=localStorage.getItem(KEY);return text?validateBookmarks(JSON.parse(text)).bookmarks:[];}catch{return [];}}
function download(name:string,text:string){const url=URL.createObjectURL(new Blob([text],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
interface Props {map:RefObject<MapHandle|null>;view:'2d'|'3d';place:Place;hidden:boolean;measurement:Measurement;onMeasure:(mode:MeasurementMode)=>void;onUndo:()=>void;onNotice:(message:string)=>void;onLocate:(place:Place)=>void;}
export default function MapInteractionTools({map,view,place,hidden,measurement,onMeasure,onUndo,onNotice,onLocate}:Props){
  const [open,setOpen]=useState(false),[bookmarks,setBookmarks]=useState(stored),[locating,setLocating]=useState(false);
  const importRef=useRef<HTMLInputElement>(null);
  const persist=(rows:MapBookmark[])=>{
    try{const file=validateBookmarks({schema_version:1,kind:'korea-replay-bookmarks',bookmarks:rows});localStorage.setItem(KEY,JSON.stringify(file));setBookmarks(file.bookmarks);return true;}
    catch{onNotice('즐겨찾기를 저장하지 못했습니다. 저장 공간과 항목 수를 확인해 주세요.');return false;}
  };
  const save=()=>{
    const current=map.current?.viewport?.()??place,id=crypto.randomUUID();
    const row={id,name:place.name,position:{...current,id:`bookmark-${id}`,name:place.name,region:'저장한 위치'},created_at:new Date().toISOString()};
    if(persist([...bookmarks,row])){setOpen(true);onNotice('현재 지도 위치를 이 기기에 저장했습니다.');}
  };
  const locate=()=>{
    if(!navigator.geolocation){onNotice('이 브라우저는 현재 위치를 지원하지 않습니다.');return;}
    setLocating(true);
    navigator.geolocation.getCurrentPosition(({coords})=>{
      setLocating(false);if(coords.longitude<124||coords.longitude>132.5||coords.latitude<32||coords.latitude>39.5){onNotice('현재 서비스의 지도 범위는 대한민국입니다.');return;}
      onLocate({id:'my-location',name:'내 위치',region:`기기 위치 · 오차 약 ${Math.round(coords.accuracy)}m`,lon:coords.longitude,lat:coords.latitude,range:2500});
    },()=>{setLocating(false);onNotice('현재 위치를 확인하지 못했습니다. 브라우저 위치 권한을 확인해 주세요.');},{enableHighAccuracy:false,timeout:10000,maximumAge:60000});
  };
  return <div className="interaction-tools" hidden={hidden}>
    <div className="interaction-buttons" role="group" aria-label="지도 도구">
      <button onClick={locate} disabled={locating} title="내 위치로 이동">{locating?'확인 중':'내 위치'}</button>
      {view==='2d'&&<><button aria-pressed={measurement.mode==='distance'} onClick={()=>onMeasure(measurement.mode==='distance'?'none':'distance')}>거리</button><button aria-pressed={measurement.mode==='area'} onClick={()=>onMeasure(measurement.mode==='area'?'none':'area')}>면적</button></>}
      <button onClick={save} title="현재 지도 위치 저장">저장 ☆</button>
      <button aria-expanded={open} aria-controls="map-bookmarks" onClick={()=>setOpen(value=>!value)}>즐겨찾기 {bookmarks.length||''}</button>
    </div>
    {measurement.mode!=='none'&&view==='2d'&&<div className="measurement-readout" role="status"><strong>{measurement.mode==='area'?formatArea(measurement.area_m2):formatDistance(measurement.distance_m)}</strong><span>지도에 점을 선택하세요 · {measurement.points.length}점</span><small>지표면 근사값 · 측량·지적 면적이 아닙니다.</small><div><button onClick={onUndo} disabled={!measurement.points.length}>이전 점 취소</button><button onClick={()=>onMeasure('none')}>측정 닫기</button></div></div>}
    {open&&<section id="map-bookmarks" className="bookmarks-panel" aria-label="저장한 위치"><header><strong>저장한 위치</strong><button aria-label="즐겨찾기 닫기" onClick={()=>setOpen(false)}>×</button></header><p>이 기기에 저장됩니다. 파일로 옮길 수 있습니다.</p>{!bookmarks.length&&<p>지도를 옮긴 뒤 ‘저장’을 눌러 보세요.</p>}<ul>{bookmarks.map(row=><li key={row.id}><button onClick={()=>onLocate(row.position)}>{row.name}<small>{row.position.lat.toFixed(4)}, {row.position.lon.toFixed(4)}</small></button><button aria-label={`${row.name} 즐겨찾기 삭제`} onClick={()=>persist(bookmarks.filter(v=>v.id!==row.id))}>×</button></li>)}</ul><footer><button onClick={()=>download('korea-replay-bookmarks.json',JSON.stringify({schema_version:1,kind:'korea-replay-bookmarks',bookmarks},null,2))}>내보내기</button><button onClick={()=>importRef.current?.click()}>가져오기</button></footer><input ref={importRef} hidden type="file" accept="application/json,.json" onChange={async event=>{const file=event.target.files?.[0];event.target.value='';if(!file)return;try{if(file.size>256*1024)throw new Error('즐겨찾기 파일은 256KiB 이하여야 합니다.');const parsed=validateBookmarks(JSON.parse(await file.text()));if(persist(mergeBookmarks(bookmarks,parsed.bookmarks)))onNotice('즐겨찾기를 가져왔습니다. 기존 항목은 유지했습니다.');}catch(error){onNotice(error instanceof Error?error.message:'파일을 읽지 못했습니다.');}}}/></section>}
  </div>;
}
