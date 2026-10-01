"""Verified, portable collector cutover. No source calls or automatic deletion.

Export requires an idle collector; its SQLite online backup and every immutable
file travel together. Import permits only a new destination, regular allowlisted
files, exact byte hashes, reference closure and an identical call ledger.
"""
from __future__ import annotations

import argparse
from contextlib import closing, nullcontext
import gzip
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import tempfile

from .real_estate import RealEstateError, canonical_bytes, _reject_links
from .real_estate_archive import (audit_checkpoint, checked_path, PREFIXES,
                                  MAX_FILE, MAX_FILES, HASH)
from .real_estate_local_archive_set import LocalArchiveSet, load_set, restore_set
from .real_estate_manifest import parts, validate_object

MAX_MANIFEST = 64 * 1024**2
MAX_TOTAL = 100 * 1024**3
RESERVE = 30 * 1024**3


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024**2), b''):
            h.update(data)
    return h.hexdigest()


def room(path, required, reserve):
    use = shutil.disk_usage(path)
    if use.free - required < reserve:
        raise RealEstateError('disk_reserve')


def export(root, output, *, reserve=RESERVE, progress=None):
    root = Path(root).absolute(); output = Path(output).absolute()
    _reject_links(root); _reject_links(output)
    if output.exists() or output.is_relative_to(root):
        raise RealEstateError('transfer_output_exists_or_nested')
    with tempfile.TemporaryDirectory(prefix='kr-cutover-', dir=output.parent) as temp:
        checkpoint = Path(temp) / 'checkpoint.sqlite'
        with closing(sqlite3.connect((root / 'checkpoint.sqlite').as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(checkpoint)) as target:
                source.backup(target)
        paths = [('checkpoint.sqlite', checkpoint)]
        for prefix in sorted(PREFIXES):
            folder = root / prefix
            _reject_links(folder)
            if folder.exists():
                # Each ancestor is checked once while walking from a verified
                # root. Rechecking all 10+ parents for every immutable page made
                # Windows cutover spend most of its time in metadata syscalls.
                for parent, dirs, files in os.walk(folder, followlinks=False):
                    for name in (*dirs, *files):
                        node = Path(parent) / name
                        if node.is_symlink() or hasattr(node, 'is_junction') and node.is_junction():
                            raise RealEstateError('linked_path')
                    for name in sorted(files):
                        path = Path(parent) / name
                        paths.append((checked_path(path.relative_to(root).as_posix()), path))
        rows = []
        for index, (name, path) in enumerate(paths):
            rows.append({'path': name, 'bytes': path.stat().st_size, 'sha256': digest(path)})
            if progress and (index + 1) % 1000 == 0:
                progress({'phase': 'source_sha', 'files': index + 1, 'total': len(paths)})
        # One byte pass, followed by closure against those verified descriptors.
        # The copied lease still rejects an active collector. Don't reread every
        # raw page twice before hashing the complete immutable inventory.
        audit = audit_checkpoint(root, checkpoint, descriptors={row['path']: row for row in rows})
        manifest = validate({'schema_version': 1, 'kind': 'korea-replay-cutover',
                             'files': rows, 'audit': audit})
        body = canonical_bytes(manifest)
        if len(body) > MAX_MANIFEST:
            raise RealEstateError('transfer_manifest_limit')
        room(output.parent, sum(row['bytes'] for row in rows) + len(body) + 1024**2, reserve)
        manifest_path = Path(temp) / 'manifest.json'
        manifest_path.write_bytes(body)
        # Exclusive creation; an interrupted bundle is preserved for inspection.
        with output.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=1, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode='w|', format=tarfile.USTAR_FORMAT) as archive:
                archive.add(manifest_path, arcname='manifest.json', recursive=False)
                for index, (name, path) in enumerate(paths):
                    archive.add(path, arcname=name, recursive=False)
                    if progress and (index + 1) % 1000 == 0:
                        progress({'phase': 'packing', 'files': index + 1, 'total': len(paths)})
        return {'files': len(rows), 'source_bytes': sum(row['bytes'] for row in rows),
                'archive_bytes': output.stat().st_size, 'archive_sha256': digest(output), 'audit': audit}


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'kind', 'files', 'audit'}
            or value['schema_version'] != 1 or value['kind'] != 'korea-replay-cutover'
            or not isinstance(value['files'], list) or not 1 <= len(value['files']) <= MAX_FILES
            or not isinstance(value['audit'], dict)):
        raise RealEstateError('transfer_manifest')
    seen = set(); total = 0
    for row in value['files']:
        if (not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256'}
                or not isinstance(row['sha256'], str) or not HASH.fullmatch(row['sha256'])
                or type(row['bytes']) is not int or not 0 < row['bytes'] <= MAX_FILE):
            raise RealEstateError('transfer_descriptor')
        name = checked_path(row['path'])
        if name != 'checkpoint.sqlite' and not Path(name).name.startswith(row['sha256'] + '.'):
            raise RealEstateError('transfer_content_address')
        if name in seen:
            raise RealEstateError('transfer_duplicate')
        seen.add(name); total += row['bytes']
    if 'checkpoint.sqlite' not in seen or total > MAX_TOTAL:
        raise RealEstateError('transfer_total_limit')
    return value


