import json
import sqlite3
from unittest.mock import Mock
import pytest
from pipeline.real_estate import RealEstateError,sha256
from pipeline.real_estate_publish import publish,checked_read,verify_snapshot
from pipeline.real_estate_complex_summary import build
from pipeline.property_verification_cache import VerificationCache
from pipeline.property_transport import encode,decode
from test_real_estate_publish import setup
from test_real_estate_fetch import registry

def test_compressed_publication_preserves_rows_and_supports_summary(tmp_path):
    root=setup(tmp_path);result=publish(root,registry(),tmp_path/'out',reserve_bytes=0,packed_transactions=True,compressed_transactions=True)
    base=tmp_path/'out'/result['release_id'];compressed=[f for f in result['files'] if 'transport' in f]
    assert compressed and result['audit']['source_rows']==4
    for f in compressed:
        raw=(base/f['path']).read_bytes();assert sha256(raw)==f['sha256']
        decoded=checked_read(base,{**f,'bytes':f['byte_length']},24*1024**2)
        assert len(decoded)==f['transport']['decoded_bytes']
        rows=json.loads(decoded)['months'];assert sum(len(m['transactions']) for m in rows)==4
    summary=build(base/'publication.json',tmp_path/'summary',reserve_bytes=0)
    assert summary['audit']['source_rows']==4
    assert publish(root,registry(),tmp_path/'out',reserve_bytes=0,packed_transactions=True,compressed_transactions=True)==result

def test_verified_snapshot_cache_skips_only_identical_inputs(tmp_path):
    root=setup(tmp_path);db=sqlite3.connect(root/'checkpoint.sqlite');db.row_factory=sqlite3.Row;job=dict(db.execute("select * from jobs where status='complete' limit 1").fetchone());db.close()
    cache=VerificationCache(tmp_path/'audit.sqlite',root);verifier=Mock(side_effect=verify_snapshot)
    assert cache.verify(root,job,verifier)==cache.verify(root,job,verifier);assert verifier.call_count==1 and cache.hits==1
    changed={**job,'updated_at':'2026-09-30T00:00:00Z'};cache.verify(root,changed,verifier);assert verifier.call_count==2
    with pytest.raises(RealEstateError,match='verification_cache_root'):cache.verify(tmp_path,job,verifier)
    cache.close()

def test_transport_rejects_bounds_truncation_and_wrong_decoded_hash():
    raw=b'a'*10000;body=encode(raw);descriptor={'transport':{'encoding':'gzip','decoded_bytes':len(raw),'decoded_sha256':sha256(raw)}}
    assert decode(body,descriptor)==raw
    with pytest.raises(RealEstateError):decode(body[:-8],descriptor)
    with pytest.raises(RealEstateError):decode(body,{'transport':{**descriptor['transport'],'decoded_bytes':10}})
    with pytest.raises(RealEstateError):decode(body,{'transport':{**descriptor['transport'],'decoded_sha256':'0'*64}})
