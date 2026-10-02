import {describe,it,expect} from 'vitest';
import {workspaceSearch} from '../src/property-workspace-search';
const regions=[{lawd_code:'11710',name:'서울특별시 송파구'}];
const complexes=[{id:'apt-1',name:'헬리오시티',legal_dong_name:'가락동',lot_number:'913'}];
const view={query:'헬리오',propertyType:'apartment' as const,region:'11710',dataRegion:'11710',loading:false};
describe('workspace search data scope',()=>{
 it('returns confirmed selected-region apartment results',()=>expect(workspaceSearch(regions,complexes,view).complexes).toEqual(complexes));
 it('does not relabel apartment records as officetels',()=>expect(workspaceSearch(regions,complexes,{...view,propertyType:'officetel'}).complexes).toEqual([]));
 it('never serves old-region records while a new region loads',()=>{
  expect(workspaceSearch(regions,complexes,{...view,loading:true}).complexes).toEqual([]);
  expect(workspaceSearch(regions,complexes,{...view,region:'11680'}).complexes).toEqual([]);
 });
 it('allows region navigation without an officetel dataset',()=>expect(workspaceSearch(regions,complexes,{...view,propertyType:'officetel',query:'송파'})).toEqual({regions,complexes:[]}));
 it('normalizes Korean text and supports addresses without treating them as coordinates',()=>{
  expect(workspaceSearch(regions,complexes,{...view,query:'가락동'.normalize('NFD')}).complexes).toEqual(complexes);
  expect(workspaceSearch(regions,complexes,{...view,query:'913'}).complexes).toEqual(complexes);
 });
});
