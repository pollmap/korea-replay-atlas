"""Bounded remote-backed MOLIT collection with selective restoration.

Existing objects remain in the immutable parent manifest. Only accessed files
are hydrated and only new bytes are uploaded. This does not publish website data.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from .real_estate import RealEstateError, canonical_bytes, sha256, _reject_links
from .real_estate_archive import (D1Archive, validate_manifest, checked_path,
    audit_checkpoint, MAX_FILE, PREFIXES)
from .real_estate_fetch import Collector, read_key, fetch_page
from .real_estate_regions import load_registry
from .real_estate_run_guard import CollectionGuard, guarded_transport


class RemoteWorkspace:
    def __init__(self, root, store):
        self.root=Path(root).absolute();_reject_links(self.root)
        if self.root.exists():raise RealEstateError('remote_workspace_requires_new_directory')
        self.store=store;self.head=store.head()
        if not self.head:raise RealEstateError('archive_no_backup')
        self.manifest=validate_manifest(json.loads(store.get(self.head['sha256'],self.head['bytes'])))
        self.files={r['path']:r for r in self.manifest['files']}
        self.cached_key=None;self.cached_body=None;self.hydrated_bytes=0;self.hydrated_files=0;self.downloaded_object_bytes=0
        self.root.mkdir(parents=True)
        self.hydrate(['checkpoint.sqlite'])
        with closing(sqlite3.connect(self.root/'checkpoint.sqlite')) as db:
            if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise RealEstateError('archive_sqlite_integrity')
            if db.execute('SELECT 1 FROM lease WHERE expires>?',(time.time(),)).fetchone():raise RealEstateError('archive_collector_active')
            digest=db.execute("SELECT value FROM meta WHERE key='registry_sha256'").fetchone()[0]
            self.baseline_counts=[{'day':day,'trade':trade,'used':used} for day,trade,used in db.execute('SELECT day,trade_type,COUNT(*) FROM calls GROUP BY day,trade_type ORDER BY day,trade_type')]
        self.registry_path=f'registry/{digest}.json'
        self.hydrate([self.registry_path])

    def hydrate(self, names):
        for name in names:
            name=checked_path(name)
            row=self.files.get(name)
            if row is None:raise RealEstateError('remote_file_not_in_parent')
            path=self.root/name;_reject_links(path)
            if path.exists():
                raw=path.read_bytes()
                if len(raw)!=row['bytes'] or sha256(raw)!=row['sha256']:raise RealEstateError('remote_local_file_changed')
                continue
            obj=row['object'];key=(obj['sha256'],obj['bytes'])
            if key!=self.cached_key:
                self.cached_body=self.store.get(*key);self.cached_key=key
                self.downloaded_object_bytes+=len(self.cached_body)
            raw=self.cached_body[row['offset']:row['offset']+row['bytes']]
            if len(raw)!=row['bytes'] or sha256(raw)!=row['sha256']:raise RealEstateError('archive_file_hash')
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as stream:stream.write(raw)
            self.hydrated_files+=1;self.hydrated_bytes+=len(raw)

    def publish(self, lease, *, heartbeat=lambda: None):
        """Audit all references; verify new bytes without rereading all old packs."""
        self.cached_key=None;self.cached_body=None
        rows=dict(self.files);new_files=0;new_bytes=0
        with tempfile.TemporaryDirectory(prefix='korea-replay-remote-checkpoint-') as temporary:
            copy=Path(temporary)/'checkpoint.sqlite'
            _reject_links(self.root/'checkpoint.sqlite')
            with closing(sqlite3.connect((self.root/'checkpoint.sqlite').as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(copy)) as dest:
                source.backup(dest)
            paths=[(copy,'checkpoint.sqlite')]
            for prefix in sorted(PREFIXES):
                folder=self.root/prefix
                if not folder.exists():continue
                _reject_links(folder)
                for path in sorted(folder.rglob('*')):
                    _reject_links(path)
                    if path.is_file():paths.append((path,checked_path(path.relative_to(self.root).as_posix())))
            for path,name in paths:
                heartbeat()
                if not 0<path.stat().st_size<=MAX_FILE:raise RealEstateError('archive_file_limit')
                raw=path.read_bytes();digest=sha256(raw);before=rows.get(name)
                if before and before['sha256']==digest and before['bytes']==len(raw):continue
                if name!='checkpoint.sqlite' and (before or not Path(name).name.startswith(digest+'.')):
                    raise RealEstateError('archive_immutable_conflict')
                obj=self.store.put(raw)
                rows[name]={'path':name,'sha256':digest,'bytes':len(raw),'object':obj,'offset':0}
                new_files+=1;new_bytes+=len(raw)
            audit=audit_checkpoint(self.root,copy,descriptors=rows)
            manifest=validate_manifest({'schema_version':1,'kind':'private-collector-backup',
                'files':[rows[name] for name in sorted(rows)],'audit':audit,'parent':self.head})
            descriptor=self.store.put(canonical_bytes(manifest))
            heartbeat()
            self.store.promote(descriptor,self.head,lease=lease)
        return {'backup':descriptor,'audit':audit,'retained_parent_files':len(self.files),
            'new_or_changed_files':new_files,'new_or_changed_bytes':new_bytes,
            'hydrated_files':self.hydrated_files,'hydrated_bytes':self.hydrated_bytes,
            'downloaded_object_decoded_bytes':self.downloaded_object_bytes,
            'verification_scope':'new-bytes-and-immutable-parent-reference-closure','public_release':False}


class RemoteCollector(Collector):
    def __init__(self, workspace, **kwargs):
        self.workspace=workspace
        super().__init__(workspace.root,load_registry(workspace.root/workspace.registry_path),**kwargs)

    def _snapshot(self,job,pages):
        needed=[p['path'] for p in pages if not (self.root/p['path']).exists()]
        if job['snapshot']:
            name=json.loads(job['snapshot'])['path']
            if not (self.root/name).exists():needed.append(name)
        self.workspace.hydrate(needed)
        return super()._snapshot(job,pages)


def run_remote(root, store, key, *, max_requests=25,max_bytes=16*1024**2,
               months=121,as_of=None,collect_months=None,transport=fetch_page,reserve_bytes=2*1024**3):
    workspace=RemoteWorkspace(root,store)
    guard=CollectionGuard(store);guard.initialize();lease=guard.acquire(workspace.head)
    collector=None
    try:
        imported=guard.seed_budget(lease,workspace.head,workspace.baseline_counts)
        collector=RemoteCollector(workspace,as_of=as_of,months=months,advance_window=True,
            reserve_bytes=reserve_bytes,transport=guarded_transport(guard,lease,transport))
        report=collector.collect(key,max_requests=max_requests,max_bytes=max_bytes,collect_months=collect_months)
        collector.close();collector=None
        pending=guard.query("SELECT 1 FROM collection_reservations WHERE owner=? AND generation=? AND phase='reserved' LIMIT 1",[lease['owner'],lease['generation']])['results']
        if pending:raise RealEstateError('collection_recovery_required')
        guard.renew(lease)
        publication=workspace.publish(lease,heartbeat=lambda:guard.renew(lease))
        guard.release(lease,publication['backup'])
        return {'collection':report,'baseline':imported,'checkpoint':publication,'scheduled':False}
    except Exception:
        # Only a provably zero-request run may release without a new checkpoint.
        # Uncertain remote reads/writes leave ownership in place for recovery.
        try:
            any_request=guard.query('SELECT 1 FROM collection_reservations WHERE owner=? AND generation=? LIMIT 1',[lease['owner'],lease['generation']])['results']
            if not any_request:guard.release(lease,workspace.head)
        except (RealEstateError,sqlite3.Error):pass
        raise
    finally:
        if collector:collector.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--secret-file',type=Path)
    parser.add_argument('--max-requests',type=int,default=25)
    parser.add_argument('--max-bytes',type=int,default=16*1024**2)
    parser.add_argument('--collect-month',action='append')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    try:
        if not args.execute:raise RealEstateError('explicit_execution_required')
        key=read_key(args.secret_file)
        _reject_links(args.config.absolute())
        store=D1Archive(json.loads(args.config.read_text(encoding='utf-8-sig')))
        result=run_remote(args.root,store,key,max_requests=args.max_requests,max_bytes=args.max_bytes,collect_months=args.collect_month)
        print(json.dumps(result,ensure_ascii=False))
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
        parser.exit(1,'real_estate_remote: '+(error.code if isinstance(error,RealEstateError) else 'invalid_input')+'\n')


if __name__=='__main__':main()
