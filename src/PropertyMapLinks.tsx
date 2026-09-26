import type {PropertyComplex} from '../shared/property';
import {complexMapLinks} from '../shared/external-maps';

export default function PropertyMapLinks({complex,region}:{complex:PropertyComplex;region:string}) {
  const links=complexMapLinks(complex,region);
  if(!links.length)return null;
  return <div className="property-map-links"><nav aria-label="단지 외부 지도">{links.map(link=><a key={link.label} href={link.href} target="_blank" rel="noopener noreferrer" aria-label={`${link.label}에서 ${complex.name} 열기 · 새 창`}>{link.label} ↗</a>)}</nav><small>주소 검색 · 거리뷰와 길찾기는 외부 지도에서 확인</small></div>;
}
