import type {PropertyComplex} from '../shared/property';
import {verifiedPropertyListing} from '../shared/property-external-listings';
import {addressMapLinks} from '../shared/external-maps';
export default function PropertyListingLink({complex,region}:{complex:PropertyComplex;region:string}){
 const direct=verifiedPropertyListing(complex);
 const address=[region,complex.legal_dong_name,complex.lot_number,complex.name].filter(Boolean).join(' ');
 const fallback=addressMapLinks(address)[0];
 if(!direct&&!fallback)return null;
 return <a className="property-listing-link" href={direct?.href??fallback.href} target="_blank" rel="noopener noreferrer" title={direct?'네이버 부동산 단지 페이지 · 예산과 면적은 원문에서 선택':'단지 매물 페이지 미연결 · 네이버 지도에서 주소 검색'} aria-label={`${complex.name} ${direct?'매물 원문 보기':'주소로 찾기'} · 새 창`}>{direct?'매물 원문 보기':'주소로 찾기'} ↗</a>;
}
