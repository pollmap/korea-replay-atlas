import json
import sqlite3

import pytest

from pipeline.real_estate import RealEstateError,sha256
from pipeline.real_estate_archive import backup,restore,PACK_BYTES
from pipeline.real_estate_remote import RemoteWorkspace,run_remote
from pipeline.real_estate_run_guard import CollectionGuard,DAY
from test_real_estate_archive import LocalD1
from test_real_estate_fetch import collector,xml,STAMP


def parent(tmp_path):
    root=tmp_path/'original';c=collector(root,lambda *a,**kw:xml())
    c.collect('fixture-key',max_requests=1,min_interval=0);c.close()
    store=LocalD1();backup(root,store)
    return root,store


def test_sparse_collection_preserves_parent_and_full_restore_matches(tmp_path):
    root,store=parent(tmp_path);before=store.head()
    result=run_remote(tmp_path/'runner',store,'fixture-key',months=2,as_of=STAMP,
        max_requests=1,transport=lambda *a,**kw:xml(),reserve_bytes=0)
    assert result['collection']['requests']==1 and result['baseline']['imported_calls']==1
    assert result['checkpoint']['audit']['calls']==2
    assert result['checkpoint']['hydrated_files']==2
    assert result['checkpoint']['new_or_changed_files']>=3 and store.head()!=before
    recovered=restore(tmp_path/'full-restore',store)
    assert recovered['audit']==result['checkpoint']['audit']
    assert all((tmp_path/'full-restore'/p.relative_to(root)).read_bytes()==p.read_bytes() for p in (root/'raw').rglob('*.xml'))
    guard=CollectionGuard(store)
    assert guard.status()['owner'][0]['occupied']==0


def test_unused_large_pack_is_not_downloaded_for_minimal_workspace(tmp_path):
    root,store=parent(tmp_path)
    raw=b'x'*PACK_BYTES;extra=root/'raw'/'unreferenced';extra.mkdir()
    (extra/(sha256(raw)+'.xml')).write_bytes(raw)
    backup(root,store)
    fetched=[];original_get=store.get
    def get(digest,size):fetched.append(size);return original_get(digest,size)
    store.get=get
    workspace=RemoteWorkspace(tmp_path/'minimal',store)
    assert workspace.hydrated_files==2
    assert workspace.downloaded_object_bytes<PACK_BYTES
    assert PACK_BYTES not in fetched
    assert not (workspace.root/'raw').exists()


def test_initial_budget_import_blocks_calls_until_complete_and_does_not_double_charge(tmp_path):
    _,store=parent(tmp_path);guard=CollectionGuard(store);guard.initialize();head=store.head();lease=guard.acquire(head)
    with pytest.raises(sqlite3.IntegrityError,match='baseline_required'):
        guard.reserve(lease,'sale','sale/11110/202609',1)
    day=store.query(store.control,f'SELECT {DAY} AS day')['results'][0]['day']
    counts=[{'day':day,'trade':'sale','used':7999}]
    assert guard.seed_budget(lease,head,counts)['imported_calls']==7999
    guard.reserve(lease,'sale','sale/11110/202609',1)
    assert guard.seed_budget(lease,head,counts)['already_imported'] is True
    assert guard.status()['today']==[{'trade':'sale','used':8000}]
    with pytest.raises(sqlite3.IntegrityError,match='daily_budget'):
        guard.reserve(lease,'sale','sale/11110/202609',2)


def test_partially_imported_counts_are_idempotent_and_conflict_is_rejected(tmp_path):
    _,store=parent(tmp_path);guard=CollectionGuard(store);guard.initialize();head=store.head();lease=guard.acquire(head)
    counts=[{'day':'2026-09-20','trade':'sale','used':6},{'day':'2026-09-21','trade':'rent','used':7}]
    query=guard.query;attempts=0
    def interrupt(sql,params=()):
        nonlocal attempts
        if sql.startswith('INSERT OR IGNORE INTO collection_baseline_items'):
            attempts+=1
            if attempts==2:raise RealEstateError('fixture_interrupted')
        return query(sql,params)
    guard.query=interrupt
    with pytest.raises(RealEstateError,match='fixture_interrupted'):guard.seed_budget(lease,head,counts)
    guard.query=query
    with pytest.raises(RealEstateError,match='baseline_conflict'):
        guard.seed_budget(lease,head,[{**counts[0],'used':5}])
    assert guard.seed_budget(lease,head,counts)['imported_calls']==13
    assert guard.query('SELECT SUM(used) AS n FROM collection_budget')['results'][0]['n']==13


def test_invalid_calendar_day_cannot_seed_budget(tmp_path):
    _,store=parent(tmp_path);guard=CollectionGuard(store);guard.initialize();head=store.head();lease=guard.acquire(head)
    with pytest.raises(RealEstateError,match='invalid_baseline'):
        guard.seed_budget(lease,head,[{'day':'2026-02-30','trade':'sale','used':1}])
    assert not guard.query('SELECT 1 FROM collection_baseline')['results']


def test_failed_remote_persistence_does_not_publish_or_release_owner(tmp_path):
    _,store=parent(tmp_path);before=store.head();original_put=store.put
    def put(raw):
        if raw==xml():raise RealEstateError('fixture_write_failure')
        return original_put(raw)
    store.put=put;calls=[]
    def transport(*a,**k):calls.append(1);return xml()
    with pytest.raises(RealEstateError,match='recovery_required'):
        run_remote(tmp_path/'runner',store,'fixture-key',months=2,as_of=STAMP,max_requests=2,transport=transport,reserve_bytes=0)
    assert len(calls)==1 and store.head()==before
    assert CollectionGuard(store).status()['owner'][0]['occupied']==1


def test_existing_destination_and_missing_parent_reference_are_rejected(tmp_path):
    root,store=parent(tmp_path)
    with pytest.raises(RealEstateError,match='requires_new_directory'):RemoteWorkspace(root,store)
    workspace=RemoteWorkspace(tmp_path/'runner',store)
    with pytest.raises(RealEstateError,match='not_in_parent'):workspace.hydrate(['raw/'+'0'*64+'.xml'])


def test_incomplete_parent_reference_cannot_become_new_head(tmp_path):
    _,store=parent(tmp_path);before=store.head()
    workspace=RemoteWorkspace(tmp_path/'runner',store)
    snapshot=next(name for name in workspace.files if name.startswith('snapshots/'))
    del workspace.files[snapshot]
    guard=CollectionGuard(store);guard.initialize();lease=guard.acquire(before)
    with pytest.raises(RealEstateError,match='archive_reference_hash'):
        workspace.publish(lease)
    assert store.head()==before
    guard.release(lease,before)
