"""Restore only the verified private files needed to build a property release.

This never calls the source API, writes to the remote archive, or changes its
head. A candidate still has to pass ``real_estate_publish.publish`` and the
separate Pages release gates before it can become public.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3

from .real_estate import RealEstateError, _reject_links, sha256
from .real_estate_archive import checked_path
from .real_estate_publish import checked_read, snapshot_sources
from .real_estate_remote import RemoteWorkspace
from .real_estate_storage import decode_snapshot


def _reference(workspace, descriptor):
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get('path'), str):
        raise RealEstateError('publication_reference_invalid')
    path = checked_path(descriptor.get('path'))
    expected = workspace.files.get(path)
    if (expected is None or descriptor.get('sha256') != expected['sha256']
            or descriptor.get('bytes') != expected['bytes']):
        raise RealEstateError('publication_reference_not_in_head')
    return path


def _hydrate(workspace, references, reuse_roots):
    names = sorted(references)
    reused = 0
    for name in names:
        target = workspace.root / name
        if target.exists():
            continue
        row = workspace.files[name]
        for root in reuse_roots:
            source = root / name
            _reject_links(source)
            if not source.is_file() or source.stat().st_size != row['bytes']:
                continue
            if sha256(source.read_bytes()) != row['sha256']:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Copy, rather than hard-link, so later local edits to an older
            # workspace cannot mutate this release candidate's input bytes.
            with source.open('rb') as stream, target.open('xb') as output:
                shutil.copyfileobj(stream, output, 1024 * 1024)
            reused += 1
            break
    # The archive packs many small files into one object. RemoteWorkspace keeps
    # only the last object in memory, so path order can download the same pack
    # repeatedly. Verify already present files, then read missing files in
    # backing-object order. The caller's input order has no publication meaning.
    present = [name for name in names if (workspace.root / name).exists()]
    missing = [name for name in names if not (workspace.root / name).exists()]
    missing.sort(key=lambda name: (
        workspace.files[name].get('object', {}).get('sha256', ''), name))
    workspace.hydrate(present)
    workspace.hydrate(missing)
    return reused


def hydrate_for_publication(workspace, *, reuse_roots=()):
    """Materialize every current source and every refresh's last good source.

    The caller pins a ``RemoteWorkspace`` to one archive head. The immutable
    publication routine later reparses and hashes all of these files itself.
    """
    roots = tuple(Path(root).absolute() for root in reuse_roots)
    for root in roots:
        _reject_links(root)
        if not root.is_dir() or root == workspace.root:
            raise RealEstateError('publication_cache_invalid')
    initial = set()
    stale = []
    with closing(sqlite3.connect((workspace.root / 'checkpoint.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        if [row[0] for row in db.execute('PRAGMA integrity_check')] != ['ok']:
            raise RealEstateError('archive_sqlite_integrity')
        for job in db.execute('SELECT id,lawd_code,deal_month,trade_type,status,pages,snapshot FROM jobs'):
            status = job['status']
            if status not in ('complete', 'empty', 'pending', 'partial', 'failed'):
                continue
            if not job['snapshot']:
                if status in ('complete', 'empty'):
                    raise RealEstateError('missing_complete_snapshot')
                continue
            initial.add(_reference(workspace, json.loads(job['snapshot'])))
            if status in ('complete', 'empty'):
                for ref in json.loads(job['pages']):
                    initial.add(_reference(workspace, ref))
            else:
                stale.append(dict(job))
    reused = _hydrate(workspace, initial, roots)
    prior = set()
    for job in stale:
        descriptor = json.loads(job['snapshot'])
        recorded = decode_snapshot(checked_read(workspace.root, descriptor, 128 * 1024**2), descriptor)
        for ref in snapshot_sources(job, recorded):
            prior.add(_reference(workspace, ref))
    reused += _hydrate(workspace, prior, roots)
    return {'archive_head': workspace.head, 'current_references': len(initial),
            'refresh_source_references': len(prior), 'reused_files': reused,
            'remote_hydrated_files': workspace.hydrated_files,
            'remote_hydrated_bytes': workspace.hydrated_bytes,
            'raw_verified_by_publisher': False, 'public_release': False}
