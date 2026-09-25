import {useState} from 'react';
import type {PropertyComplex} from '../shared/property';
import {COMMUTE_STORAGE_KEY,commuteUrl,parseCommuteDestinations,type CommuteDestination} from '../shared/property-commute';

export default function PropertyCommute({complex,region}:{complex:PropertyComplex;region:string}) {
  const [items,setItems]=useState<CommuteDestination[]>(()=>{try{return parseCommuteDestinations(localStorage.getItem(COMMUTE_STORAGE_KEY));}catch{return [];}});
  const [label,setLabel]=useState(''),[address,setAddress]=useState(''),[message,setMessage]=useState('');
  const origin=[region,complex.legal_dong_name,complex.lot_number,complex.name].filter(Boolean).join(' ');
  const save=(next:CommuteDestination[])=>{
    try{localStorage.setItem(COMMUTE_STORAGE_KEY,JSON.stringify({version:1,items:next}));setItems(next);setMessage('이 기기에 저장했습니다.');return true;}
    catch{setMessage('브라우저 저장 공간에 접근할 수 없습니다. 저장 설정을 확인해 주세요.');return false;}
  };
  return <section className="property-commute" aria-label="출퇴근과 교통"><h3>내 목적지까지</h3>
    <p className="commute-origin"><span>출발 단지</span><strong>{complex.name}</strong><small>{origin}</small></p>
    <p className="property-caption">목적지를 저장하면 다른 단지에서도 같은 직장·학교까지 경로를 열 수 있습니다. 소요시간·환승은 외부 지도에서 출발 시각과 위치를 확인하세요.</p>
    {complex.address_conflict&&<p role="status">신고 주소가 달라 출발지를 외부 지도에서 반드시 확인해야 합니다.</p>}
    <ul className="commute-destinations">{items.map((item,index)=><li key={`${item.label}:${item.address}`}><div><strong>{item.label}</strong><button aria-label={`${item.label} 목적지 삭제`} onClick={()=>save(items.filter((_,i)=>i!==index))}>삭제</button></div><p>{item.address}</p><nav aria-label={`${item.label} 경로 확인`}>{([['transit','대중교통'],['driving','자동차'],['walking','도보']] as const).map(([mode,title])=><a key={mode} href={commuteUrl(origin,item.address,mode)??undefined} target="_blank" rel="noopener noreferrer">{title} ↗</a>)}</nav></li>)}</ul>
    {items.length<3&&<form onSubmit={event=>{event.preventDefault();const next=parseCommuteDestinations(JSON.stringify({version:1,items:[{label:label.trim(),address:address.trim()}]}));if(!next.length){setMessage('목적지 이름과 주소를 입력해 주세요.');return;}if(items.some(item=>item.address===next[0].address)){setMessage('이미 저장한 목적지입니다.');return;}if(save([...items,next[0]])){setLabel('');setAddress('');}}}>
      <label>목적지 이름<input required maxLength={40} value={label} onChange={event=>setLabel(event.target.value)} placeholder="예: 직장, 학교"/></label>
      <label>주소 또는 정확한 장소명<input required maxLength={200} value={address} onChange={event=>setAddress(event.target.value)} placeholder="시·구와 주소를 함께 입력하세요"/></label>
      <button type="submit">목적지 저장 ({items.length}/3)</button></form>}
    {message&&<p role="status">{message}</p>}
    <p className="property-caption">Google 지도에서 열립니다. 경로 버튼을 누를 때 출발지와 목적지가 전달됩니다. 저장한 목적지는 이 기기에만 보관하며 공유 링크에 포함하지 않습니다. 교통수단별 지원 지역은 외부 지도에 따라 다릅니다.</p>
  </section>;
}
