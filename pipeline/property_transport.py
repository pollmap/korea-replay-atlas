"""Bounded explicit gzip file encoding; HTTP headers are not a data contract."""
import gzip
import re
import zlib
from .real_estate import RealEstateError, sha256

MAX_DECODED = 24 * 1024**2
def encode(raw):
    return gzip.compress(raw, compresslevel=6, mtime=0)

def decode(raw, descriptor):
    transport=descriptor.get('transport')
    if transport is None:return raw
    limit=transport.get('decoded_bytes');digest=transport.get('decoded_sha256')
    if (set(transport)!= {'encoding','decoded_bytes','decoded_sha256'} or transport['encoding']!='gzip'
            or type(limit) is not int or not 0<limit<=MAX_DECODED or not isinstance(digest,str)
            or not re.fullmatch(r'[a-f0-9]{64}',digest)):
        raise RealEstateError('publication_transport_invalid')
    try:
        stream=zlib.decompressobj(16+zlib.MAX_WBITS)
        result=stream.decompress(raw,limit+1)
        if len(result)>limit or stream.unconsumed_tail or not stream.eof or stream.unused_data:
            raise RealEstateError('publication_transport_size')
    except zlib.error:
        raise RealEstateError('publication_transport_corrupt') from None
    if len(result)!=limit or sha256(result)!=digest:raise RealEstateError('publication_decoded_hash')
    return result
