"""Versioned byte-verification receipts for immutable local CAS objects.

Only a previous successful store.get can produce a receipt. A missing/replaced/
resized/touched object or decoder change requires full byte verification again.
Full manual audit and restore do not consult this cache.
"""
from contextlib import contextmanager, closing
from functools import lru_cache
import hashlib
from pathlib import Path
import sqlite3

from .real_estate import RealEstateError, canonical_bytes, _reject_links

CACHE = '.object-verification.sqlite'


@lru_cache(maxsize=1)
def validator_version():
    digest = hashlib.sha256(b'local-cas-byte-verification-v1')
    for name in ('real_estate_archive.py','real_estate_local_archive.py','real_estate_manifest.py','real_estate_archive_verification.py'):
        digest.update(name.encode());digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def _identity(store, digest, size):
    path = store._path(digest);_reject_links(path)
    encoded = False
    if not path.exists():
        path = path.with_suffix('.encoded');_reject_links(path);encoded = True
    if not path.is_file():
        raise RealEstateError('archive_object_missing')
    value = path.stat()
    if value.st_size <= 0 or (not encoded and value.st_size != size):
        raise RealEstateError('archive_object_size')
    return canonical_bytes([validator_version(), encoded, value.st_dev, value.st_ino,
                            value.st_size, value.st_mtime_ns, value.st_ctime_ns]).decode()


@contextmanager
def verification(store, enabled):
    if not enabled:
        yield lambda digest, size: store.get(digest, size)
        return
    path = store.root / CACHE;_reject_links(path)
    for suffix in ('-journal','-wal','-shm'):_reject_links(Path(str(path)+suffix))
    with closing(sqlite3.connect(path,timeout=5)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS receipts (sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,identity TEXT NOT NULL,PRIMARY KEY(sha256,bytes))')
        db.commit()
        pending = 0
        def check(digest, size):
            nonlocal pending
            stamp = _identity(store,digest,size)
            old = db.execute('SELECT identity FROM receipts WHERE sha256=? AND bytes=?',(digest,size)).fetchone()
            if old and old[0] == stamp:
                return
            store.get(digest,size) # exact decompression, original length and SHA
            if _identity(store,digest,size) != stamp:
                raise RealEstateError('archive_object_changed_during_verification')
            db.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?)',(digest,size,stamp))
            pending += 1
            if pending >= 100:
                db.commit(); pending = 0  # restart does not discard prior verified chunks
        with db:
            yield check
