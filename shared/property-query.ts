import type {PropertyRegion} from './property';
import type {PropertyViewState} from './property-view';
import {HISTORY_RANGES, type HistoryRange} from './property-history';
import {areaFromBounds, M2_PER_PYEONG, NATIONAL_AREA} from './property-area';

type Field = 'region'|'trade'|'budget'|'area'|'period';
export interface QueryChip {field:Field;label:string;start:number;end:number;}
export interface PropertyQuery {
  patch:Partial<PropertyViewState>; chips:QueryChip[]; unresolved:string[];
  regions:Pick<PropertyRegion,'lawd_code'|'name'>[]; active:boolean; valid:boolean;
}
/** Parse only explicit conditions. Residual words must never silently broaden a search. */
export function parsePropertyQuery(input:string,regions:readonly Pick<PropertyRegion,'lawd_code'|'name'>[]):PropertyQuery {
  const text=input.normalize('NFKC').trim(),patch:Partial<PropertyViewState>={},chips:QueryChip[]=[],unresolved:string[]=[];
  const used:boolean[]=Array(text.length).fill(false),fields=new Set<Field>();
  const consume=(field:Field,match:RegExpExecArray,label:string)=>{
    if(fields.has(field)){unresolved.push(`${label}: 같은 조건을 하나만 입력해 주세요.`);}
    fields.add(field);chips.push({field,label,start:match.index,end:match.index+match[0].length});
    for(let i=match.index;i<match.index+match[0].length;i++)used[i]=true;
  };
  if(text.length>120||Array.from(text).some(c=>c.charCodeAt(0)<32||c.charCodeAt(0)===127))return {patch,chips,unresolved:['검색어는 120자 이내로 입력해 주세요.'],regions:[],active:true,valid:false};
  for(const m of text.matchAll(/매매|전세|월세/g)){
    consume('trade',m,m[0]);patch.trade=m[0]==='매매'?'sale':'rent';patch.rentKind=m[0]==='매매'?'all':m[0]==='전세'?'jeonse':'monthly';
  }
  for(const m of text.matchAll(/(?:최근\s*)?(\d+)\s*(개월|년)/g)){
    const months=Number(m[1])*(m[2]==='년'?12:1);consume('period',m,`${months}개월`);
    if(HISTORY_RANGES.includes(months as HistoryRange))patch.historyMonths=months as HistoryRange;
    else unresolved.push('기간은 1·3·6개월 또는 1·3·5·10·20년을 선택해 주세요.');
  }
  for(const m of text.matchAll(/국평|(?:전용|공급)\s*\d+(?:\.\d+)?\s*(?:㎡|m2|m²|평)(?:\s*(?:이하|이상))?/gi)){
    consume('area',m,m[0]);
    if(m[0]==='국평'){patch.area=NATIONAL_AREA;patch.areaBasis='exclusive';continue;}
    if(m[0].startsWith('공급')){unresolved.push('공급면적은 전용면적으로 바꾸지 않습니다. 전용면적을 입력해 주세요.');continue;}
    const number=Number(m[0].match(/\d+(?:\.\d+)?/)![0]),value=m[0].includes('평')?Number((number*M2_PER_PYEONG).toFixed(5)):number;
    if(value<=0||value>10000){unresolved.push('전용면적을 확인해 주세요.');continue;}
    // A pyeong label is rounded, so do not pretend it identifies an exact registered type.
    if(m[0].includes('평')&&!/이하|이상/.test(m[0])){unresolved.push('전용 평수는 이상·이하 범위 또는 정확한 ㎡로 입력해 주세요.');continue;}
    patch.area=/이하/.test(m[0])?areaFromBounds('',String(value)):/이상/.test(m[0])?areaFromBounds(String(value),''):String(value);patch.areaBasis='exclusive';
  }
  for(const m of text.matchAll(/(\d+(?:\.\d+)?)\s*(억|만)(?:\s*(\d+)\s*만)?\s*원?\s*(이하|이상)/g)){
    const amount=Number(m[1])*(m[2]==='억'?1e8:1e4)+(m[3]?Number(m[3])*1e4:0);
    consume('budget',m,m[0]);
    if(!Number.isSafeInteger(amount)||amount<=0||amount>1e13||m[2]==='만'&&m[3]){unresolved.push('예산 금액을 확인해 주세요.');continue;}
    patch.latestPriceMinEok=m[4]==='이상'?String(amount/1e8):'';patch.latestPriceMaxEok=m[4]==='이하'?String(amount/1e8):'';
  }
  const remaining=()=>Array.from(text).map((c,i)=>used[i]?' ':c).join('').trim().replace(/\s+/g,' ');
  const residual=remaining(),tokens=residual.split(' ').filter(Boolean);
  const short=(s:string)=>s.replace(/특별자치시$|특별자치도$|특별시$|광역시$|도$|시$|구$|군$/,'');
  const matching=tokens.length?regions.filter(row=>{
    const words=row.name.split(/\s+/),aliases=new Set(words.flatMap(w=>[w,short(w)]));
    return tokens.every(token=>aliases.has(token));
  }):[];
  if(matching.length===1){
    const indices=used.flatMap((occupied,i)=>!occupied&&!/\s/.test(text[i])?[i]:[]);
    patch.region=matching[0].lawd_code;chips.push({field:'region',label:matching[0].name,start:indices[0],end:indices[indices.length-1]+1});
  }else if(matching.length>1)unresolved.push('지역을 선택해 주세요.');
  else if(residual&&chips.length)unresolved.push(`확인할 조건: ${residual}`);
  const active=chips.some(c=>c.field!=='region');
  return {patch,chips,unresolved,regions:matching,active,valid:active&&unresolved.length===0};
}

/** Budget-only rent queries mean deposit, never a guessed monthly payment. */
export function propertyQueryIssues(query:PropertyQuery,current:Pick<PropertyViewState,'trade'|'rentKind'>):string[]{
  const monthly=(query.patch.trade??current.trade)==='rent'&&(query.patch.rentKind??current.rentKind)!=='jeonse';
  return [...query.unresolved,...(monthly&&query.chips.some(c=>c.field==='budget')?['월세·전체 임대차 예산은 보증금과 월세를 상세조건에서 나눠 선택해 주세요.']:[])];
}
