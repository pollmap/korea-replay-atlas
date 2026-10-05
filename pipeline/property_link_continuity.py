"""Carry existing official address joins only when their published identity remains stable.

This does not verify coordinates or establish a new name-only join.
"""
import argparse
import json
from pathlib import Path
from .real_estate import RealEstateError,canonical_bytes,sha256
from .property_continuity import _source

IDENTITY_FIELDS=('source_complex_id','lawd_code','legal_dong_code','legal_dong_name','lot_number')
def stable_identity(before,after):
    return (before.get('id')==after.get('id') and not before.get('address_conflict')
        and not after.get('address_conflict')
        and all(before.get(k)==after.get(k) for k in IDENTITY_FIELDS)
        and bool(before.get('lot_number')) and bool(before.get('legal_dong_name'))
        and before.get('name') in set([after.get('name'),*after.get('observed_name_variants',[])]))

def build(previous,candidate,points,expected_sha):
    body=Path(points).read_bytes()
    if sha256(body)!=expected_sha:raise RealEstateError('prior_points_hash')
    source=json.loads(body)
    old,old_hash,old_read,old_regions=_source(previous)
    new,new_hash,new_read,new_regions=_source(candidate)
    if source.get('metadata',{}).get('property_release_id')!=old['release_id']:
        raise RealEstateError('prior_points_release')
    def complexes(read,regions):
        result={}
        for code,region in regions.items():
            if not code.startswith('11'):continue
            index=read(region['index']['url'])
            if not index.get('complexes'):continue
            value=read(index['complexes']['url'])
            for row in value['complexes']:
                if row['id'] in result:raise RealEstateError('duplicate_complex_identity')
                result[row['id']]=row
        return result
    before=complexes(old_read,old_regions);after=complexes(new_read,new_regions)
    retained=0;rejected=0
    for feature in source['features']:
        props=feature['properties'];identity=props.get('property_complex_id')
        if not identity:continue
        if props.get('property_aptseq_join')!='unique_official_road_address_and_name' or props.get('property_release_id')!=old['release_id']:
            raise RealEstateError('prior_join_method')
        # Never carry stale prices. New summaries provide prices for the selected conditions.
        for key in list(props):
            if key.startswith('recent_sale_'):del props[key]
        if identity not in before or identity not in after or not stable_identity(before[identity],after[identity]):
            for key in ('property_complex_id','property_release_id','property_aptseq_join'):props.pop(key,None)
            rejected+=1;continue
        props['property_release_id']=new['release_id'];retained+=1
    source['metadata']['property_release_id']=new['release_id']
    source['metadata']['identity_continuity']={'prior_points_sha256':expected_sha,
        'previous_publication_sha256':old_hash,'candidate_publication_sha256':new_hash,
        'rule':'existing_official_address_join_same_source_id_and_legal_address',
        'retained':retained,'rejected':rejected,'coordinate_verification':'not_performed'}
    return source

def main():
    p=argparse.ArgumentParser();p.add_argument('--previous',type=Path,required=True);p.add_argument('--candidate',type=Path,required=True)
    p.add_argument('--points',type=Path,required=True);p.add_argument('--prior-point-sha256',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=build(a.previous,a.candidate,a.points,a.prior_point_sha256);body=canonical_bytes(result)
    if a.output.exists() and a.output.read_bytes()!=body:raise RealEstateError('immutable_output_changed')
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(body)
    print(json.dumps({'sha256':sha256(body),'bytes':len(body),**result['metadata']['identity_continuity']}))
if __name__=='__main__':main()
