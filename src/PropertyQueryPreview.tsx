import type {PropertyQuery} from '../shared/property-query';

export default function PropertyQueryPreview({query,issues,onApply,onEdit,onRegion}:{query:PropertyQuery;issues:string[];onApply:()=>void;onEdit:(start:number,end:number)=>void;onRegion:(name:string)=>void}){
  return <div className="property-query-preview" role="region" aria-label="검색 조건 확인">
    <div className="property-query-chips">{query.chips.map((chip,i)=><button key={`${chip.field}:${i}`} onClick={()=>onEdit(chip.start,chip.end)} aria-label={`${chip.label} 수정`}>{chip.label}</button>)}</div>
    {issues.map((issue,i)=><p key={i} role="status">{issue}</p>)}
    {query.regions.length>1&&<div className="property-query-regions">{query.regions.map(row=><button key={row.lawd_code} onClick={()=>onRegion(row.name)}>{row.name}</button>)}</div>}
    <button className="property-query-apply" disabled={!query.valid||issues.length>0} onClick={onApply}>조건 적용</button>
  </div>;
}
