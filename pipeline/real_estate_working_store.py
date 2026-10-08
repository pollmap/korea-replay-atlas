"""Read collector files from plaintext or existing verified local archive packs.

The ledger keeps its original paths, SHA and encoded snapshot descriptors. The
small index is a local lookup, never an alternative source of transaction truth.
Only raw XML and snapshot files can be retired. Legacy backups still restore to
plaintext, so recovery never depends on an installation-specific index path.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from contextlib import closing
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import time
import threading
from types import SimpleNamespace

from .real_estate import RealEstateError, canonical_bytes, sha256, _reject_links

INDEX = '.working-store.sqlite'
SCHEMA = '1'
MAX_BYTES = 256 * 1024**2
ARCHIVE_ENV = 'KOREA_REPLAY_WORKING_ARCHIVE'
PACK_CACHE_BYTES = 32 * 1024**2
_PACK_CACHE = OrderedDict()
_PACK_LOCK = threading.Lock()


def _path(root, name):
    root = Path(root).absolute(); _reject_links(root)
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or str(PurePosixPath(name)) != name or PurePosixPath(name).is_absolute()
            or any(part in ('', '.', '..') for part in PurePosixPath(name).parts)):
        raise RealEstateError('working_store_path')
    path = root / name; _reject_links(path)
    if not path.resolve().is_relative_to(root.resolve()):
        raise RealEstateError('working_store_path')
    return root, path


def _index(root):
    path = Path(root).absolute() / INDEX; _reject_links(path)
    return path


def _open_index(root):
    path = _index(root)
    if not path.exists():
        return None
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        if db.execute("SELECT value FROM meta WHERE key='schema'").fetchone() != (SCHEMA,):
            raise RealEstateError('working_store_schema')
        return db
    except BaseException:
        db.close(); raise


def _row(root, name):
    db = _open_index(root)
    if db is None:
        return None, None
    with closing(db):
        item = db.execute('SELECT descriptor FROM files WHERE path=?', (name,)).fetchone()
        if item is None:
            return None, None
        location = db.execute("SELECT value FROM meta WHERE key='archive_root'").fetchone()
        if location is None:
            raise RealEstateError('working_store_archive_missing')
        row = _validated_row(json.loads(item[0]))
        if row['path'] != name:
            raise RealEstateError('working_store_path')
        return row, location[0]


def _validated_row(row):
    from .real_estate_local_archive_set import _row as validate
    row = validate(row)
    if row['path'].split('/')[0] not in ('raw', 'snapshots') or 'segments' in row:
        raise RealEstateError('working_store_not_retirable')
    return row


def _store(location):
    from .real_estate_local_archive import LocalArchive
    root = Path(os.environ.get(ARCHIVE_ENV) or location).absolute(); _reject_links(root)
    # Reads must not create an empty archive at a stale/mistyped deployment path.
    if not root.is_dir() or not (root / 'objects').is_dir():
        raise RealEstateError('working_store_archive_missing')
    return LocalArchive(root)


def _read_pack(store, digest, size):
    """Bound memory and avoid decoding an 8 MiB pack for every tiny XML in it.

    CAS objects are immutable. File identity is still part of the cache key so
    replaced/corrupt/missing storage cannot be hidden by a previous good read.
    """
    path = store._path(digest); _reject_links(path)
    if not path.exists():
        path = path.with_suffix('.encoded'); _reject_links(path)
    stamp = path.stat()
    key = (str(path), digest, size, stamp.st_ino, stamp.st_size, stamp.st_mtime_ns)
    with _PACK_LOCK:
        if key in _PACK_CACHE:
            _PACK_CACHE.move_to_end(key)
            return _PACK_CACHE[key]
        payload = store.get(digest, size)
        if len(payload) <= PACK_CACHE_BYTES:
            while _PACK_CACHE and (len(_PACK_CACHE) >= 16 or
                    sum(map(len, _PACK_CACHE.values())) + len(payload) > PACK_CACHE_BYTES):
                _PACK_CACHE.popitem(last=False)
            _PACK_CACHE[key] = payload
        return payload


def has_reference(root, name):
    root, path = _path(root, name)
    return path.is_file() or _row(root, name)[0] is not None


def read_reference(root, descriptor, limit=MAX_BYTES):
    """Return exact stored bytes; snapshot decompression remains with its caller."""
    root, path = _path(root, descriptor.get('path'))
    size, digest = descriptor.get('bytes'), descriptor.get('sha256')
    if (type(size) is not int or not 0 < size <= min(limit, MAX_BYTES)
            or not isinstance(digest, str) or len(digest) != 64
            or any(char not in '0123456789abcdef' for char in digest)):
        raise RealEstateError('working_store_descriptor')
    try:
        with path.open('rb') as stream:
            raw = stream.read(size + 1)
    except FileNotFoundError:
        row, location = _row(root, descriptor['path'])
        if row is None:
            raise RealEstateError('working_store_missing') from None
        if row['bytes'] != size or row['sha256'] != digest:
            raise RealEstateError('working_store_reference_mismatch')
        from .real_estate_manifest import read_file
        store = _store(location)
        raw = read_file(row, lambda digest, size: _read_pack(store, digest, size))
    if len(raw) != size:
        raise RealEstateError('checkpoint_size_mismatch')
    if sha256(raw) != digest:
        raise RealEstateError('checkpoint_hash_mismatch')
    return raw


class ArchivedFile:
    """Minimal Path-like read view used by both existing backup formats."""
    def __init__(self, root, row):
        self.root, self.row = Path(root), _validated_row(row)

    def stat(self):
        return SimpleNamespace(st_size=self.row['bytes'])

    def read_bytes(self):
        return read_reference(self.root, self.row)


def append_archived_paths(root, paths):
    """Include retired logical files without adding the machine-local index."""
    db = _open_index(root)
    if db is None:
        return paths
    names = {name for _, name in paths}
    with closing(db):
        for name, encoded in db.execute("SELECT path,descriptor FROM files ORDER BY json_extract(descriptor, '$.object.sha256'),path"):
            row = _validated_row(json.loads(encoded))
            if row['path'] != name:
                raise RealEstateError('working_store_path')
            if name not in names:
                paths.append((ArchivedFile(root, row), name))
                names.add(name)
    return paths


def plan(root, store):
    """Read-only reference/size inventory, not proof that payload hashes pass."""
    from .real_estate_local_archive_set import load_set
    root = Path(root).absolute(); _reject_links(root)
    head = store.head()
    if head is None:
        raise RealEstateError('archive_no_backup')
    manifest = load_set(store, head)
    eligible = {row['path']: _validated_row(row) for row in manifest['files']
                if row['path'].split('/')[0] in ('raw', 'snapshots')}
    references = {}
    checkpoint = root / 'checkpoint.sqlite'; _reject_links(checkpoint)
    with closing(sqlite3.connect(checkpoint.as_uri() + '?mode=ro', uri=True)) as db:
        for pages, snapshot in db.execute('SELECT pages,snapshot FROM jobs'):
            refs = json.loads(pages) + ([json.loads(snapshot)] if snapshot else [])
            for ref in refs:
                references[ref['path']] = ref
        for (descriptor,) in db.execute('SELECT descriptor FROM snapshots'):
            ref = json.loads(descriptor); references[ref['path']] = ref
    matched = sum(1 for name, ref in references.items() if name in eligible
                  and ref['sha256'] == eligible[name]['sha256'] and ref['bytes'] == eligible[name]['bytes'])
    plain = plain_bytes = missing_plain = size_conflicts = 0
    for name, row in eligible.items():
        _, path = _path(root, name)
        if path.is_file():
            plain += 1; plain_bytes += path.stat().st_size
            size_conflicts += path.stat().st_size != row['bytes']
        else:
            missing_plain += 1
    index = _open_index(root)
    indexed = 0
    if index is not None:
        with closing(index):
            indexed = index.execute('SELECT COUNT(*) FROM files').fetchone()[0]
    return {'head': head, 'eligible_files': len(eligible),
            'eligible_logical_bytes': sum(row['bytes'] for row in eligible.values()),
            'plaintext_files': plain, 'plaintext_logical_bytes': plain_bytes,
            'missing_plaintext_files': missing_plain, 'plaintext_size_conflicts': size_conflicts,
            'indexed_files': indexed, 'ledger_references': len(references),
            'ledger_references_in_head': matched,
            'ledger_references_not_in_head': len(references) - matched,
            'payload_hashes_verified': False, 'source_calls': 0, 'writes': 0}


def _discard_candidate(root):
    candidate = _index(root).with_name(INDEX + '.next')
    # Only this command's fixed scratch names, under the exclusive writer lock.
    # Never remove a journal belonging to the current published index.
    for suffix in ('', '-journal', '-wal', '-shm'):
        path = Path(str(candidate) + suffix); _reject_links(path)
        if path.exists():
            path.unlink()
    return candidate


def _sync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _publish_index(root, store, pending, progress):
    """Never write the SQLite file mounted read-only by the API.

    Only a closed, journal-free candidate replaces that file. An unclean process
    death can leave a hot journal beside the candidate, never beside the current
    index. Readers that already opened the old inode can finish their reads.
    """
    candidate = _discard_candidate(root)
    current = _index(root)
    try:
        with closing(sqlite3.connect(candidate)) as index:
            source = _open_index(root)
            if source is not None:
                with closing(source):
                    source.backup(index)
            index.execute('PRAGMA journal_mode=DELETE')
            index.execute('PRAGMA synchronous=FULL')
            index.executescript("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);"
                                "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY,descriptor TEXT NOT NULL);")
            meta = dict(index.execute('SELECT key,value FROM meta'))
            if meta and (meta.get('schema') != SCHEMA or meta.get('archive_root') != str(store.root)):
                raise RealEstateError('working_store_configuration_changed')
            index.executemany('INSERT OR IGNORE INTO meta VALUES (?,?)',
                              [('schema', SCHEMA), ('archive_root', str(store.root))])
            for row, _, new in pending:
                if new:
                    index.execute('INSERT INTO files VALUES (?,?)',
                                  (row['path'], canonical_bytes(row).decode()))
            if progress:
                progress({'phase': 'index-prepared', 'files': len(pending)})
            index.commit()
            if index.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise RealEstateError('working_store_index_integrity')
        # All SQLite connections are closed before publication. The source of
        # replacement cannot need recovery on an API's read-only filesystem.
        if any(Path(str(candidate) + suffix).exists() for suffix in ('-journal', '-wal', '-shm')):
            raise RealEstateError('working_store_index_not_closed')
        with candidate.open('rb') as handle:
            os.fsync(handle.fileno())
        os.replace(candidate, current)
        _sync_directory(current.parent)
        if progress:
            progress({'phase': 'index-published', 'files': len(pending)})
    finally:
        # A SIGKILL deliberately bypasses this block; the next writer removes
        # only its abandoned candidate after reacquiring both project locks.
        _discard_candidate(root)


def migrate(root, store, *, limit=1000, retire_plaintext=False,
            readers_deployed=False, progress=None):
    """Bounded/resumable migration from an already verified backup-set head.

    No source calls, archive writes, snapshot changes or ledger changes. New
    mappings are published as one closed SQLite candidate before any unlink.
    """
    from .bulk_work import bulk_work
    from .real_estate_local_archive import _lock
    from .real_estate_local_archive_set import load_set
    from .real_estate_manifest import read_file
    if (type(limit) is not int or not 1 <= limit <= 10000
            or type(retire_plaintext) is not bool or type(readers_deployed) is not bool):
        raise RealEstateError('working_store_budget')
    if retire_plaintext and not readers_deployed:
        raise RealEstateError('working_store_readers_not_deployed')
    root = Path(root).absolute(); _reject_links(root)
    checkpoint = root / 'checkpoint.sqlite'; _reject_links(checkpoint)
    if not checkpoint.is_file():
        raise RealEstateError('working_store_checkpoint_missing')
    head = store.head()
    if head is None:
        raise RealEstateError('archive_no_backup')
    manifest = load_set(store, head)
    rows = sorted((row for row in manifest['files']
                   if row['path'].split('/')[0] in ('raw', 'snapshots')),
                  key=lambda row: (row['object']['sha256'], row['path']))
    indexed = retired = retired_bytes = verified = 0
    # One decoded pack at a time, never the full archive or a second plaintext tree.
    cached_key = cached_body = None
    def getter(digest, size):
        nonlocal cached_key, cached_body
        if (digest, size) != cached_key:
            cached_body = store.get(digest, size); cached_key = (digest, size)
        return cached_body
    with bulk_work(root.parent), _lock(root / '.working-store.lock'):
        with closing(sqlite3.connect(checkpoint, timeout=1)) as ledger:
            ledger.execute('BEGIN IMMEDIATE')
            if ledger.execute('SELECT 1 FROM lease WHERE expires>?', (time.time(),)).fetchone():
                raise RealEstateError('archive_collector_active')
            _discard_candidate(root)
            current = _open_index(root)
            pending = []
            try:
                if current is not None:
                    meta = dict(current.execute('SELECT key,value FROM meta'))
                    if meta.get('archive_root') != str(store.root):
                        raise RealEstateError('working_store_configuration_changed')
                for row in rows:
                    row = _validated_row(row)
                    _, path = _path(root, row['path'])
                    old = (current.execute('SELECT descriptor FROM files WHERE path=?',
                           (row['path'],)).fetchone() if current is not None else None)
                    if old:
                        # Keep prior backing packs even if a later backup repacks
                        # identical bytes; every earlier retired mapping survives.
                        prior = _validated_row(json.loads(old[0]))
                        if prior['sha256'] != row['sha256'] or prior['bytes'] != row['bytes']:
                            raise RealEstateError('working_store_reference_mismatch')
                        row = prior
                    if old and (not retire_plaintext or not path.exists()):
                        continue
                    if verified >= limit:
                        break
                    archived = read_file(row, getter)
                    if path.exists():
                        if read_reference(root, row) != archived:
                            raise RealEstateError('working_store_source_changed')
                    elif old is None:
                        raise RealEstateError('working_store_missing')
                    verified += 1
                    pending.append((row, path, old is None))
            finally:
                if current is not None:
                    current.close()
            indexed = sum(new for _, _, new in pending)
            if indexed:
                _publish_index(root, store, pending, progress)
            # No SQLite writer ever opens the published index. A kill here leaves
            # all mappings readable, including files retired by earlier batches.
            for row, path, _ in pending:
                if retire_plaintext and path.exists():
                    saved, location = _row(root, row['path'])
                    if saved != row or Path(location) != store.root:
                        raise RealEstateError('working_store_reference_mismatch')
                    archived = read_file(row, getter)
                    _reject_links(path)
                    if read_reference(root, row) != archived:
                        raise RealEstateError('working_store_source_changed')
                    path.unlink(); retired += 1; retired_bytes += row['bytes']
                if progress:
                    progress({'phase': 'retired' if retire_plaintext else 'verified',
                              'verified': verified, 'indexed': indexed,
                              'retired': retired, 'retired_logical_bytes': retired_bytes})
    return {'head': head, 'verified': verified, 'indexed': indexed, 'retired': retired,
            'retired_logical_bytes': retired_bytes, 'source_calls': 0,
            'ledger_changed': False, 'archive_objects_written': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=1000)
    parser.add_argument('--plan', action='store_true')
    parser.add_argument('--retire-plaintext', action='store_true')
    parser.add_argument('--readers-deployed', action='store_true')
    args = parser.parse_args()
    from .real_estate_local_archive_set import LocalArchiveSet
    try:
        if args.plan:
            if args.retire_plaintext or args.readers_deployed:
                raise RealEstateError('working_store_conflicting_mode')
            result = plan(args.root, LocalArchiveSet(args.store))
        else:
            result = migrate(args.root, LocalArchiveSet(args.store), limit=args.limit,
                             retire_plaintext=args.retire_plaintext,
                             readers_deployed=args.readers_deployed)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(1, 'working_store: ' +
                    (error.code if isinstance(error, RealEstateError) else 'invalid_input') + '\n')


if __name__ == '__main__':
    main()
