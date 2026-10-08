"""Append-only local property archive using the verified archive backup contract.

Objects and historical heads are immutable. A small compare-and-swap head file
selects the latest complete backup. Nothing here calls a source API or publishes
property data. Keep this store on a different physical device for device-loss
protection; a second directory on the same laptop only protects against a bad
working checkpoint or accidental deletion of one path.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from .real_estate import RealEstateError, canonical_bytes, sha256, _reject_links
from .real_estate_archive import backup, restore, MAX_FILE, encode_object, decode_object

HASH = re.compile(r'^[a-f0-9]{64}$')
HEAD = 'head.json'


def _descriptor(value):
    if (not isinstance(value, dict) or set(value) != {'sha256', 'bytes'}
            or not isinstance(value['sha256'], str) or not HASH.fullmatch(value['sha256'])
            or type(value['bytes']) is not int or not 0 < value['bytes'] <= MAX_FILE):
        raise RealEstateError('local_archive_descriptor')
    return value


@contextmanager
def _lock(path: Path):
    """Cross-process lock for head promotion; never silently steal a writer."""
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'\0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RealEstateError('local_archive_writer_active') from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_immutable(path: Path, body: bytes) -> None:
    _reject_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.stat().st_size != len(body) or sha256(path.read_bytes()) != sha256(body):
            raise RealEstateError('local_archive_object_changed')
        return
    scratch = path.parent / ('.archive-' + uuid.uuid4().hex + '.partial')
    try:
        with scratch.open('xb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(scratch, path)
        except FileExistsError:
            if path.stat().st_size != len(body) or sha256(path.read_bytes()) != sha256(body):
                raise RealEstateError('local_archive_object_changed') from None
    finally:
        if scratch.exists():
            scratch.unlink()


class LocalArchive:
    head_name = HEAD
    heads_directory = 'heads'
    lock_name = '.head.lock'

    def __init__(self, directory: Path, *, reserve_bytes: int = 0):
        if type(reserve_bytes) is not int or reserve_bytes < 0:
            raise RealEstateError('invalid_disk_reserve')
        self.root = Path(directory).absolute()
        _reject_links(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.reserve_bytes = reserve_bytes

    def _path(self, digest: str) -> Path:
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise RealEstateError('local_archive_descriptor')
        return self.root / 'objects' / digest[:2] / (digest + '.bin')

    def put(self, raw: bytes) -> dict:
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_FILE:
            raise RealEstateError('local_archive_object_size')
        descriptor = {'sha256': sha256(raw), 'bytes': len(raw)}
        path = self._path(descriptor['sha256'])
        encoded_path = path.with_suffix('.encoded')
        _reject_links(path); _reject_links(encoded_path)
        if path.exists() or encoded_path.exists():
            self.get(descriptor['sha256'], descriptor['bytes'])
            return descriptor
        encoded = encode_object(raw) if len(raw) >= 1024 else raw
        if len(encoded) < len(raw):
            path, body = encoded_path, encoded
        else:
            body = raw
        if shutil.disk_usage(self.root).free - len(body) < self.reserve_bytes:
            raise RealEstateError('disk_reserve')
        _write_immutable(path, body)
        self.get(descriptor['sha256'], descriptor['bytes'])
        return descriptor

    def get(self, digest: str, size: int) -> bytes:
        _descriptor({'sha256': digest, 'bytes': size})
        path = self._path(digest)
        _reject_links(path)
        try:
            if path.stat().st_size > MAX_FILE:
                raise RealEstateError('local_archive_object_changed')
            raw = path.read_bytes()
        except FileNotFoundError:
            encoded_path = path.with_suffix('.encoded'); _reject_links(encoded_path)
            if not 0 < encoded_path.stat().st_size <= MAX_FILE:
                raise RealEstateError('local_archive_object_changed')
            return decode_object(encoded_path.read_bytes(), digest, size)
        if len(raw) != size or sha256(raw) != digest:
            raise RealEstateError('local_archive_object_changed')
        return raw

    def compact(self, *, retire_raw=False, progress=None):
        """Verify each new encoding before optionally retiring its raw duplicate.

        Caller must hold the deployment's shared bulk-work lock. Deploy the dual
        reader to every consumer first; legacy-only images cannot be rolled back
        after retirement. Logical SHA, byte count and every head remain unchanged.
        """
        count = 0; before = 0; after = 0
        with _lock(self.root / '.compaction.lock'):
            for path in sorted((self.root / 'objects').glob('*/*.bin')):
                _reject_links(path)
                digest = path.stem
                if self._path(digest) != path or not 0 < path.stat().st_size <= MAX_FILE:
                    raise RealEstateError('local_archive_object_changed')
                raw = path.read_bytes()
                if sha256(raw) != digest:
                    raise RealEstateError('local_archive_object_changed')
                if len(raw) < 1024: continue
                target = path.with_suffix('.encoded'); _reject_links(target)
                if target.exists():
                    if not 0 < target.stat().st_size <= MAX_FILE:
                        raise RealEstateError('local_archive_object_changed')
                    encoded = target.read_bytes()
                else:
                    encoded = encode_object(raw)
                if len(encoded) >= len(raw): continue
                if not target.exists() and shutil.disk_usage(self.root).free - len(encoded) < self.reserve_bytes:
                    raise RealEstateError('disk_reserve')
                _write_immutable(target, encoded)
                if decode_object(target.read_bytes(), digest, len(raw)) != raw:
                    raise RealEstateError('local_archive_object_changed')
                if retire_raw:
                    # Shared lock excludes writers; reject path replacement too.
                    _reject_links(path)
                    if path.read_bytes() != raw:
                        raise RealEstateError('local_archive_object_changed')
                    path.unlink()
                count += 1; before += len(raw); after += len(encoded)
                if progress: progress({'objects': count, 'raw_bytes': before,
                    'encoded_bytes': after, 'retired': retire_raw})
        return {'objects': count, 'raw_bytes': before, 'encoded_bytes': after,
                'retired': retire_raw, 'source_calls': 0, 'head_changed': False}

    def head(self):
        path = self.root / self.head_name
        _reject_links(path)
        if not path.exists():
            return None
        value = _descriptor(json.loads(path.read_text(encoding='utf-8')))
        self.get(value['sha256'], value['bytes'])
        return value

    def promote(self, descriptor, expected, *, lease=None):
        if lease is not None:
            raise RealEstateError('local_archive_lease_unsupported')
        descriptor = _descriptor(descriptor)
        if expected is not None:
            expected = _descriptor(expected)
        self.get(descriptor['sha256'], descriptor['bytes'])
        with _lock(self.root / self.lock_name):
            if self.head() != expected:
                raise RealEstateError('archive_head_changed')
            body = canonical_bytes(descriptor)
            _write_immutable(self.root / self.heads_directory / (descriptor['sha256'] + '.json'), body)
            scratch = self.root / ('.head-' + uuid.uuid4().hex + '.partial')
            try:
                with scratch.open('xb') as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(scratch, self.root / self.head_name)
            finally:
                if scratch.exists():
                    scratch.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['backup', 'restore', 'status', 'plan-set', 'backup-set', 'restore-set'])
    parser.add_argument('--store', required=True, type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--reserve-gib', type=int, default=0)
    args = parser.parse_args()
    try:
        store = LocalArchive(args.store, reserve_bytes=args.reserve_gib * 1024**3)
        if args.command == 'status':
            from .real_estate_local_archive_set import LocalArchiveSet
            result = {'head': store.head(), 'set_head': LocalArchiveSet(
                args.store, reserve_bytes=store.reserve_bytes).head()}
        elif args.root is None:
            raise RealEstateError('archive_root_required')
        elif args.command in ('plan-set', 'backup-set', 'restore-set'):
            from .real_estate_local_archive_set import LocalArchiveSet, backup_set, restore_set, plan_set
            grouped = LocalArchiveSet(args.store, reserve_bytes=store.reserve_bytes)
            operation = {'plan-set': plan_set, 'backup-set': backup_set, 'restore-set': restore_set}[args.command]
            result = operation(args.root, grouped)
        elif args.command == 'backup':
            result = backup(args.root, store)
        else:
            result = restore(args.root, store)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(1, 'real_estate_local_archive: ' +
                    (error.code if isinstance(error, RealEstateError) else 'invalid_input') + '\n')


if __name__ == '__main__':
    main()
