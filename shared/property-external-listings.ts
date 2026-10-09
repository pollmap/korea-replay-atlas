import type {PropertyComplex} from './property';
export interface PropertyExternalListing {
 provider:'naver';providerComplexId:string;complexId:string;lawdCode:string;legalDong:string;lotNumber:string;
 checkedAt:string;evidence:'provider_complex_page_exact_lot';
}
/** IDs were checked against the provider page's full lot address. No prices, listings or private collection data are copied. */
export const VERIFIED_PROPERTY_LINKS:readonly PropertyExternalListing[]=[
 {provider:'naver',providerComplexId:'111515',complexId:'molit-apt:11710:11710-8865',lawdCode:'11710',legalDong:'가락동',lotNumber:'913',checkedAt:'2026-10-10',evidence:'provider_complex_page_exact_lot'},
];
export function verifiedPropertyListing(complex:PropertyComplex,links:readonly PropertyExternalListing[]=VERIFIED_PROPERTY_LINKS){
 if(complex.address_conflict||!complex.legal_dong_name||!complex.lot_number)return null;
 const matches=links.filter(link=>link.complexId===complex.id&&link.lawdCode===complex.lawd_code&&link.legalDong===complex.legal_dong_name&&link.lotNumber===complex.lot_number&&link.evidence==='provider_complex_page_exact_lot'&&link.provider==='naver'&&/^\d{1,12}$/.test(link.providerComplexId)&&/^\d{4}-\d{2}-\d{2}$/.test(link.checkedAt));
 if(matches.length!==1)return null;
 const link=matches[0];
 // Do not forward unverified budget/area parameters or any personal state.
 return {...link,href:`https://fin.land.naver.com/complexes/${link.providerComplexId}`};
}
