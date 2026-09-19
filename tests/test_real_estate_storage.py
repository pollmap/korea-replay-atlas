import gzip

import pytest

from pipeline.real_estate import RealEstateError,sha256
from pipeline.real_estate_storage import encode_snapshot,decode_snapshot,MAX_SNAPSHOT_BYTES


def descriptor(blob,extra=None):
    return {'path':'snapshots/rent/11110/202609/'+sha256(blob)+('.json.gz' if extra else '.json'),
            'sha256':sha256(blob),'bytes':len(blob),**(extra or {})}


def test_deterministic_compression_preserves_exact_original_bytes_and_identity_compatibility():
    raw=('완전한 원본 정규화 자료\n'*300).encode();blob,extra=encode_snapshot(raw)
    assert encode_snapshot(raw)==(blob,extra)
    assert len(blob)<len(raw)//10 and blob[4:8]==b'\0\0\0\0' and blob[9]==255
    assert decode_snapshot(blob,descriptor(blob,extra))==raw
    assert decode_snapshot(raw,descriptor(raw))==raw


@pytest.mark.parametrize('alteration',['stored_hash','decoded_hash','decoded_size','truncated','trailing','double_member'])
def test_storage_and_decoded_hashes_gzip_crc_and_single_member_are_all_required(alteration):
    raw=b'original-data\n'*400;blob,extra=encode_snapshot(raw)
    if alteration=='truncated':blob=blob[:-3]
    if alteration=='trailing':blob+=b'x'
    if alteration=='double_member':blob+=blob
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
