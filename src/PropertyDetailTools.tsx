import {useEffect,useState} from 'react';
import type {PropertyComplex,RegionMetric} from '../shared/property';
import {metricCount,moneyLabel,monthLabel} from '../shared/property-view';
import {equalLoanPayment,readComplexNote} from './property-desktop';

export function DetailUnavailable({title,link}:{title:string;link?:{label:string;url:string}}){
  return <section className="detail-unavailable" aria-label={title}><h3>{title}<span className="detail-data-state" role="status">자료 연결 전</span></h3>{link&&<a href={link.url} target="_blank" rel="noopener noreferrer">{link.label} ↗</a>}</section>;
}

export function PropertyRegionAnalysis({metrics,month,region}:{metrics:readonly RegionMetric[];month:string;region:string}){
  const current=metrics.find(item=>item.deal_month===month),count=current?metricCount(current):null;
  const price=current?.status==='complete'?current.median_price_per_m2_krw:null;
  return <section className="detail-region-analysis" aria-label="지역 분석"><h3>{region} 지역 분석</h3><div className="detail-metric-grid"><div><span>{monthLabel(month)} 확인 거래</span><strong>{count===null?'미수집':`${count.toLocaleString('ko-KR')}건`}</strong></div><div><span>지역 전용㎡당 거래 중앙값</span><strong>{price==null?'미수집':`${moneyLabel(price)}원`}</strong></div></div><details><summary>지표 기준</summary><p>선택 지역·계약월의 신고 거래 기준입니다. 거래 구성에 영향을 받는 자체 통계이며 공식 가격지수와 다릅니다.</p></details><DetailUnavailable title="인구·입주·가격지수"/></section>;
}

export function PropertyLoanCalculator(){
  const [principal,setPrincipal]=useState(''),[rate,setRate]=useState(''),[years,setYears]=useState('30');
  const entered=principal.trim()!==''&&rate.trim()!=='';
  const result=entered?equalLoanPayment(Number(principal)*1e8,Number(rate),Number(years)*12):null;
  return <section className="detail-loan-calculator" aria-label="대출 상환 계산"><h3>대출 상환 계산</h3><div className="detail-calculator-inputs"><label>대출금액 · 억원<input inputMode="decimal" value={principal} onChange={event=>setPrincipal(event.target.value)} placeholder="금액 입력"/></label><label>연 금리 · %<input inputMode="decimal" value={rate} onChange={event=>setRate(event.target.value)} placeholder="금리 입력"/></label><label>상환 기간<select value={years} onChange={event=>setYears(event.target.value)}>{[1,5,10,15,20,25,30,35,40,50].map(value=><option key={value} value={value}>{value}년</option>)}</select></label></div><div className="detail-loan-result" aria-live="polite">{result?<><span>월 원리금</span><strong>{result.monthly.toLocaleString('ko-KR')}원</strong><small>총 이자 {moneyLabel(result.interest)}원 · 총 상환 {moneyLabel(result.total)}원</small></>:<p>{entered?'금액은 0 초과 10,000억원 이하, 금리는 0–100%로 입력해 주세요.':'금액과 금리를 입력하면 월 상환액을 계산합니다.'}</p>}</div><details><summary>계산 기준</summary><p>고정 금리·월 단위 원리금균등상환의 산술 계산입니다. 대출 승인금액·중도상환수수료·세금·금리 변동은 포함하지 않습니다.</p></details><DetailUnavailable title="세금·중개보수"/></section>;
}

export function PropertyComplexNote({complex}:{complex:PropertyComplex}){
  const key=`korea-replay:complex-note:v1:${complex.id}`;
  const [draft,setDraft]=useState(''),[message,setMessage]=useState(''),[readFailed,setReadFailed]=useState(false);
  useEffect(()=>{try{setDraft(readComplexNote(localStorage.getItem(key)));setMessage('');setReadFailed(false);}catch{setDraft('');setReadFailed(true);setMessage('기존 메모를 읽지 못했습니다. 저장 내용을 덮어쓰지 않습니다.');}},[key]);
  const save=()=>{if(readFailed)return;try{localStorage.setItem(key,JSON.stringify({version:1,text:draft,savedAt:new Date().toISOString()}));setMessage('이 기기에 저장했습니다.');}catch{setMessage('메모를 저장하지 못했습니다. 입력 내용은 유지됩니다.');}};
  const download=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify({version:1,complexId:complex.id,name:complex.name,text:draft},null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='korea-replay-complex-note.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  return <details className="detail-personal-note"><summary>나의 단지 메모</summary><label><span className="sr-only">{complex.name} 개인 메모</span><textarea value={draft} maxLength={4000} onChange={event=>setDraft(event.target.value)} placeholder="관심 평형, 방문 기록, 확인할 내용을 메모하세요."/></label><div><button disabled={readFailed} onClick={save}>메모 저장</button><button disabled={!draft} onClick={download}>내보내기</button></div><small>이 기기에만 저장 · 공유 링크에 포함하지 않음</small>{message&&<p role="status">{message}</p>}</details>;
}