def restore(bundle, target, *, expected_sha256, reserve=RESERVE):
    bundle = Path(bundle).absolute(); target = Path(target).absolute()
    _reject_links(bundle); _reject_links(target)
    if target.exists() or not HASH.fullmatch(expected_sha256) or digest(bundle) != expected_sha256:
        raise RealEstateError('transfer_existing_target_or_hash')
    with tarfile.open(bundle, mode='r|gz') as archive:
        first = archive.next()
        if first is None or first.name != 'manifest.json' or not first.isfile() or not 0 < first.size <= MAX_MANIFEST:
            raise RealEstateError('transfer_manifest')
        manifest = validate(json.loads(archive.extractfile(first).read()))
        rows = {row['path']: row for row in manifest['files']}
        room(target.parent, sum(row['bytes'] for row in rows.values()), reserve)
        target.mkdir(mode=0o700)
        seen = set()
        # Never extractall: reject links, duplicates, unknowns and oversized data.
        while (member := archive.next()) is not None:
            row = rows.get(member.name)
            if (not member.isfile() or row is None or member.name in seen or member.size != row['bytes']):
                raise RealEstateError('transfer_member')
            path = target / checked_path(member.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256(); count = 0
            with archive.extractfile(member) as source, path.open('xb') as output:
                for chunk in iter(lambda: source.read(1024**2), b''):
                    h.update(chunk); count += len(chunk); output.write(chunk)
            if count != row['bytes'] or h.hexdigest() != row['sha256']:
                raise RealEstateError('transfer_file_hash')
            seen.add(member.name)
        if seen != set(rows):
            raise RealEstateError('transfer_missing_file')
    audit = audit_checkpoint(target)
    if audit != manifest['audit']:
        raise RealEstateError('transfer_ledger_changed')
    return {'status': 'verified', 'files': len(rows), 'source_bytes': sum(r['bytes'] for r in rows.values()), 'audit': audit}


def export_cas(storage, bundle=None, *, stream=None, reserve=RESERVE, progress=None):
    """Reuse the last verified recovery set: hundreds of packs, not 35k opens."""
    store = LocalArchiveSet(storage, reserve_bytes=reserve)
    head = store.head()
    if head is None:
        raise RealEstateError('transfer_missing_backup_head')
    manifest = load_set(store, head)
    objects = {head['sha256']: head}
    for group in manifest['groups']:
        obj = group['manifest']; objects[obj['sha256']] = obj
    for row in manifest['files']:
        for segment in parts(row):
            obj = segment['object']
            if obj['sha256'] in objects and objects[obj['sha256']]['bytes'] != obj['bytes']:
                raise RealEstateError('transfer_descriptor')
            objects[obj['sha256']] = obj
    if (bundle is None) == (stream is None):
        raise RealEstateError('transfer_output_required')
    if bundle is not None:
        bundle = Path(bundle).absolute(); _reject_links(bundle)
        if bundle.exists() or bundle.is_relative_to(Path(storage).absolute()):
            raise RealEstateError('transfer_output_exists_or_nested')
    total = sum(obj['bytes'] for obj in objects.values())
    if total > MAX_TOTAL or len(objects) > MAX_FILES:
        raise RealEstateError('transfer_total_limit')
    room(bundle.parent if bundle else Path(storage), total + 1024**2 if bundle else 0, reserve)
    class HashWriter:
        def __init__(self, destination):
            self.destination = destination; self.hash = hashlib.sha256(); self.size = 0
        def write(self, body):
            result = self.destination.write(body)
            if result != len(body): raise RealEstateError('transfer_short_write')
            self.hash.update(body); self.size += len(body)
            return result
        def flush(self): self.destination.flush()
    destination = bundle.open('xb') if bundle else nullcontext(stream)
    with destination as raw:
        writer = HashWriter(raw)
        with gzip.GzipFile(fileobj=writer, mode='wb', compresslevel=1, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode='w|', format=tarfile.USTAR_FORMAT) as archive:
                body = canonical_bytes(head)
                item = tarfile.TarInfo('set-head.json'); item.size = len(body); item.mode = 0o600
                archive.addfile(item, io.BytesIO(body))
                for index, obj in enumerate(objects.values()):
                    body = store.get(obj['sha256'], obj['bytes'])  # Every pack SHA verified.
                    item = tarfile.TarInfo('objects/' + obj['sha256'][:2] + '/' + obj['sha256'] + '.bin')
                    item.size = len(body); item.mode = 0o600
                    archive.addfile(item, io.BytesIO(body))
                    if progress and (index + 1) % 100 == 0:
                        progress({'phase': 'verified_packs', 'objects': index + 1, 'total': len(objects)})
        writer.flush()
    return {'files': manifest['file_count'], 'source_bytes': manifest['source_bytes'],
            'objects': len(objects), 'archive_bytes': writer.size,
            'archive_sha256': writer.hash.hexdigest(), 'head': head, 'audit': manifest['audit']}


def restore_cas(bundle, storage, target, *, expected_sha256, reserve=RESERVE, progress=None):
    """Restore the CAS closure and the complete checkpoint into a new root."""
    bundle = Path(bundle).absolute(); storage = Path(storage).absolute(); target = Path(target).absolute()
    _reject_links(bundle); _reject_links(storage); _reject_links(target)
    if (target.exists() or storage.exists() and any(storage.iterdir())
            or not isinstance(expected_sha256, str) or not HASH.fullmatch(expected_sha256)
            or digest(bundle) != expected_sha256):
        raise RealEstateError('transfer_existing_target_or_hash')
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    total = 0; seen = set()
    with tarfile.open(bundle, mode='r|gz') as archive:
        first = archive.next()
        if first is None or first.name != 'set-head.json' or not first.isfile() or not 0 < first.size <= 1024:
            raise RealEstateError('transfer_manifest')
        head = validate_object(json.loads(archive.extractfile(first).read()))
        while (item := archive.next()) is not None:
            match = re.fullmatch(r'objects/([a-f0-9]{2})/([a-f0-9]{64})\.bin', item.name)
            if (not item.isfile() or match is None or match[1] != match[2][:2]
                    or item.name in seen or not 0 < item.size <= MAX_FILE or len(seen) >= MAX_FILES):
                raise RealEstateError('transfer_member')
            total += item.size
            if total > MAX_TOTAL: raise RealEstateError('transfer_total_limit')
            room(storage, item.size, reserve)
            path = storage / item.name; path.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            with archive.extractfile(item) as source, path.open('xb') as output:
                for data in iter(lambda: source.read(1024**2), b''):
                    output.write(data); h.update(data)
            if h.hexdigest() != match[2]: raise RealEstateError('transfer_file_hash')
            seen.add(item.name)
    (storage / 'set-head.json').write_bytes(canonical_bytes(head))
    store = LocalArchiveSet(storage, reserve_bytes=reserve)
    manifest = load_set(store, head)
    result = restore_set(target, store, descriptor=head, progress=progress)
    return {'status': 'verified', 'head': head, 'files': manifest['file_count'],
            'source_bytes': manifest['source_bytes'], 'objects': len(seen), 'audit': result['audit']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('export', 'restore', 'export-cas', 'restore-cas'))
    parser.add_argument('--root', type=Path)
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--sha256')
    parser.add_argument('--storage', type=Path)
    parser.add_argument('--stdout', action='store_true', help='Stream a verified CAS bundle without a local intermediate file')
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    if args.stdout and (args.mode != 'export-cas' or args.bundle or not args.receipt):
        parser.error('--stdout requires export-cas and --receipt, without --bundle')
    if not args.stdout and not args.bundle:
        parser.error('--bundle is required')
    if args.mode != 'export-cas' and not args.root:
        parser.error('--root is required')
    if args.mode.endswith('-cas') and not args.storage:
        parser.error('--storage is required')
    try:
        if args.mode == 'export-cas':
            result = export_cas(args.storage, args.bundle, stream=sys.stdout.buffer if args.stdout else None)
        elif args.mode == 'restore-cas': result = restore_cas(args.bundle, args.storage, args.root, expected_sha256=args.sha256 or '')
        elif args.mode == 'export': result = export(args.root, args.bundle)
        else: result = restore(args.bundle, args.root, expected_sha256=args.sha256 or '')
        if args.receipt:
            _reject_links(args.receipt.absolute())
            with args.receipt.open('x', encoding='utf-8') as output:
                json.dump(result, output, ensure_ascii=False)
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr if args.stdout else sys.stdout)
    except (OSError, ValueError, KeyError, sqlite3.Error, tarfile.TarError) as error:
        parser.exit(1, 'vps_transfer: ' + (error.code if isinstance(error, RealEstateError) else 'invalid_input') + '\n')


if __name__ == '__main__':
    main()
