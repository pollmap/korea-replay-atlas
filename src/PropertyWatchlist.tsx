import {useEffect,useRef,useState} from 'react';
import {mergeWatchlist,parseWatchlist,watchlistJson,WATCHLIST_KEY,type WatchedComplex} from '../shared/property-watchlist';

const read=()=>{const raw=localStorage.getItem(WATCHLIST_KEY);return raw===null?[]:parseWatchlist(raw);};
export function usePropertyWatchlist(){
  const [items,setItems]=useState<WatchedComplex[]>([]),[message,setMessage]=useState('');
  useEffect(()=>{const refresh=()=>{try{setItems(read());setMessage('');}catch{setMessage('관심 단지를 읽지 못했습니다. 저장 권한과 가져온 파일을 확인해 주세요.');}};refresh();
    const changed=(event:StorageEvent)=>{if(event.key===WATCHLIST_KEY||event.key===null)refresh();};window.addEventListener('storage',changed);return()=>window.removeEventListener('storage',changed);
  },[]);
  const update=(transform:(previous:WatchedComplex[])=>WatchedComplex[])=>{try{const next=transform(read());localStorage.setItem(WATCHLIST_KEY,watchlistJson(next));setItems(next);setMessage('');return true;}catch(error){setMessage(error instanceof Error?error.message:'기기에 저장하지 못했습니다.');return false;}};
  return {items,message,setMessage,toggle:(item:WatchedComplex)=>update(previous=>previous.some(row=>row.id===item.id)?previous.filter(row=>row.id!==item.id):mergeWatchlist(previous,[item])),
    remove:(id:string)=>update(previous=>previous.filter(row=>row.id!==id)),importItems:(raw:string)=>update(previous=>mergeWatchlist(previous,parseWatchlist(raw)))};
}
export default function PropertyWatchlist({items,onOpen,onRemove,onImport,onMessage}:{items:WatchedComplex[];onOpen:(item:WatchedComplex)=>void;onRemove:(id:string)=>void;onImport:(raw:string)=>boolean;onMessage:(value:string)=>void}){
  const upload=useRef<HTMLInputElement>(null);
  const download=()=>{const url=URL.createObjectURL(new Blob([watchlistJson(items)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='korea-replay-interest.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  return <section className="property-watchlist" aria-label="관심 아파트 목록"><div className="watchlist-heading"><div><h3>관심 아파트</h3><p>다시 보고 싶은 단지를 한곳에</p></div><strong>{items.length}<small> / 100</small></strong></div>
    <div className="watchlist-file-tools"><button onClick={()=>upload.current?.click()}>가져오기</button><button onClick={download} disabled={!items.length}>내보내기</button><input ref={upload} type="file" accept=".json,application/json" hidden onChange={async event=>{const file=event.target.files?.[0];event.target.value='';if(!file)return;if(file.size>100_000){onMessage('관심 단지 파일은 100KB 이하여야 합니다.');return;}try{if(onImport(await file.text()))onMessage('관심 단지를 가져왔습니다. 기존 목록은 유지됩니다.');}catch{onMessage('파일을 읽지 못했습니다.');}}}/></div>
    {!items.length?<div className="watchlist-empty"><span aria-hidden="true">☆</span><h4>관심 단지를 모아보세요</h4><p>단지 목록이나 상세 화면의 별을 누르면 여기에 저장됩니다. 로그인 없이 이 브라우저에서 사용할 수 있습니다.</p></div>:<ul>{items.map(item=><li key={item.id}><button className="watchlist-open" onClick={()=>onOpen(item)}><strong>{item.name}</strong><span>{item.address||'주소 미제공'}</span><small>실거래 보기 →</small></button><button className="watchlist-remove" aria-label={`${item.name} 관심 해제`} onClick={()=>onRemove(item.id)}>★</button></li>)}</ul>}
    <p className="property-caption">이 기기의 브라우저에 저장됩니다. 기기를 바꾸기 전에 파일로 내보내세요. 선택한 자료 버전에 단지가 없으면 알려드립니다.</p>
  </section>;
}
