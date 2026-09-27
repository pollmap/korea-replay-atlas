import gzip
import lzma

import pytest

from pipeline.real_estate import RealEstateError,sha256
from pipeline.real_estate_storage import encode_snapshot,decode_snapshot,MAX_SNAPSHOT_BYTES


def descriptor(blob,extra=None):
    suffix={'gzip':'.gz','xz':'.xz'}.get((extra or {}).get('encoding'),'')
    return {'path':'snapshots/rent/11110/202609/'+sha256(blob)+'.json'+suffix,
            'sha256':sha256(blob),'bytes':len(blob),**(extra or {})}


def test_deterministic_compression_preserves_exact_original_bytes_and_identity_compatibility():
    raw=('완전한 원본 정규화 자료\n'*300).encode();blob,extra=encode_snapshot(raw)
    assert encode_snapshot(raw)==(blob,extra)
    assert len(blob)<len(raw)//10 and blob.startswith(b'\xfd7zXZ\0') and extra['encoding']=='xz'
    assert decode_snapshot(blob,descriptor(blob,extra))==raw
    assert decode_snapshot(raw,descriptor(raw))==raw


@pytest.mark.parametrize('alteration',['stored_hash','decoded_hash','decoded_size','truncated','trailing','double_member','checksum'])
@pytest.mark.parametrize('encoding',['gzip','xz'])
def test_storage_and_decoded_hashes_crc_and_single_member_are_all_required(alteration,encoding):
    raw=b'original-data\n'*400;blob,extra=encode_snapshot(raw)
    if encoding=='gzip':
        blob=gzip.compress(raw,mtime=0);extra={**extra,'encoding':'gzip'}
    if alteration=='truncated':blob=blob[:-3]
    if alteration=='trailing':blob+=b'x'
    if alteration=='double_member':blob+=blob
    if alteration=='checksum':blob=blob[:-5]+bytes([blob[-5]^1])+blob[-4:]
    ref=descriptor(blob,extra)
    if alteration=='stored_hash':ref['sha256']='0'*64
    if alteration=='decoded_hash':ref['decoded_sha256']='0'*64
    if alteration=='decoded_size':ref['decoded_bytes']-=1
    with pytest.raises(RealEstateError):decode_snapshot(blob,ref)


def test_expansion_bound_is_checked_before_allocating_the_claimed_size():
    blob=gzip.compress(b'X'*100_000,mtime=0)
    ref=descriptor(blob,{'encoding':'gzip','decoded_bytes':10,'decoded_sha256':'0'*64})
    with pytest.raises(RealEstateError,match='snapshot_decoded_hash_mismatch'):decode_snapshot(blob,ref)
    ref['decoded_bytes']=MAX_SNAPSHOT_BYTES+1
    with pytest.raises(RealEstateError,match='invalid_snapshot_encoding'):decode_snapshot(blob,ref)


def test_legacy_gzip_exact_bytes_are_decoded_without_rewriting():
    raw=b'{"records":[],"source":"original"}'
    for stamp in [0,1720000000]:
        blob=gzip.compress(raw,mtime=stamp)
        ref=descriptor(blob,{'encoding':'gzip','decoded_bytes':len(raw),'decoded_sha256':sha256(raw)})
        assert decode_snapshot(blob,ref)==raw
        assert ref['sha256']==sha256(blob)


def test_xz_expansion_and_dictionary_memory_are_bounded():
    raw=b'X'*100_000
    blob,extra=encode_snapshot(raw);ref=descriptor(blob,{**extra,'decoded_bytes':10})
    with pytest.raises(RealEstateError,match='snapshot_decoded_hash_mismatch'):decode_snapshot(blob,ref)
    # An XZ header can request a huge dictionary even for tiny decoded data.
    blob=lzma.compress(b'hello',filters=[{'id':lzma.FILTER_LZMA2,'dict_size':128*1024**2}])
    ref=descriptor(blob,{'encoding':'xz','decoded_bytes':5,'decoded_sha256':sha256(b'hello')})
    with pytest.raises(RealEstateError,match='invalid_snapshot_xz'):decode_snapshot(blob,ref)


@pytest.mark.parametrize('change',['missing_encoding','wrong_suffix','list_encoding','oversized','bool_size'])
def test_xz_descriptor_rejects_ambiguous_or_unbounded_encoding(change):
    blob,extra=encode_snapshot(b'original');ref=descriptor(blob,extra)
    if change=='missing_encoding':ref.pop('encoding')
    if change=='wrong_suffix':ref['path']=ref['path'].replace('.xz','.gz')
    if change=='list_encoding':ref['encoding']=[]
    if change=='oversized':ref['decoded_bytes']=MAX_SNAPSHOT_BYTES+1
    if change=='bool_size':ref['decoded_bytes']=True
    with pytest.raises(RealEstateError):decode_snapshot(blob,ref)
