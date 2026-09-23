import type {Asset} from './contracts';

/** Operator website display colours, sampled from the linked line-menu symbols
 * on 2026-09-23. These are not claimed to be a universal printed-signage palette.
 * https://smss.seoulmetro.co.kr/traininfo/traininfoUserView.do
 * https://smss.seoulmetro.co.kr/images/train/user/menu_line_{1..8}_n.png
 */
export const SEOUL_METRO_DISPLAY_COLORS:Readonly<Record<string,string>>=Object.freeze({
  '1':'#051d86','2':'#2fae35','3':'#ff6000','4':'#1a97dd',
  '5':'#822fe1','6':'#ae4908','7':'#636b10','8':'#e6265b',
});
type RailAsset=Pick<Asset,'source_id'|'layer'>;
type Properties=Record<string,unknown>;
const recordLine=(value:unknown)=>typeof value==='string'?/^([1-8]):\d+$/.exec(value)?.[1]:undefined;

/** A nationwide bare "1호선" is ambiguous. Only an explicit Seoul network name
 * or line identifiers belonging to the Seoul depth source select this palette.
 * Conflicting endpoint/feature identities stay neutral instead of guessing.
 */
export function railDisplayColor(properties:Properties,asset:RailAsset,featureId?:string):string {
  const neutral=asset.layer==='depth'?'#64748b':'#a79571';
  if(asset.source_id==='seoul-depth'){
    const identities:string[]=[];
    if(properties.line!==undefined){
      const line=String(properties.line);if(!/^[1-8]$/.test(line))return neutral;
      identities.push(line);
    }
    const sourceIds=properties.source_station_ids;
    if(Array.isArray(sourceIds)){
      for(const id of sourceIds){const line=recordLine(id);if(!line)return neutral;identities.push(line);}
    }
    const provenance=properties.provenance;
    if(provenance&&typeof provenance==='object'&&!Array.isArray(provenance)){
      const line=recordLine((provenance as Properties).source_record_id);if(line)identities.push(line);
    }
    const featureLine=recordLine(featureId);if(featureLine)identities.push(featureLine);
    if(identities.length&&identities.every(line=>line===identities[0]))return SEOUL_METRO_DISPLAY_COLORS[identities[0]];
    return neutral;
  }
  const name=typeof properties.name==='string'?properties.name.trim():'';
  const match=/^(?:서울 지하철|수도권 전철) ([1-8])호선$/.exec(name);
  return match?SEOUL_METRO_DISPLAY_COLORS[match[1]]:neutral;
}
