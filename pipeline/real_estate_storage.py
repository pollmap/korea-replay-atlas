"""Lossless private snapshot storage; public JSON and original XML stay unchanged."""
from __future__ import annotations

import lzma
import re
import zlib

from .real_estate import RealEstateError,sha256

MAX_SNAPSHOT_BYTES=128*1024**2
XZ_MEMORY_LIMIT=64*1024**2


def encode_snapshot(payload):
    if not isinstance(payload,bytes) or not 0<len(payload)<=MAX_SNAPSHOT_BYTES:
        raise RealEstateError('snapshot_decoded_size_limit')
    # XZ has no filename or timestamp. Keep old stored JSON/gzip untouched;
    # decoded bytes and their identity are identical across storage encodings.
    stored=lzma.compress(payload,format=lzma.FORMAT_XZ,check=lzma.CHECK_CRC64,preset=3)
    return stored,{'encoding':'xz','decoded_bytes':len(payload),'decoded_sha256':sha256(payload)}


def decode_snapshot(payload,descriptor):
    if (not isinstance(payload,bytes) or not 0<len(payload)<=MAX_SNAPSHOT_BYTES
            or len(payload)!=descriptor.get('bytes') or sha256(payload)!=descriptor.get('sha256')):
        raise RealEstateError('snapshot_storage_hash_mismatch')
    if descriptor.get('encoding') is None:
        if str(descriptor.get('path','')).endswith(('.gz','.xz')):
            raise RealEstateError('snapshot_encoding_missing')
        return payload
    size=descriptor.get('decoded_bytes');digest=descriptor.get('decoded_sha256')
    encoding=descriptor.get('encoding')
    suffix={'gzip':'.json.gz','xz':'.json.xz'}.get(encoding) if isinstance(encoding,str) else None
    if (suffix is None or not isinstance(size,int) or isinstance(size,bool)
            or not 0<size<=MAX_SNAPSHOT_BYTES or not isinstance(digest,str)
            or not re.fullmatch('[a-f0-9]{64}',digest) or not str(descriptor.get('path','')).endswith(suffix)):
        raise RealEstateError('invalid_snapshot_encoding')
    try:
        if encoding=='xz':
            decoder=lzma.LZMADecompressor(format=lzma.FORMAT_XZ,memlimit=XZ_MEMORY_LIMIT)
            result=decoder.decompress(payload,max_length=size+1)
            unconsumed=not decoder.eof and not decoder.needs_input
        else:
            decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
            result=decoder.decompress(payload,size+1)
            unconsumed=decoder.unconsumed_tail
        if (len(result)!=size or unconsumed or decoder.unused_data or not decoder.eof
                or sha256(result)!=digest):
            raise RealEstateError('snapshot_decoded_hash_mismatch')
        return result
    except (zlib.error,lzma.LZMAError):
        raise RealEstateError('invalid_snapshot_'+encoding) from None
