"""Small, source-pinned priority-nine apartment name/address search index.
No source calls, position inference or other-region acquisition."""
from pathlib import Path
import json
from .real_estate import RealEstateError,canonical_bytes,sha256,_reject_links
from .real_estate_publish import checked_read
from .real_estate_scope import FOCUS_RANKS

def build(publication_root):
 root=Path(publication_root).absolute();_reject_links(root)
 pub=json.loads((root/'publication.json').read_bytes())
 if pub.get('kind')!='property-publication' or not pub.get('audit',{}).get('all_transaction_ids_preserved'):raise RealEstateError('search_publication_unverified')
 declared={f['path']:f for f in pub['files']}
 def read(ref):
  path=ref.get('path',ref.get('url','').lstrip('/'));entry=declared.get(path)
  if not entry or ref['sha256']!=entry['sha256']:raise RealEstateError('search_undeclared_asset')
  return json.loads(checked_read(root,{'path':path,'sha256':entry['sha256'],'bytes':entry['byte_length']},24*1024**2))
 manifest=read(pub['property_release']);release=pub['release_id'];regions=read(manifest['regions'])
 if manifest['release_id']!=release or regions['release_id']!=release:raise RealEstateError('search_release_mismatch')
 codes=set();ids=set();rows=[];names=[];inputs=[]
 for region in regions['regions']:
  code=region['lawd_code']
  if code not in FOCUS_RANKS:continue
  if code in codes:raise RealEstateError('search_duplicate_region')
  codes.add(code);names.append([code,region['name']]);detail=read(region['index'])
  if detail['release_id']!=release or detail['lawd_code']!=code:raise RealEstateError('search_region_mismatch')
  if not detail['complexes']:continue
  ref=detail['complexes'];complexes=read(ref)
  if complexes['release_id']!=release or complexes['lawd_code']!=code:raise RealEstateError('search_complex_version')
  inputs.append({'code':code,'sha256':ref['sha256']})
  for c in complexes['complexes']:
   if c['id'] in ids or c['lawd_code']!=code or not c['id'].startswith('molit-apt:'+code+':'):raise RealEstateError('search_complex_identity')
   ids.add(c['id']);aliases=sorted(set(c['observed_name_variants'])-{c['name']})
   rows.append([c['id'],c['name'],code,c['legal_dong_name'],c['lot_number'],aliases])
 if codes!=set(FOCUS_RANKS):raise RealEstateError('search_scope_incomplete')
 value={'schema_version':1,'kind':'property-complex-search-index','property_release_id':release,'scope':'priority-nine','regions':names,'rows':sorted(rows),'inputs':inputs,'audit':{'complexes':len(rows),'regions':len(codes),'source_calls':0,'position_inference':False}}
 payload=canonical_bytes(value)
 if len(payload)>8*1024**2:raise RealEstateError('search_index_budget')
 return payload,{'sha256':sha256(payload),'bytes':len(payload),'rows':len(rows),'regions':len(codes)}
