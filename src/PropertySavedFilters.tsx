import {useEffect,useRef,useState} from 'react';
import type {PropertyDiscoveryFilters} from '../shared/property-discovery';
import {checkedDiscoveryFilters,mergeSavedFilters,parseSavedFilters,savedFiltersJson,SAVED_FILTERS_KEY,type SavedPropertyFilter} from '../shared/property-saved-filters';
function read(){const raw=localStorage.getItem(SAVED_FILTERS_KEY);return raw===null?[]:parseSavedFilters(raw);}
export default function PropertySavedFilters({region,trade,filters,onApply}:{region:string;trade:'sale'|'rent';filters:PropertyDiscoveryFilters;onApply:(value:PropertyDiscoveryFilters)=>void}){
  const [items,setItems]=useState<SavedPropertyFilter[]>([]),[name,setName]=useState(''),[message,setMessage]=useState('');
  const upload=useRef<HTMLInputElement>(null);
  useEffect(()=>{const refresh=()=>{try{setItems(read());setMessage('');}catch{setMessage('저장된 검색조건을 읽지 못했습니다. 기존 자료는 유지합니다.');}};refresh();const changed=(event:StorageEvent)=>{if(event.key===SAVED_FILTERS_KEY||event.key===null)refresh();};window.addEventListener('storage',changed);return()=>window.removeEventListener('storage',changed);},[]);
  const update=(transform:(rows:SavedPropertyFilter[])=>SavedPropertyFilter[])=>{try{const next=parseSavedFilters(savedFiltersJson(transform(read())));localStorage.setItem(SAVED_FILTERS_KEY,savedFiltersJson(next));setItems(next);setMessage('');return true;}catch(error){setMessage(error instanceof Error?error.message:'검색조건을 저장하지 못했습니다.');return false;}};
  const relevant=items.filter(row=>row.region===region&&row.trade===trade);
  const download=()=>{const url=URL.createObjectURL(new Blob([savedFiltersJson(items)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='korea-replay-searches.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  return <details className="property-saved-filters"><summary>내 검색조건 <span>{relevant.length}</span></summary>
    <form onSubmit={event=>{event.preventDefault();try{const checked=checkedDiscoveryFilters(filters,trade);if(update(previous=>mergeSavedFilters(previous,[{id:crypto.randomUUID(),name:name.trim()||`${trade==='sale'?'매매':'전월세'} 조건 ${relevant.length+1}`,region,trade,filters:checked,savedAt:new Date().toISOString()}]))){setName('');setMessage('현재 조건을 저장했습니다.');}}catch(error){setMessage(error instanceof Error?error.message:'조건을 확인해 주세요.');}}}><input aria-label="검색조건 이름" maxLength={40} value={name} onChange={event=>setName(event.target.value)} placeholder="예: 국평 8억 이하"/><button type="submit">현재 조건 저장</button></form>
    {!!relevant.length&&<ul>{relevant.map(row=><li key={row.id}><button onClick={()=>{onApply({...row.filters});setMessage(`${row.name} 조건을 적용했습니다.`);}}>{row.name}</button><button aria-label={`${row.name} 저장 조건 삭제`} onClick={()=>update(previous=>previous.filter(item=>item.id!==row.id))}>×</button></li>)}</ul>}
    <div className="saved-filter-files"><button onClick={()=>upload.current?.click()}>가져오기</button><button disabled={!items.length} onClick={download}>전체 내보내기</button></div>
    <small>이 지역의 {trade==='sale'?'매매':'전월세'} 조건 · 계약월은 현재 선택 유지</small>
    <input ref={upload} hidden type="file" accept=".json,application/json" onChange={async event=>{const file=event.target.files?.[0];event.target.value='';if(!file)return;if(file.size>100_000){setMessage('검색조건 파일은 100KB 이하여야 합니다.');return;}try{const incoming=parseSavedFilters(await file.text());if(update(previous=>mergeSavedFilters(previous,incoming)))setMessage('검색조건을 가져왔습니다. 다른 지역 조건은 해당 지역에서 보입니다.');}catch{setMessage('검색조건 파일을 확인해 주세요. 기존 조건은 유지됩니다.');}}}/>
    {message&&<p role="status">{message}</p>}
  </details>;
}
