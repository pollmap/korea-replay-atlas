"""Partitioned manifests and flat, shared checkpoint slices.

Every slice addresses an existing flat archive object, never another recipe.
No ancestor manifest is needed to restore a version, and no old object is deleted.
"""
from __future__ import annotations

import json

from .real_estate import RealEstateError, canonical_bytes, sha256
from .real_estate_archive import MAX_FILE, MAX_FILES, HASH, validate_manifest

BLOCK_BYTES = 64 * 1024
BUCKETS = 64
MAX_BUCKET_BYTES = 16 * 1024**2
MAX_INDEXED_BYTES = 128 * 1024**2


def validate_object(value):
    if (not isinstance(value, dict) or set(value) != {'sha256', 'bytes'}
            or not isinstance(value['sha256'], str) or not HASH.fullmatch(value['sha256'])
            or type(value['bytes']) is not int or not 0 < value['bytes'] <= MAX_FILE):
        raise RealEstateError('archive_descriptor')
    return value


def parts(row):
    return row.get('segments', [row])


def bucket_for(path):
    return int(sha256(path.encode())[:2], 16) % BUCKETS


def load_manifest(store, descriptor):
    root = json.loads(store.get(descriptor['sha256'], descriptor['bytes']))
    if not isinstance(root, dict):
        raise RealEstateError('archive_manifest')
    if root.get('schema_version') == 1:
        return validate_manifest(root)
    if (root.get('schema_version') != 2 or root.get('kind') != 'private-collector-backup'
            or set(root) != {'schema_version', 'kind', 'buckets', 'audit', 'parent', 'file_count'}
            or not isinstance(root['buckets'], list) or len(root['buckets']) != BUCKETS
            or type(root['file_count']) is not int or not 1 <= root['file_count'] <= MAX_FILES):
        raise RealEstateError('archive_manifest')
    rows = []; indexed_bytes = 0
    for index, obj in enumerate(root['buckets']):
        validate_object(obj)
        indexed_bytes += obj['bytes']
        if obj['bytes'] > MAX_BUCKET_BYTES or indexed_bytes > MAX_INDEXED_BYTES:
            raise RealEstateError('archive_manifest_limit')
        batch = json.loads(store.get(obj['sha256'], obj['bytes']))
        if (not isinstance(batch, list) or len(batch) > MAX_FILES - len(rows)
                or any(not isinstance(r, dict) or not isinstance(r.get('path'), str)
                       or bucket_for(r['path']) != index for r in batch)):
            raise RealEstateError('archive_manifest_bucket')
        rows.extend(batch)
    if len(rows) != root['file_count']:
        raise RealEstateError('archive_manifest_count')
    # Flatten only in memory. The durable root remains a small bucket index.
    return validate_manifest({**root, 'files': rows})


def save_manifest(store, rows, audit, parent, *, previous=None, heartbeat=lambda: None):
    flat = validate_manifest({'schema_version': 2, 'kind': 'private-collector-backup',
                              'files': list(rows), 'audit': audit, 'parent': parent})
    buckets = [[] for _ in range(BUCKETS)]
    for row in flat['files']:
        buckets[bucket_for(row['path'])].append(row)
    old = previous.get('buckets', []) if previous else []
    descriptors = []
    rewritten = 0
    for index, batch in enumerate(buckets):
        body = canonical_bytes(sorted(batch, key=lambda row: row['path']))
        if len(body) > MAX_BUCKET_BYTES:
            raise RealEstateError('archive_manifest_limit')
        descriptor = {'sha256': sha256(body), 'bytes': len(body)}
        if index >= len(old) or descriptor != old[index]:
            heartbeat()
            descriptor = store.put(body)
            rewritten += 1
        descriptors.append(descriptor)
    heartbeat()
    root = {'schema_version': 2, 'kind': 'private-collector-backup', 'buckets': descriptors,
            'file_count': len(flat['files']), 'audit': audit, 'parent': parent}
    if sum(row['bytes'] for row in descriptors) > MAX_INDEXED_BYTES:
        raise RealEstateError('archive_manifest_limit')
    return store.put(canonical_bytes(root)), rewritten


def checkpoint_row(raw, previous_raw, previous_row, store):
    """Keep matching 64 KiB slices; pack changed slices in one new flat object."""
    if not 0 < len(raw) <= MAX_FILE:
        raise RealEstateError('archive_file_limit')
    if len(previous_raw) != previous_row['bytes'] or sha256(previous_raw) != previous_row['sha256']:
        raise RealEstateError('archive_previous_checkpoint_hash')
    segments = []
    changed = bytearray()
    for position in range(0, len(raw), BLOCK_BYTES):
        body = raw[position:position + BLOCK_BYTES]
        digest = sha256(body)
        if body == previous_raw[position:position + BLOCK_BYTES]:
            if 'segments' in previous_row:
                segment = previous_row['segments'][position // BLOCK_BYTES]
            else:
                segment = {'object': previous_row['object'], 'offset': previous_row['offset'] + position,
                           'bytes': len(body), 'sha256': digest}
            segments.append(segment)
        else:
            segments.append({'offset': len(changed), 'bytes': len(body), 'sha256': digest})
            changed.extend(body)
    if changed:
        obj = store.put(bytes(changed))
        for segment in segments:
            if 'object' not in segment:
                segment['object'] = obj
    return {'path': 'checkpoint.sqlite', 'sha256': sha256(raw), 'bytes': len(raw),
            'segments': segments}, len(changed)


def read_file(row, getter):
    """Read each backing object once; bound output by the validated file size."""
    body = bytearray(row['bytes'])
    groups = {}
    position = 0
    for part in parts(row):
        obj = part['object']
        groups.setdefault((obj['sha256'], obj['bytes']), []).append((position, part))
        position += part['bytes']
    if position != row['bytes']:
        raise RealEstateError('archive_file_hash')
    for key, entries in groups.items():
        pack = getter(*key)
        for position, part in entries:
            chunk = pack[part['offset']:part['offset'] + part['bytes']]
            if len(chunk) != part['bytes'] or sha256(chunk) != part['sha256']:
                raise RealEstateError('archive_file_hash')
            body[position:position + len(chunk)] = chunk
    if sha256(body) != row['sha256']:
        raise RealEstateError('archive_file_hash')
    return bytes(body)
