"""Publish closed, verified SQLite generations; never expose the writer's WAL."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
import uuid

from .real_estate import RealEstateError, canonical_bytes, _reject_links


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def publish(data, *, reserve_bytes=0):
    """Online backup is consistent even while the collector is committing."""
    data = Path(data).absolute(); _reject_links(data)
    source = data / 'collector/checkpoint.sqlite'; _reject_links(source)
    if not source.is_file():
        raise RealEstateError('read_model_source_missing')
    if shutil.disk_usage(data).free < reserve_bytes + source.stat().st_size * 3:
        raise RealEstateError('read_model_storage_reserve')
    folder = data / 'read-model'; _reject_links(folder)
    folder.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    candidate = folder / (token + '.candidate.sqlite')
    deadline = time.monotonic() + 120
    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise RealEstateError('read_model_backup_timeout')
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=5)) as origin:
        with closing(sqlite3.connect(candidate)) as target:
            origin.backup(target, pages=256, progress=progress, sleep=.05)
            target.commit()
            if target.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
                raise RealEstateError('read_model_journal')
            if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise RealEstateError('read_model_integrity')
            # Validate the columns used by both public API and acquisition audit.
            target.execute('SELECT key,value FROM meta LIMIT 1').fetchall()
            target.execute('SELECT id,lawd_code,deal_month,trade_type,status,snapshot,error_code FROM jobs LIMIT 1').fetchall()
            counts = {table: target.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                      for table in ('jobs', 'calls', 'snapshots')}
    digest = sha256(candidate)
    generation = token + '-' + digest
    final = folder / (generation + '.sqlite')
    os.replace(candidate, final)
    with final.open('rb+') as handle:
        os.fsync(handle.fileno())
    manifest = {'schema_version': 1, 'generation': generation, 'sha256': digest,
                'bytes': final.stat().st_size, 'counts': counts,
                'created_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'public_release': False}
    temporary = folder / (token + '.json')
    temporary.write_bytes(canonical_bytes(manifest))
    with temporary.open('rb+') as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, folder / 'current.json')
    return manifest


class ReadModel:
    def __init__(self, data):
        self.folder = Path(data).absolute() / 'read-model'
        self.last_good = None
        self.error_code = None

    def resolve(self):
        try:
            path = self.folder / 'current.json'; _reject_links(path)
            if path.stat().st_size > 4096:
                raise RealEstateError('read_model_manifest_size')
            manifest = json.loads(path.read_bytes())
            generation = manifest.get('generation', '')
            if not isinstance(generation, str) or not re.fullmatch(r'[a-f0-9]{32}-[a-f0-9]{64}', generation):
                raise RealEstateError('read_model_generation')
            if not self.last_good or generation != self.last_good[1]['generation']:
                database = self.folder / (generation + '.sqlite'); _reject_links(database)
                if (manifest.get('schema_version') != 1 or manifest.get('sha256') != generation[33:]
                        or database.stat().st_size != manifest.get('bytes')
                        or sha256(database) != manifest['sha256']):
                    raise RealEstateError('read_model_hash')
                with closing(sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                    if db.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
                        raise RealEstateError('read_model_journal')
                    db.execute('SELECT 1 FROM meta LIMIT 1').fetchone()
                self.last_good = (database, manifest)
            self.error_code = None
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, RealEstateError):
            self.error_code = 'read_model_update_unavailable'
            if not self.last_good:
                raise RealEstateError('read_model_not_ready') from None
        return self.last_good
