"""Lossless private snapshot storage; public JSON and original XML stay unchanged."""
from __future__ import annotations

import gzip
import io
import re
import zlib

from .real_estate import RealEstateError,sha256

MAX_SNAPSHOT_BYTES=128*1024**2


def encode_snapshot(payload):
    if not isinstance(payload,bytes) or not 0<len(payload)<=MAX_SNAPSHOT_BYTES:
        raise RealEstateError('snapshot_decoded_size_limit')
    buffer=io.BytesIO()
    # GzipFile omits names and fixes OS=255; mtime=0 makes bytes reproducible.
    with gzip.GzipFile(filename='',fileobj=buffer,mode='wb',compresslevel=6,mtime=0) as stream:
        stream.write(payload)
    return buffer.getvalue(),{'encoding':'gzip','decoded_bytes':len(payload),'decoded_sha256':sha256(payload)}


def decode_snapshot(payload,descriptor):
    if (not isinstance(payload,bytes) or not 0<len(payload)<=MAX_SNAPSHOT_BYTES
            or len(payload)!=descriptor.get('bytes') or sha256(payload)!=descriptor.get('sha256')):
        raise RealEstateError('snapshot_storage_hash_mismatch')
    if descriptor.get('encoding') is None:
        if str(descriptor.get('path','')).endswith('.gz'):
            raise RealEstateError('snapshot_encoding_missing')
        return payload
    size=descriptor.get('decoded_bytes');digest=descriptor.get('decoded_sha256')
    if (descriptor.get('encoding')!='gzip' or not isinstance(size,int) or isinstance(size,bool)
            or not 0<size<=MAX_SNAPSHOT_BYTES or not isinstance(digest,str)
            or not re.fullmatch('[a-f0-9]{64}',digest) or not str(descriptor.get('path','')).endswith('.json.gz')):
        raise RealEstateError('invalid_snapshot_encoding')
    try:
        decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
        result=decoder.decompress(payload,size+1)
        if (len(result)!=size or decoder.unconsumed_tail or decoder.unused_data or not decoder.eof
                or sha256(result)!=digest):
            raise RealEstateError('snapshot_decoded_hash_mismatch')
        return result
    except zlib.error:
        raise RealEstateError('invalid_snapshot_gzip') from None
