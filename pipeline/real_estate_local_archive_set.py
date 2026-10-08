"""Partitioned laptop recovery sets, preserving the legacy archive namespace.

A set's small restore list covers every file in an audited SQLite checkpoint.
Each year/region/property/trade group is split at 8 GiB of source bytes, while
flat content-addressed objects remain bounded by the existing file contract.
The source, legacy head, and existing immutable objects are never deleted.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from collections import OrderedDict
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from .real_estate import RealEstateError, canonical_bytes, sha256, _reject_links
from .real_estate_archive import (
    HASH, MAX_FILE, MAX_FILES, PACK_BYTES, PREFIXES, audit_checkpoint, checked_path,
    restore as legacy_restore,
)
from .real_estate_local_archive import LocalArchive
from .real_estate_manifest import checkpoint_row, load_manifest, parts, read_file, validate_object

MAX_GROUP = 8 * 1024**3
MAX_GROUP_MANIFEST = 16 * 1024**2
MAX_INDEXED_BYTES = 128 * 1024**2
GROUP_PATTERN = re.compile(r'^(?:control|shared|metadata|(?:apartment|officetel)/(?:sale|rent)/[0-9]{5}/[0-9]{4})$')


class LocalArchiveSet(LocalArchive):
    """Separate CAS head; get/put intentionally share verified legacy objects."""
    head_name = 'set-head.json'
    heads_directory = 'set-heads'
    lock_name = '.set-head.lock'


def _limits(group_limit: int) -> int:
    if type(group_limit) is not int or not 0 < group_limit <= MAX_GROUP:
        raise RealEstateError('archive_set_group_limit')
    return group_limit


def _row(row):
    if not isinstance(row, dict):
        raise RealEstateError('archive_set_descriptor')
    segmented = 'segments' in row
    if set(row) != ({'path', 'sha256', 'bytes', 'segments'} if segmented
                    else {'path', 'sha256', 'bytes', 'object', 'offset'}):
        raise RealEstateError('archive_set_descriptor')
    checked_path(row['path'])
    if (not isinstance(row['sha256'], str) or not HASH.fullmatch(row['sha256'])
            or type(row['bytes']) is not int or not 0 < row['bytes'] <= MAX_FILE):
        raise RealEstateError('archive_set_descriptor')
    segments = parts(row)
    if segmented and (row['path'] != 'checkpoint.sqlite'
            or not isinstance(segments, list)
            or len(segments) != (row['bytes'] + 65535) // 65536):
        raise RealEstateError('archive_set_segments')
    for index, segment in enumerate(segments):
        if segmented and (not isinstance(segment, dict)
                or set(segment) != {'sha256', 'bytes', 'object', 'offset'}
                or type(segment['bytes']) is not int
                or segment['bytes'] != min(65536, row['bytes'] - index * 65536)
                or not isinstance(segment['sha256'], str)
                or not HASH.fullmatch(segment['sha256'])):
            raise RealEstateError('archive_set_segments')
        obj = validate_object(segment['object'])
        if (type(segment['offset']) is not int or segment['offset'] < 0
                or segment['offset'] + segment['bytes'] > obj['bytes']):
            raise RealEstateError('archive_set_descriptor')
    return row


def load_set(store: LocalArchiveSet, descriptor: dict) -> dict:
    """Read and hash every index before trusting counts or allocating a restore."""
    validate_object(descriptor)
    value = json.loads(store.get(descriptor['sha256'], descriptor['bytes']))
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'kind', 'groups',
            'group_limit_bytes', 'file_count', 'source_bytes', 'audit', 'parent'}
            or value['schema_version'] != 1 or value['kind'] != 'private-collector-backup-set'
            or not isinstance(value['groups'], list) or not 1 <= len(value['groups']) <= MAX_FILES
            or type(value['file_count']) is not int or not 1 <= value['file_count'] <= MAX_FILES
            or type(value['source_bytes']) is not int or value['source_bytes'] <= 0
            or not isinstance(value['audit'], dict)):
        raise RealEstateError('archive_set_manifest')
    _limits(value['group_limit_bytes'])
    if value['parent'] is not None:
        validate_object(value['parent'])
    rows = []; seen = set(); group_ids = set(); index_bytes = 0
    for group in value['groups']:
        if (not isinstance(group, dict) or set(group) != {'group', 'part', 'manifest', 'files', 'source_bytes'}
                or not isinstance(group['group'], str) or not GROUP_PATTERN.fullmatch(group['group'])
                or type(group['part']) is not int or group['part'] < 0
                or type(group['files']) is not int or not 1 <= group['files'] <= MAX_FILES
                or type(group['source_bytes']) is not int
                or not 0 < group['source_bytes'] <= value['group_limit_bytes']
                or (group['group'], group['part']) in group_ids):
            raise RealEstateError('archive_set_group')
        group_ids.add((group['group'], group['part']))
        obj = validate_object(group['manifest'])
        manifest_bytes = obj['bytes']
        index_bytes += obj['bytes']
        if obj['bytes'] > MAX_GROUP_MANIFEST or index_bytes > MAX_INDEXED_BYTES:
            raise RealEstateError('archive_set_manifest_limit')
        batch = json.loads(store.get(obj['sha256'], obj['bytes']))
        if not isinstance(batch, list) or len(batch) != group['files'] or len(rows) + len(batch) > MAX_FILES:
            raise RealEstateError('archive_set_group')
        total = 0
        object_sizes = {}
        for item in batch:
            row = _row(item)
            if row['path'] in seen:
                raise RealEstateError('archive_set_duplicate_path')
            seen.add(row['path']); total += row['bytes']
            for part in parts(row):
                obj = part['object']
                if obj['sha256'] in object_sizes and object_sizes[obj['sha256']] != obj['bytes']:
                    raise RealEstateError('archive_set_descriptor')
                object_sizes[obj['sha256']] = obj['bytes']
        if total != group['source_bytes']:
            raise RealEstateError('archive_set_group_bytes')
        # Imported legacy packs can contain bytes belonging to another group.
        # A portable group must fit including those whole objects and its index.
        if sum(object_sizes.values()) + manifest_bytes > value['group_limit_bytes']:
            raise RealEstateError('archive_set_group_object_limit')
        rows.extend(batch)
    if ('checkpoint.sqlite' not in seen or len(rows) != value['file_count']
            or sum(row['bytes'] for row in rows) != value['source_bytes']):
        raise RealEstateError('archive_set_totals')
    return {**value, 'files': rows}


def _previous(store):
    expected = store.head()
    if expected:
        manifest = load_set(store, expected)
    else:
        legacy = LocalArchive(store.root, reserve_bytes=store.reserve_bytes)
        previous_head = legacy.head()
        manifest = load_manifest(legacy, previous_head) if previous_head else {'files': []}
    rows = {row['path']: row for row in manifest['files']}
    # Shared old objects are read back once. No manifest ancestor is needed.
    for digest, size in sorted({(p['object']['sha256'], p['object']['bytes'])
                              for row in rows.values() for p in parts(row)}):
        store.get(digest, size)
    return expected, rows


def _file_groups(database):
    """Assign by source job identity; shared responses retain a shared group."""
    owners = {}
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        property_row = db.execute("SELECT value FROM meta WHERE key='property_type'").fetchone()
        property_type = property_row[0] if property_row else 'apartment'
        if property_type not in ('apartment', 'officetel'):
            raise RealEstateError('archive_set_property_type')
        jobs = list(db.execute('SELECT id,trade_type,lawd_code,deal_month,pages,snapshot FROM jobs'))
        keys = {}
        for job_id, trade, region, month, pages, snapshot in jobs:
            if (trade not in ('sale', 'rent') or not re.fullmatch(r'[0-9]{5}', region)
                    or not re.fullmatch(r'[0-9]{4}(?:0[1-9]|1[0-2])', month)):
                raise RealEstateError('archive_set_job_identity')
            group = f'{property_type}/{trade}/{region}/{month[:4]}'
            keys[job_id] = group
            refs = json.loads(pages)
            if snapshot:
                refs.append(json.loads(snapshot))
            for ref in refs:
                owners.setdefault(checked_path(ref['path']), set()).add(group)
        for job_id, descriptor in db.execute('SELECT job_id,descriptor FROM snapshots'):
            if job_id not in keys:
                raise RealEstateError('archive_set_snapshot_job')
            owners.setdefault(checked_path(json.loads(descriptor)['path']), set()).add(keys[job_id])
    return ({name: next(iter(groups)) if len(groups) == 1 else 'shared'
             for name, groups in owners.items()}, property_type)


def _group_for_path(name, owners, property_type):
    if name == 'checkpoint.sqlite':
        return 'control'
    if name in owners:
        return owners[name]
    # A superseded/partial raw page can be absent from today's job references,
    # but its immutable collector path still carries the original source scope.
    match = re.fullmatch(r'raw/(sale|rent)/([0-9]{5})/([0-9]{4})(?:0[1-9]|1[0-2])/[a-f0-9]{64}\.xml', name)
    if match:
        return f'{property_type}/{match[1]}/{match[2]}/{match[3]}'
    return 'metadata'


def _paths(root, checkpoint):
    paths = [(checkpoint, 'checkpoint.sqlite')]

    def visit(directory):
        # Each ancestor has already been checked before descending. Checking
        # every ancestor again for every small XML creates hundreds of
        # thousands of redundant Windows reparse-point filesystem calls.
        with os.scandir(directory) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink() or hasattr(path, 'is_junction') and path.is_junction():
                raise RealEstateError('linked_path')
            if entry.is_dir(follow_symlinks=False):
                visit(path)
            elif entry.is_file(follow_symlinks=False):
                paths.append((path, checked_path(path.relative_to(root).as_posix())))

    for prefix in sorted(PREFIXES):
        directory = root / prefix
        if not directory.exists():
            continue
        _reject_links(directory)
        visit(directory)
    from .real_estate_working_store import append_archived_paths
    append_archived_paths(root, paths)
    if len(paths) > MAX_FILES:
        raise RealEstateError('archive_set_file_limit')
    for path, _ in paths:
        if not 0 < path.stat().st_size <= MAX_FILE:
            raise RealEstateError('archive_file_limit')
    return paths


def _space(store, additional):
    free = shutil.disk_usage(store.root).free
    if free - additional < store.reserve_bytes:
        raise RealEstateError('disk_reserve')
    return free


def _row_upper(entry, previous):
    if entry['reusable']:
        return len(canonical_bytes(previous[entry['path']])) + 2
    if entry['path'] == 'checkpoint.sqlite' and entry['path'] in previous:
        # An updated checkpoint inherits unchanged 64 KiB slices. Count the
        # larger segmented descriptor before any changed bytes are written.
        segments = [{'sha256': 'f' * 64, 'bytes': min(65536, entry['bytes'] - position),
                     'object': {'sha256': 'f' * 64, 'bytes': MAX_FILE}, 'offset': MAX_FILE}
                    for position in range(0, entry['bytes'], 65536)]
        return len(canonical_bytes({key: entry[key] for key in ('path', 'sha256', 'bytes')} |
                                   {'segments': segments})) + 2
    # Exact field set, using the longest allowed offset/object-size decimals.
    return len(canonical_bytes({key: entry[key] for key in ('path', 'sha256', 'bytes')} |
                              {'object': {'sha256': 'f' * 64, 'bytes': MAX_FILE},
                               'offset': MAX_FILE})) + 2


@contextmanager
def _snapshot(root, store, progress=None):
    root = Path(root).absolute(); _reject_links(root)
    original = root / 'checkpoint.sqlite'; _reject_links(original)
    # SQLite's WAL can contain newer pages than the main file. Include its size
    # in the upper bound before creating a durable snapshot on the store disk.
    wal = root / 'checkpoint.sqlite-wal'; _reject_links(wal)
    copy_allowance = original.stat().st_size + (wal.stat().st_size if wal.exists() else 0)
    _space(store, copy_allowance + PACK_BYTES)
    if progress:
        progress({'phase': 'checkpoint-copy'})
    with tempfile.TemporaryDirectory(prefix='.set-checkpoint-', dir=store.root) as temporary:
        copy = Path(temporary) / 'checkpoint.sqlite'
        with closing(sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(copy)) as dest:
            source.backup(dest)
        # First check SQLite, lease and reference metadata. Actual file hashes
        # are verified once in _plan and matched against these references
        # before writing objects, then checked again in the restore-list audit.
        refs = {}
        with closing(sqlite3.connect(copy.as_uri() + '?mode=ro', uri=True)) as db:
            references = []
            for pages, snapshot in db.execute('SELECT pages,snapshot FROM jobs'):
                references.extend(json.loads(pages))
                if snapshot:
                    references.append(json.loads(snapshot))
            references.extend(json.loads(row[0]) for row in db.execute('SELECT descriptor FROM snapshots'))
        for ref in references:
            name = checked_path(ref['path'])
            candidate = {key: ref[key] for key in ('path', 'sha256', 'bytes')}
            if name in refs and refs[name] != candidate:
                raise RealEstateError('archive_reference_hash')
            refs[name] = candidate
        audit = audit_checkpoint(root, copy, descriptors=refs)
        if progress:
            progress({'phase': 'checkpoint-metadata-verified'})
        yield root, copy, audit


def _plan(root, checkpoint, store, previous, *, group_limit, progress=None):
    paths = _paths(root, checkpoint)
    if not set(previous) <= {name for _, name in paths}:
        raise RealEstateError('archive_previous_files_missing')
    owners, property_type = _file_groups(checkpoint)
    source_bytes = 0; new_bytes = 0; entries = []
    for index, (path, name) in enumerate(paths):
        body = path.read_bytes(); digest = sha256(body)
        size = len(body); source_bytes += size
        if size > group_limit:
            raise RealEstateError('archive_set_group_file_limit')
        if name != 'checkpoint.sqlite' and not Path(name).name.startswith(digest + '.'):
            raise RealEstateError('archive_content_address')
        old = previous.get(name)
        reusable = bool(old and old['sha256'] == digest and old['bytes'] == size)
        if not reusable:
            new_bytes += size
        group = _group_for_path(name, owners, property_type)
        entries.append({'path': name, 'file': path, 'sha256': digest, 'bytes': size,
                        'group': group, 'reusable': reusable})
        if progress and (index + 1) % 1000 == 0:
            progress({'phase': 'source-sha', 'verified_files': index + 1, 'total_files': len(paths)})
    audit_checkpoint(root, checkpoint, descriptors={entry['path']: entry for entry in entries})
    # Each row descriptor (including checkpoint slices), root and group entry
    # costs are counted conservatively without assuming compression or dedup.
    # Account for a worst case of one root group descriptor per source file,
    # plus exact bounded row descriptors. This includes tiny-index-heavy data.
    row_index_allowance = sum(_row_upper(entry, previous) for entry in entries)
    root_index_allowance = 65536 + 512 * len(entries)
    index_allowance = row_index_allowance + root_index_allowance
    if row_index_allowance > MAX_INDEXED_BYTES or root_index_allowance > MAX_FILE:
        raise RealEstateError('archive_set_manifest_limit')
    checkpoint_bytes = checkpoint.stat().st_size
    backup_remaining = new_bytes + index_allowance + PACK_BYTES
    free = shutil.disk_usage(store.root).free
    result = {'source_bytes': source_bytes, 'files': len(entries),
              'group_limit_bytes': group_limit, 'new_object_upper_bytes': new_bytes,
              'manifest_upper_bytes': index_allowance, 'checkpoint_scratch_bytes': checkpoint_bytes,
              'backup_additional_upper_bytes': backup_remaining + checkpoint_bytes,
              'restore_additional_bytes': source_bytes,
              'backup_and_restore_upper_bytes': backup_remaining + checkpoint_bytes + source_bytes,
              'available_bytes': free + checkpoint_bytes, 'reserve_bytes': store.reserve_bytes,
              'fits_backup': free - backup_remaining >= store.reserve_bytes,
              'fits_backup_and_restore': free - backup_remaining - source_bytes >= store.reserve_bytes,
              'same_disk_device_loss_protection': False}
    return result, entries


def plan_set(root, store: LocalArchiveSet, *, group_limit=MAX_GROUP):
    """No head writes; includes checkpoint, manifests and a full restore budget."""
    _limits(group_limit)
    _, previous = _previous(store)
    with _snapshot(root, store) as (root, checkpoint, audit):
        result, _ = _plan(root, checkpoint, store, previous, group_limit=group_limit)
    return {**result, 'audit': audit, 'public_release': False}


def backup_set(root, store: LocalArchiveSet, progress=None, *, group_limit=MAX_GROUP):
    _limits(group_limit)
    expected, previous = _previous(store)
    with _snapshot(root, store, progress) as (root, checkpoint, audit):
        plan, entries = _plan(root, checkpoint, store, previous, group_limit=group_limit, progress=progress)
        if not plan['fits_backup']:
            raise RealEstateError('disk_reserve')
        if progress:
            progress({'phase': 'space-preflight', **plan})
        checkpoint_changed_bytes = 0
        checkpoint_entry = next(entry for entry in entries if entry['path'] == 'checkpoint.sqlite')
        if not checkpoint_entry['reusable'] and 'checkpoint.sqlite' in previous:
            old_row = previous['checkpoint.sqlite']
            raw = checkpoint.read_bytes()
            if len(raw) != checkpoint_entry['bytes'] or sha256(raw) != checkpoint_entry['sha256']:
                raise RealEstateError('archive_set_source_changed')
            segmented, checkpoint_changed_bytes = checkpoint_row(
                raw, read_file(old_row, store.get), old_row, store)
            for digest, size in {(part['object']['sha256'], part['object']['bytes']) for part in parts(segmented)}:
                store.get(digest, size)
            previous['checkpoint.sqlite'] = segmented
            checkpoint_entry['reusable'] = True
        elif not checkpoint_entry['reusable']:
            checkpoint_changed_bytes = checkpoint_entry['bytes']
        grouped = {}; grouped_bytes = {}; grouped_index_bytes = {}
        grouped_objects = {}; grouped_object_bytes = {}; grouped_new_bytes = {}
        for entry in sorted(entries, key=lambda value: (value['group'], value['path'])):
            batches = grouped.setdefault(entry['group'], [[]])
            row_bytes = _row_upper(entry, previous)
            objects = {part['object']['sha256']: part['object']['bytes']
                       for part in parts(previous[entry['path']])} if entry['reusable'] else {}
            inherited = grouped_objects.setdefault(entry['group'], {})
            added_object_bytes = sum(size for digest, size in objects.items() if digest not in inherited)
            added_new_bytes = 0 if entry['reusable'] else entry['bytes']
            own_upper = sum(objects.values()) + added_new_bytes + row_bytes + 2
            if own_upper > group_limit:
                raise RealEstateError('archive_set_group_file_limit')
            if row_bytes + 2 > MAX_GROUP_MANIFEST:
                raise RealEstateError('archive_set_manifest_limit')
            if (grouped_bytes.get(entry['group'], 0) + entry['bytes'] > group_limit
                    or grouped_index_bytes.get(entry['group'], 2) + row_bytes > MAX_GROUP_MANIFEST
                    or grouped_object_bytes.get(entry['group'], 0) + added_object_bytes
                       + grouped_new_bytes.get(entry['group'], 0) + added_new_bytes
                       + grouped_index_bytes.get(entry['group'], 2) + row_bytes > group_limit):
                batches.append([])
                grouped_bytes[entry['group']] = 0
                grouped_index_bytes[entry['group']] = 2
                inherited.clear()
                grouped_object_bytes[entry['group']] = 0
                grouped_new_bytes[entry['group']] = 0
                added_object_bytes = sum(objects.values())
            batches[-1].append(entry)
            grouped_bytes[entry['group']] = grouped_bytes.get(entry['group'], 0) + entry['bytes']
            grouped_index_bytes[entry['group']] = grouped_index_bytes.get(entry['group'], 2) + row_bytes
            inherited.update(objects)
            grouped_object_bytes[entry['group']] = grouped_object_bytes.get(entry['group'], 0) + added_object_bytes
            grouped_new_bytes[entry['group']] = grouped_new_bytes.get(entry['group'], 0) + added_new_bytes
        groups = []; index_bytes = 0
        for name, batches in sorted(grouped.items()):
            for number, batch in enumerate(batches):
                rows = []; pack = bytearray(); pending = []

                def flush():
                    if pack:
                        descriptor = store.put(bytes(pack))
                        # Local writes are verified before a group can be used.
                        store.get(descriptor['sha256'], descriptor['bytes'])
                        rows.extend({**row, 'object': descriptor} for row in pending)
                        pack.clear(); pending.clear()

                for entry in batch:
                    if entry['reusable']:
                        rows.append(previous[entry['path']])
                        continue
                    raw = entry['file'].read_bytes()
                    if len(raw) != entry['bytes'] or sha256(raw) != entry['sha256']:
                        raise RealEstateError('archive_set_source_changed')
                    if len(pack) + len(raw) > PACK_BYTES:
                        flush()
                    pending.append({key: entry[key] for key in ('path', 'sha256', 'bytes')} | {'offset': len(pack)})
                    pack.extend(raw)
                flush()
                body = canonical_bytes(sorted(rows, key=lambda row: row['path']))
                index_bytes += len(body)
                if len(body) > MAX_GROUP_MANIFEST or index_bytes > MAX_INDEXED_BYTES:
                    raise RealEstateError('archive_set_manifest_limit')
                descriptor = store.put(body)
                store.get(descriptor['sha256'], descriptor['bytes'])
                groups.append({'group': name, 'part': number, 'manifest': descriptor,
                               'files': len(rows), 'source_bytes': sum(row['bytes'] for row in rows)})
                if progress:
                    progress({'phase': 'verified-group', 'group': name, 'part': number, 'files': len(rows)})
        value = {'schema_version': 1, 'kind': 'private-collector-backup-set',
                 'groups': groups, 'group_limit_bytes': group_limit,
                 'file_count': plan['files'], 'source_bytes': plan['source_bytes'],
                 'audit': audit, 'parent': expected}
        descriptor = store.put(canonical_bytes(value))
        manifest = load_set(store, descriptor)
        # Exact snapshot reference closure, with every source/object hash already
        # checked above. This rejects an incomplete restore list before CAS.
        closure = audit_checkpoint(root, checkpoint,
                                   descriptors={row['path']: row for row in manifest['files']})
        if closure != audit:
            raise RealEstateError('archive_set_audit_changed')
        store.promote(descriptor, expected)
    return {'backup_set': descriptor, 'groups': len(groups), 'files': plan['files'],
            'source_bytes': plan['source_bytes'], 'audit': audit, 'space': plan,
            'checkpoint_changed_bytes': checkpoint_changed_bytes,
            'legacy_head_preserved': True, 'public_release': False}


def restore_set(target, store: LocalArchiveSet, descriptor=None, progress=None):
    """Restore the current set, or a legacy backup when no set exists yet."""
    target = Path(target).absolute(); _reject_links(target)
    if target.exists():
        raise RealEstateError('archive_restore_requires_new_directory')
    descriptor = descriptor or store.head()
    if descriptor is None:
        legacy = LocalArchive(store.root, reserve_bytes=store.reserve_bytes)
        descriptor = legacy.head()
        if descriptor is None:
            raise RealEstateError('archive_no_backup')
        old = load_manifest(legacy, descriptor)
        parent = target.parent
        while not parent.exists():
            parent = parent.parent
        if shutil.disk_usage(parent).free - sum(row['bytes'] for row in old['files']) < store.reserve_bytes:
            raise RealEstateError('disk_reserve')
        return {**legacy_restore(target, legacy, descriptor, progress), 'format': 'legacy'}
    manifest = load_set(store, descriptor)
    parent = target.parent
    while not parent.exists():
        parent = parent.parent
    _reject_links(parent)
    if shutil.disk_usage(parent).free - manifest['source_bytes'] < store.reserve_bytes:
        raise RealEstateError('disk_reserve')
    # Verify all referenced flat objects before creating a restore directory.
    for digest, size in sorted({(p['object']['sha256'], p['object']['bytes'])
                              for row in manifest['files'] for p in parts(row)}):
        store.get(digest, size)
    target.mkdir(parents=True)
    cache = OrderedDict(); cache_bytes = 0
    written = {}

    def cached_get(digest, size):
        nonlocal cache_bytes
        key = (digest, size)
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        raw = store.get(digest, size)
        # Do not reread an 8 MiB pack once per small XML file. Oversized single
        # file objects are verified and returned without expanding the cache.
        if size <= 32 * 1024**2:
            while cache and cache_bytes + size > 32 * 1024**2:
                _, removed = cache.popitem(last=False); cache_bytes -= len(removed)
            cache[key] = raw; cache_bytes += size
        return raw

    for index, row in enumerate(manifest['files']):
        destination = target / row['path']; _reject_links(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        body = read_file(row, cached_get)
        if shutil.disk_usage(target).free - len(body) < store.reserve_bytes:
            raise RealEstateError('disk_reserve')
        with destination.open('xb') as stream:
            stream.write(body)
        actual = destination.read_bytes()
        if len(actual) != row['bytes'] or sha256(actual) != row['sha256']:
            raise RealEstateError('archive_set_restore_file_hash')
        written[row['path']] = {key: row[key] for key in ('path', 'sha256', 'bytes')}
        if progress and (index + 1) % 100 == 0:
            progress({'phase': 'restore-set', 'verified_files': index + 1, 'total_files': len(manifest['files'])})
    # Every restored file has just been read back and hashed, including retained
    # unreferenced originals. Check SQLite/reference closure against those real
    # bytes instead of repeating ancestor checks and rereading all XML files.
    audit = audit_checkpoint(target, descriptors=written)
    if audit != manifest['audit']:
        raise RealEstateError('archive_restored_audit_changed')
    return {'restored_set': descriptor, 'files': len(manifest['files']),
            'groups': len(manifest['groups']), 'source_bytes': manifest['source_bytes'],
            'audit': audit, 'source_calls': 0, 'format': 'partitioned-set'}
