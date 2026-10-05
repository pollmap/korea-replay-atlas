import json
import tempfile
import unittest
from pathlib import Path
from pipeline.property_search_index import build
from pipeline.real_estate import canonical_bytes,sha256,RealEstateError
from pipeline.real_estate_scope import FOCUS_RANKS

class SearchIndex(unittest.TestCase):
 def fixture(self,root,mutate=None):
  release='property-1111111111111111';files=[]
  def asset(path,value):
   payload=canonical_bytes(value);p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(payload)
   files.append({'path':path,'sha256':sha256(payload),'byte_length':len(payload)})
   return {'url':'/'+path,'sha256':sha256(payload),'bytes':len(payload)}
  regions=[]
  for code in [*sorted(FOCUS_RANKS),'47230']:
   rows=[{'id':f'molit-apt:{code}:{code}-1','name':'테스트','lawd_code':code,'legal_dong_name':'중앙동','lot_number':'1','observed_name_variants':['테스트','이전명']}]
   if mutate:mutate(code,rows)
   complexes=asset(f'data/{code}-complex.json',{'release_id':release,'lawd_code':code,'complexes':rows})
   detail=asset(f'data/{code}-detail.json',{'release_id':release,'lawd_code':code,'complexes':complexes})
   regions.append({'lawd_code':code,'name':code+' 지역','index':detail})
  registry=asset('data/regions.json',{'release_id':release,'regions':regions})
  manifest=asset('data/release.json',{'release_id':release,'regions':registry})
  (root/'publication.json').write_bytes(canonical_bytes({'kind':'property-publication','release_id':release,'audit':{'all_transaction_ids_preserved':True},'property_release':manifest,'files':files}))
 def test_deterministic_complete_scope_without_external_acquisition(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);self.fixture(root);one,receipt=build(root);two,_=build(root)
   self.assertEqual(one,two);self.assertEqual(receipt['rows'],112);v=json.loads(one)
   self.assertNotIn('47230',{row[2] for row in v['rows']});self.assertEqual(v['audit']['source_calls'],0)
   self.assertEqual(v['rows'][0][5],['이전명']);self.assertEqual(receipt['sha256'],sha256(one))
 def test_rejects_hash_corruption_before_publication(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);self.fixture(root);(root/'data/11710-complex.json').write_text('{}')
   with self.assertRaises(RealEstateError):build(root)
 def test_rejects_identity_and_duplicate_rows(self):
  for mutate in [lambda code,rows:rows.append(rows[0]),lambda code,rows:rows[0].update(lawd_code='47230')]:
   with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);self.fixture(root,mutate)
    with self.assertRaises(RealEstateError):build(root)
 def test_requires_verified_publication(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);self.fixture(root);p=root/'publication.json';v=json.loads(p.read_bytes());v['audit']['all_transaction_ids_preserved']=False;p.write_bytes(canonical_bytes(v))
   with self.assertRaises(RealEstateError):build(root)

if __name__=='__main__':unittest.main()
