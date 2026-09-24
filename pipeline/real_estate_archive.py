"""Private, content-addressed D1 backup for the official transaction collector.

This is recovery storage, not a public property release. A head is advanced only
after every referenced byte has been read back and verified. No source API calls.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import time
import zlib

from .real_estate import RealEstateError, canonical_bytes, sha256, _reject_links

CHUNK = 48 * 1024
MAX_FILE = 256 * 1024**2
MAX_TOTAL = 20 * 1024**3
MAX_FILES = 300_000
PACK_BYTES = 8 * 1024**2
SHARD_CAP = 380 * 1024**2
HASH = re.compile(r'[a-f0-9]{64}\Z')
PREFIXES = {'raw', 'snapshots', 'registry', 'runs', 'changes', 'history-windows'}


def checked_path(value):
    if not isinstance(value, str) or '\\' in value or ':' in value:
        raise RealEstateError('archive_path')
    p = PurePosixPath(value)
    if p.is_absolute() or str(p) != value or any(x in ('', '.', '..') for x in p.parts):
        raise RealEstateError('archive_path')
    if value != 'checkpoint.sqlite' and (len(p.parts) < 2 or p.parts[0] not in PREFIXES
            or not re.fullmatch(r'[a-f0-9]{64}\.(?:xml|json|json.gz)', p.name)):
        raise RealEstateError('archive_path')
    return value


def decode_object(encoded, digest, size):
    if type(size) is not int or not 0 < size <= MAX_FILE or not HASH.fullmatch(digest):
        raise RealEstateError('archive_descriptor')
    try:
        d = zlib.decompressobj()
        raw = d.decompress(encoded, size + 1)
        if len(raw) != size or not d.eof or d.unused_data or d.unconsumed_tail or sha256(raw) != digest:
            raise RealEstateError('archive_object_hash')
        return raw
    except zlib.error:
        raise RealEstateError('archive_object_encoding') from None


def validate_manifest(value):
    if not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('kind') != 'private-collector-backup':
        raise RealEstateError('archive_manifest')
    rows = value.get('files')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        raise RealEstateError('archive_manifest')
    seen = set(); total = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'sha256', 'bytes', 'object', 'offset'}:
            raise RealEstateError('archive_descriptor')
        name = checked_path(row['path'])
        if name in seen or not isinstance(row['sha256'], str) or not HASH.fullmatch(row['sha256']):
            raise RealEstateError('archive_descriptor')
        if type(row['bytes']) is not int or not 0 < row['bytes'] <= MAX_FILE:
            raise RealEstateError('archive_descriptor')
        obj = row['object']
        if (not isinstance(obj,dict) or set(obj) != {'sha256','bytes'}
                or not isinstance(obj['sha256'],str) or not HASH.fullmatch(obj['sha256'])
                or type(obj['bytes']) is not int or not 0 < obj['bytes'] <= MAX_FILE
                or type(row['offset']) is not int or row['offset'] < 0
                or row['offset']+row['bytes'] > obj['bytes']):
            raise RealEstateError('archive_descriptor')
        seen.add(name); total += row['bytes']
    if 'checkpoint.sqlite' not in seen or total > MAX_TOTAL:
        raise RealEstateError('archive_total_limit')
    return value


def audit_checkpoint(root, database=None, *, descriptors=None):
    """Audit SQLite and references against bytes or a verified manifest's descriptors.

    Descriptor mode checks reference closure, not the availability of every old
    remote object; the parent backup's independent byte verification is retained.
    """
    root = Path(root).absolute(); _reject_links(root)
    database = Path(database) if database else root / 'checkpoint.sqlite'; _reject_links(database)
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RealEstateError('archive_sqlite_integrity')
        if db.execute('SELECT COUNT(*) FROM lease WHERE expires>?', (time.time(),)).fetchone()[0]:
            raise RealEstateError('archive_collector_active')
        refs = []
        for pages, snapshot in db.execute('SELECT pages,snapshot FROM jobs'):
            refs.extend(json.loads(pages))
            if snapshot: refs.append(json.loads(snapshot))
        refs.extend(json.loads(r[0]) for r in db.execute('SELECT descriptor FROM snapshots'))
        registry = db.execute("SELECT value FROM meta WHERE key='registry_sha256'").fetchone()[0]
        registry_path = root / f'registry/{registry}.json'
        _reject_links(registry_path)
        if sha256(registry_path.read_bytes()) != registry:
            raise RealEstateError('archive_registry_hash')
        counts = dict(db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status'))
        calls = db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
        snapshots = db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    checked = set()
    for ref in refs:
        name = checked_path(ref['path'])
        identity = (name, ref['sha256'], ref['bytes'])
        if identity in checked: continue
        if descriptors is None:
            path = root / name; _reject_links(path)
            raw = path.read_bytes()
            if len(raw) != ref['bytes'] or sha256(raw) != ref['sha256']:
                raise RealEstateError('archive_reference_hash')
        else:
            row=descriptors.get(name)
            if row is None or row['sha256']!=ref['sha256'] or row['bytes']!=ref['bytes']:
                raise RealEstateError('archive_reference_hash')
        checked.add(identity)
    return {'jobs': counts, 'calls': calls, 'snapshots': snapshots, 'verified_references': len(checked)}


class D1Archive:
    """Four private object shards and one control database; no public Worker route."""
    def __init__(self, config, token=None):
        self.account = config['account_id']; self.control = config['control_database']
        self.shards = config['object_databases']; self.token = token or os.environ.get('CLOUDFLARE_API_TOKEN')
        if (not re.fullmatch(r'[a-f0-9]{32}', self.account)
                or not isinstance(self.shards, list) or len(self.shards) != 4
                or len(set([self.control, *self.shards])) != 5
                or any(not re.fullmatch(r'[a-f0-9-]{36}', d) for d in [self.control, *self.shards])
                or not self.token):
            raise RealEstateError('archive_configuration')

    def query(self, database, sql, params=()):
        conn = http.client.HTTPSConnection('api.cloudflare.com', timeout=60)
        try:
            body = canonical_bytes({'sql': sql, 'params': list(params)})
            conn.request('POST', f'/client/v4/accounts/{self.account}/d1/database/{database}/query',
                         body, {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
            response = conn.getresponse(); raw = response.read(16 * 1024**2 + 1)
            if response.status != 200:
                if response.status==400:
                    try:
                        for error in json.loads(raw).get('errors',[]):
                            code=str(error.get('message','')).split(':',1)[0]
                            if code in ('collection_ownership_lost','collection_daily_budget','collection_invalid_reservation','collection_baseline_required','collection_baseline_conflict'):
                                raise RealEstateError(code)
                    except (json.JSONDecodeError,AttributeError,TypeError):
                        pass
                raise RealEstateError('archive_remote_http_'+str(response.status))
            if len(raw) > 16 * 1024**2:
                raise RealEstateError('archive_remote_response_limit')
            parsed = json.loads(raw)
            if not parsed.get('success') or not parsed.get('result') or not all(r.get('success') for r in parsed['result']):
                raise RealEstateError('archive_remote_query')
            return parsed['result'][0]
        except (OSError, http.client.HTTPException, json.JSONDecodeError):
            raise RealEstateError('archive_remote_unavailable') from None
        finally:
            conn.close()

    def initialize(self):
        self.query(self.control, 'CREATE TABLE IF NOT EXISTS backup_heads (name TEXT PRIMARY KEY, digest TEXT NOT NULL, bytes INTEGER NOT NULL)')
        self.query(self.control, 'CREATE TABLE IF NOT EXISTS collection_owner (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT, generation INTEGER NOT NULL, expires INTEGER NOT NULL, base_digest TEXT, base_bytes INTEGER)')
        self.query(self.control, 'INSERT OR IGNORE INTO collection_owner(id,generation,expires) VALUES(1,0,0)')
        for database in self.shards:
            self.query(database, 'CREATE TABLE IF NOT EXISTS archive_chunks (digest TEXT NOT NULL, part INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(digest,part))')

    def database(self, digest):
        if not HASH.fullmatch(digest): raise RealEstateError('archive_digest')
        return self.shards[int(digest[:2], 16) % len(self.shards)]

    def put(self, raw):
        if not 0 < len(raw) <= MAX_FILE: raise RealEstateError('archive_file_limit')
        digest = sha256(raw); database = self.database(digest)
        encoded = zlib.compress(raw, 6)
        chunks = [base64.b64encode(encoded[i:i+CHUNK]).decode() for i in range(0, len(encoded), CHUNK)]
        existing = self.query(database, 'SELECT part FROM archive_chunks WHERE digest=? ORDER BY part', [digest])
        rows = existing['results']
        for row in rows:
            if not 0 <= row['part'] < len(chunks):
                raise RealEstateError('archive_immutable_conflict')
        have = {r['part'] for r in rows}
        size = existing.get('meta', {}).get('size_after')
        if type(size) is not int or size + sum(len(v) + 512 for i,v in enumerate(chunks) if i not in have) > SHARD_CAP:
            raise RealEstateError('archive_free_storage_limit')
        missing = [(digest,i,chunk) for i,chunk in enumerate(chunks) if i not in have]
        for start in range(0,len(missing),8):
            group = missing[start:start+8]
            self.query(database,'INSERT OR IGNORE INTO archive_chunks(digest,part,payload) VALUES '+','.join('(?,?,?)' for _ in group),[v for row in group for v in row])
        # Every newly uploaded object is read back; interrupted puts never become heads.
        if self.get(digest, len(raw)) != raw: raise RealEstateError('archive_readback')
        return {'sha256': digest, 'bytes': len(raw)}

    def get(self, digest, size):
        database = self.database(digest); rows = []
        while True:
            batch = self.query(database, 'SELECT part,payload FROM archive_chunks WHERE digest=? AND part>=? ORDER BY part LIMIT 30', [digest,len(rows)])['results']
            if [r['part'] for r in batch] != list(range(len(rows),len(rows)+len(batch))):
                raise RealEstateError('archive_missing_chunk')
            rows.extend(batch)
            if len(rows) > MAX_FILE//CHUNK+100: raise RealEstateError('archive_chunk_limit')
            if len(batch)<30: break
        if not rows or [r['part'] for r in rows] != list(range(len(rows))):
            raise RealEstateError('archive_missing_chunk')
        try: encoded = b''.join(base64.b64decode(r['payload'], validate=True) for r in rows)
        except (ValueError, TypeError): raise RealEstateError('archive_chunk_encoding') from None
        return decode_object(encoded, digest, size)

    def head(self):
        rows = self.query(self.control, "SELECT digest,bytes FROM backup_heads WHERE name='collector'")['results']
        return {'sha256': rows[0]['digest'], 'bytes': rows[0]['bytes']} if rows else None

    def promote(self, descriptor, expected, *, lease=None):
        # A background collector fences every backup-head writer, including
        # manual backups. A prior or expired owner cannot publish new state.
        condition = 'NOT EXISTS(SELECT 1 FROM collection_owner WHERE owner IS NOT NULL)'
        ownership = []
        if lease is not None:
            condition = "EXISTS(SELECT 1 FROM collection_owner WHERE id=1 AND owner=? AND generation=? AND expires>CAST(strftime('%s','now') AS INTEGER))"
            ownership = [lease['owner'],lease['generation']]
        if expected is None:
            result = self.query(self.control, "INSERT OR IGNORE INTO backup_heads(name,digest,bytes) SELECT 'collector',?,? WHERE "+condition, [descriptor['sha256'],descriptor['bytes'],*ownership])
        else:
            result = self.query(self.control, "UPDATE backup_heads SET digest=?,bytes=? WHERE name='collector' AND digest=? AND bytes=? AND "+condition, [descriptor['sha256'],descriptor['bytes'],expected['sha256'],expected['bytes'],*ownership])
        if result['meta']['changes'] != 1:
            raise RealEstateError('archive_head_changed')


def backup(root, store, progress=None, *, lease=None):
    root = Path(root).absolute(); _reject_links(root)
    _reject_links(root/'checkpoint.sqlite')
    expected = store.head()
    previous = {}
    if expected:
        old = validate_manifest(json.loads(store.get(expected['sha256'],expected['bytes'])))
        previous = {r['path']:r for r in old['files']}
        # Verify inherited packs once each, retaining the original pack addresses.
        # Inserting a new source file must not repack a decade of existing bytes.
        for digest,size in sorted({(r['object']['sha256'],r['object']['bytes']) for r in old['files']}):
            store.get(digest,size)
    rows = []
    with tempfile.TemporaryDirectory(prefix='korea-replay-checkpoint-') as temporary:
        copy = Path(temporary) / 'checkpoint.sqlite'
        with closing(sqlite3.connect((root/'checkpoint.sqlite').as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(copy)) as dest:
            source.backup(dest)
        # Audit this exact snapshot, not a later state of the source database.
        if progress: progress({'phase':'audit-checkpoint'})
        audit = audit_checkpoint(root, copy)
        paths = [(copy, 'checkpoint.sqlite')]
        for prefix in sorted(PREFIXES):
            directory = root/prefix
            if not directory.exists(): continue
            _reject_links(directory)
            for path in sorted(directory.rglob('*')):
                _reject_links(path)
                if path.is_file(): paths.append((path, checked_path(path.relative_to(root).as_posix())))
        if len(paths) > MAX_FILES: raise RealEstateError('archive_total_limit')
        if not set(previous) <= {name for _,name in paths}:
            raise RealEstateError('archive_previous_files_missing')
        if progress: progress({'phase':'upload','total_files':len(paths)})
        total = 0; pack = bytearray(); pack_rows = []
        def flush():
            if not pack: return
            descriptor = store.put(bytes(pack))
            rows.extend({**row,'object':descriptor} for row in pack_rows)
            pack.clear(); pack_rows.clear()
        for i,(path,name) in enumerate(paths):
            if not 0 < path.stat().st_size <= MAX_FILE: raise RealEstateError('archive_file_limit')
            raw = path.read_bytes(); total += len(raw)
            if total > MAX_TOTAL: raise RealEstateError('archive_total_limit')
            if name != 'checkpoint.sqlite' and not Path(name).name.startswith(sha256(raw)+'.'):
                raise RealEstateError('archive_content_address')
            old = previous.get(name)
            if old and old['sha256']==sha256(raw) and old['bytes']==len(raw):
                rows.append(old)
                continue
            if len(pack)+len(raw)>PACK_BYTES: flush()
            pack_rows.append({'path':name,'sha256':sha256(raw),'bytes':len(raw),'offset':len(pack)})
            pack.extend(raw)
            if progress and ((i+1)%100 == 0): progress({'packed_files':i+1,'uploaded_verified_files':len(rows),'total_files':len(paths)})
        flush()
        manifest = validate_manifest({'schema_version':1,'kind':'private-collector-backup','files':rows,'audit':audit,'parent':expected})
        descriptor = store.put(canonical_bytes(manifest))
        store.promote(descriptor, expected, lease=lease)
    return {'backup':descriptor,'files':len(rows),'source_bytes':total,'audit':audit,'public_release':False}


def restore(target, store, descriptor=None, progress=None):
    target = Path(target).absolute(); _reject_links(target)
    if target.exists(): raise RealEstateError('archive_restore_requires_new_directory')
    descriptor = descriptor or store.head()
    if descriptor is None: raise RealEstateError('archive_no_backup')
    manifest = validate_manifest(json.loads(store.get(descriptor['sha256'],descriptor['bytes'])))
    target.mkdir(parents=True)
    # Interrupted restores stay isolated and inspectable; never delete user files.
    groups = {}
    for row in manifest['files']:
        obj = row['object']; groups.setdefault((obj['sha256'],obj['bytes']),[]).append(row)
    restored=0
    for (digest,size),rows in groups.items():
        pack = store.get(digest,size)
        for row in rows:
            body = pack[row['offset']:row['offset']+row['bytes']]
            if len(body)!=row['bytes'] or sha256(body)!=row['sha256']: raise RealEstateError('archive_file_hash')
            destination = target/row['path']; _reject_links(destination)
            destination.parent.mkdir(parents=True,exist_ok=True)
            with destination.open('xb') as stream: stream.write(body)
        restored+=len(rows)
        if progress:progress({'phase':'restore','verified_files':restored,'total_files':len(manifest['files'])})
    if progress:progress({'phase':'audit-restored-checkpoint'})
    audit = audit_checkpoint(target)
    if audit != manifest['audit']: raise RealEstateError('archive_restored_audit_changed')
    return {'restored':descriptor,'files':len(manifest['files']),'audit':audit,'source_calls':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['initialize','backup','restore','status'])
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--root',type=Path)
    args=parser.parse_args()
    try:
        _reject_links(args.config.absolute())
        store=D1Archive(json.loads(args.config.read_text(encoding='utf-8-sig')))
        if args.command=='initialize': store.initialize(); result={'initialized':True}
        elif args.command=='status': result={'head':store.head()}
        elif args.root is None: raise RealEstateError('archive_root_required')
        elif args.command=='backup': result=backup(args.root,store,lambda p:print(json.dumps(p),flush=True))
        else: result=restore(args.root,store,progress=lambda p:print(json.dumps(p),flush=True))
        print(json.dumps(result,ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        parser.exit(1,'real_estate_archive: '+(error.code if isinstance(error,RealEstateError) else 'invalid_input')+'\n')


if __name__=='__main__': main()
