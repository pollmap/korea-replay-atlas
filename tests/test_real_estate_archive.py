from copy import deepcopy
import json
import sqlite3
import zlib

import pytest

from pipeline.real_estate import RealEstateError, sha256
from pipeline.real_estate_archive import (D1Archive, backup, restore, audit_checkpoint,
    checked_path, decode_object, validate_manifest, SHARD_CAP)
from test_real_estate_fetch import collector, xml, rent


class LocalD1(D1Archive):
    """Run the exact remote SQL against SQLite, including CAS and chunk queries."""
    def __init__(self):
        self.control='control'; self.shards=['0','1','2','3']; self.databases={}
        self.size=0
        for name in [self.control,*self.shards]:
            db=sqlite3.connect(':memory:'); db.row_factory=sqlite3.Row; self.databases[name]=db
        self.initialize()

    def query(self,database,sql,params=()):
        db=self.databases[database]; before=db.total_changes
        with db: cursor=db.execute(sql,params)
        return {'results':[dict(r) for r in cursor.fetchall()],
            'meta':{'changes':db.total_changes-before,'size_after':self.size}}


def source(tmp_path):
    root=tmp_path/'source'
    c=collector(root,lambda *a,**kw:xml([rent()]))
    c.collect('fixture-key',max_requests=1,min_interval=0)
    c.close()
    return root


def test_real_collector_roundtrip_preserves_jobs_raw_snapshots_and_calls(tmp_path):
    root=source(tmp_path); store=LocalD1()
    expected=audit_checkpoint(root)
    result=backup(root,store)
    assert result['audit']==expected and result['public_release'] is False
    restored=restore(tmp_path/'restored',store)
    assert restored['audit']==expected and restored['source_calls']==0
    assert len(list((tmp_path/'restored'/'raw').rglob('*.xml')))==1
    assert (root/'checkpoint.sqlite').exists()
    with pytest.raises(RealEstateError,match='requires_new_directory'):
        restore(root,store)


def test_missing_chunk_or_tampered_payload_never_restores_as_success(tmp_path):
    root=source(tmp_path); store=LocalD1(); backup(root,store)
    head=store.head(); db=store.databases[store.database(head['sha256'])]
    db.execute('DELETE FROM archive_chunks WHERE digest=?',[head['sha256']]); db.commit()
    with pytest.raises(RealEstateError,match='missing_chunk'):
        restore(tmp_path/'restored',store)
    assert not (tmp_path/'restored').exists()


def test_failed_upload_keeps_previous_head(tmp_path):
    root=source(tmp_path); store=LocalD1(); backup(root,store); before=store.head()
    original=store.put
    def fail(raw):
        original(raw)
        raise RealEstateError('simulated_interruption')
    store.put=fail
    with pytest.raises(RealEstateError,match='simulated_interruption'): backup(root,store)
    assert store.head()==before


def test_concurrent_publication_cannot_overwrite_new_head():
    store=LocalD1(); a=store.put(b'first'); b=store.put(b'second'); c=store.put(b'third')
    store.promote(a,None); store.promote(b,a)
    with pytest.raises(RealEstateError,match='head_changed'): store.promote(c,a)
    assert store.head()==b


def test_shard_cap_and_corruption_are_fail_closed():
    store=LocalD1(); store.size=SHARD_CAP
    with pytest.raises(RealEstateError,match='free_storage_limit'): store.put(b'x')
    store.size=0; ref=store.put(b'restore me')
    db=store.databases[store.database(ref['sha256'])]
    db.execute('UPDATE archive_chunks SET payload=? WHERE digest=?',['eA==',ref['sha256']]);db.commit()
    with pytest.raises(RealEstateError,match='archive_object'):store.put(b'restore me')


@pytest.mark.parametrize('path',['../secret','/raw/a.xml','C:/secret','raw/../secret','raw\\a.xml','.env','auth/'+64*'a'+'.json','raw//'+64*'a'+'.xml'])
def test_path_traversal_and_secrets_are_rejected(path):
    with pytest.raises(RealEstateError,match='archive_path'):checked_path(path)


def test_compressed_bomb_trailing_stream_and_bad_hash_rejected():
    data=b'x'*10000; encoded=zlib.compress(data)
    for body,digest,size in [(encoded,sha256(data),5),(encoded+b'extra',sha256(data),len(data)),(encoded,'0'*64,len(data))]:
        with pytest.raises(RealEstateError,match='archive_object'):decode_object(body,digest,size)


def test_manifest_duplicate_paths_and_outside_pack_rejected(tmp_path):
    root=source(tmp_path);store=LocalD1();backup(root,store);ref=store.head()
    m=json.loads(store.get(ref['sha256'],ref['bytes']))
    bad=deepcopy(m);bad['files'].append(deepcopy(bad['files'][0]))
    with pytest.raises(RealEstateError,match='archive_descriptor'):validate_manifest(bad)
    bad=deepcopy(m);bad['files'][0]['offset']=bad['files'][0]['object']['bytes']
    with pytest.raises(RealEstateError,match='archive_descriptor'):validate_manifest(bad)


def test_broken_local_reference_cannot_promote_backup(tmp_path):
    root=source(tmp_path);store=LocalD1()
    next((root/'raw').rglob('*.xml')).write_bytes(b'corrupt')
    with pytest.raises(RealEstateError,match='archive_reference_hash'):backup(root,store)
    assert store.head() is None


def test_incremental_backup_reuses_original_pack_when_files_are_inserted(tmp_path):
    root=source(tmp_path);store=LocalD1();backup(root,store);before=store.head()
    old=json.loads(store.get(before['sha256'],before['bytes']))
    raw=b'{"kind":"fixture-run"}\n'; folder=root/'runs';folder.mkdir(exist_ok=True)
    (folder/(sha256(raw)+'.json')).write_bytes(raw)
    backup(root,store);after=store.head()
    new=json.loads(store.get(after['sha256'],after['bytes']))
    old_files={r['path']:r for r in old['files']}; new_files={r['path']:r for r in new['files']}
    for name,row in old_files.items():
        if name!='checkpoint.sqlite':assert new_files[name]==row
    assert after!=before
    assert restore(tmp_path/'second-restore',store)['audit']==audit_checkpoint(root)


def test_long_object_crosses_remote_query_page_without_losing_chunks():
    import random
    raw=random.Random(4).randbytes(48*1024*33+17)
    store=LocalD1();ref=store.put(raw)
    assert store.get(ref['sha256'],ref['bytes'])==raw
    db=store.databases[store.database(ref['sha256'])]
    db.execute('DELETE FROM archive_chunks WHERE digest=? AND part=2',[ref['sha256']]);db.commit()
    with pytest.raises(RealEstateError,match='missing_chunk'):store.get(ref['sha256'],ref['bytes'])


def test_live_collector_cannot_be_registered_as_recoverable_backup(tmp_path):
    root=source(tmp_path);store=LocalD1()
    c=collector(root,lambda *a,**kw:xml());c._acquire();c.close()
    with pytest.raises(RealEstateError,match='collector_active'):backup(root,store)
    assert store.head() is None


def test_removed_historical_file_does_not_disappear_from_next_backup(tmp_path):
    root=source(tmp_path);store=LocalD1();backup(root,store);head=store.head()
    # Run reports are outside current job references, but must still be retained.
    report=next((root/'runs').glob('*.json'))
    report.rename(tmp_path/'report.saved')
    with pytest.raises(RealEstateError,match='archive_previous_files_missing'):backup(root,store)
    assert store.head()==head
