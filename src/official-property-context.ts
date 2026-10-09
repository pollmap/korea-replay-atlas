import {parseOfficialFees,parseOfficialSchools,officialSchoolsAround,type OfficialSchool,type OfficialFees} from '../shared/official-property-context';
import type {SurroundingsScope} from '../shared/property-surroundings';
import {fetchPinnedOfficialContextJson} from './atlas-client';
let schools:Promise<OfficialSchool[]>|undefined,fees:Promise<OfficialFees>|undefined;
export function loadOfficialSchools(center:{longitude:number;latitude:number},scope:SurroundingsScope){
  schools??=fetchPinnedOfficialContextJson(new URL('./data/official-schools-20261010.json',import.meta.url).href,{bytes:780751,sha256:'4254b2def52d08071a18f96f56feff02cdea78044706c6a37ece66099e35db1c'},new AbortController().signal).then(parseOfficialSchools).catch(error=>{schools=undefined;throw error;});
  return schools.then(rows=>officialSchoolsAround(rows,center,scope));
}
export function loadOfficialFees(){
  fees??=fetchPinnedOfficialContextJson(new URL('./data/official-fees-20261010.json',import.meta.url).href,{bytes:1277773,sha256:'b3acaf8a7f81412b5dd55c7f926fc05fd21624912aa77e3f1ba649ae878decd4'},new AbortController().signal).then(parseOfficialFees).catch(error=>{fees=undefined;throw error;});
  return fees;
}
