import sqlite3

import pytest

from pipeline.real_estate import RealEstateError
from pipeline.real_estate_run_guard import CollectionGuard, DAY, guarded_transport
from pipeline.real_estate_archive import backup
from test_real_estate_archive import LocalD1
from test_real_estate_fetch import collector, xml


def fixture():
    store=LocalD1();head=store.put(b'fixture-initial-backup');store.promote(head,None)
    guard=CollectionGuard(store);guard.initialize()
    return store,guard,head


def test_single_owner_and_no_automatic_expired_takeover():
    store,guard,head=fixture();lease=guard.acquire(head)
    with pytest.raises(RealEstateError,match='busy'):guard.acquire(head)
    store.query(store.control,'UPDATE collection_owner SET expires=0')
    with pytest.raises(RealEstateError,match='busy'):guard.acquire(head)
    with pytest.raises(RealEstateError,match='ownership_lost'):guard.renew(lease)
    with pytest.raises(sqlite3.IntegrityError,match='ownership_lost'):
        guard.reserve(lease,'sale','sale/11110/202608',1)
    assert guard.status()['today']==[]


def test_budget_is_atomic_separate_by_service_and_not_refunded():
    store,guard,head=fixture();lease=guard.acquire(head)
    store.query(store.control,f"INSERT INTO collection_budget VALUES({DAY},'sale',7999)")
    r=guard.reserve(lease,'sale','sale/11110/202608',1)
    guard.finish(lease,r['id'],error='upstream_timeout')
    with pytest.raises(sqlite3.IntegrityError,match='daily_budget'):
        guard.reserve(lease,'sale','sale/11110/202608',2)
    guard.reserve(lease,'rent','rent/11110/202608',1)
    assert guard.status()['today']==[{'trade':'rent','used':1},{'trade':'sale','used':8000}]
    assert store.query(store.control,'SELECT COUNT(*) AS n FROM collection_reservations')['results']==[{'n':2}]


def test_duplicate_id_does_not_charge_twice_or_authorize_another_request():
    _,guard,head=fixture();lease=guard.acquire(head)
    r=guard.reserve(lease,'sale','sale/11110/202608',1)
    with pytest.raises(RealEstateError,match='do_not_call'):
        guard.reserve(lease,'sale','sale/11110/202608',1,reservation_id=r['id'])
    assert guard.status()['today']==[{'trade':'sale','used':1}]


def test_raw_body_persisted_before_finish_and_new_head_required_before_release():
    store,guard,head=fixture();lease=guard.acquire(head)
    r=guard.reserve(lease,'sale','sale/11110/202608',1)
    with pytest.raises(RealEstateError,match='recovery_required'):guard.release(lease,head)
    descriptor=guard.finish(lease,r['id'],raw=b'<fixture/>')
    assert store.get(descriptor['sha256'],descriptor['bytes'])==b'<fixture/>'
    stamps=store.query(store.control,'SELECT reserved_at,finished_at FROM collection_reservations')['results'][0]
    assert 0<stamps['reserved_at']<=stamps['finished_at']
    with pytest.raises(RealEstateError,match='recovery_required'):guard.release(lease,head)
    next_head=store.put(b'fixture-new-backup')
    with pytest.raises(RealEstateError,match='head_changed'):store.promote(next_head,head)
    store.promote(next_head,head,lease=lease)
    guard.release(lease,next_head)
    assert guard.status()['owner'][0]['occupied']==0
    newer=guard.acquire(next_head)
    assert newer['generation']==lease['generation']+1
    with pytest.raises(RealEstateError,match='ownership_lost'):guard.renew(lease)
    with pytest.raises(RealEstateError,match='head_changed'):store.promote(store.put(b'late'),next_head,lease=lease)


def test_failed_raw_storage_leaves_charged_pending_reservation():
    store,guard,head=fixture();lease=guard.acquire(head)
    r=guard.reserve(lease,'sale','sale/11110/202608',1)
    def fail(raw):raise RealEstateError('fixture_storage_failure')
    store.put=fail
    with pytest.raises(RealEstateError,match='storage_failure'):guard.finish(lease,r['id'],raw=b'fixture')
    assert guard.status()['today'][0]['used']==1
    assert store.query(store.control,'SELECT phase FROM collection_reservations')['results']==[{'phase':'reserved'}]
    with pytest.raises(RealEstateError,match='recovery_required'):guard.release(lease,head)


def test_no_source_call_run_can_release_original_head_and_wrong_head_cannot_acquire():
    store,guard,head=fixture()
    with pytest.raises(RealEstateError,match='backup_changed'):guard.acquire(store.put(b'stale'))
    lease=guard.acquire(head);guard.renew(lease);guard.release(lease,head)
    assert guard.status()['owner'][0]['occupied']==0


@pytest.mark.parametrize('trade,job,page',[('sale','rent/11110/202608',1),('sale','sale/11110/202613',1),('sale','sale/11110/202608',True),('rent','rent/11110/202608',0)])
def test_invalid_request_never_reserves(trade,job,page):
    _,guard,head=fixture();lease=guard.acquire(head)
    with pytest.raises(RealEstateError,match='invalid_request'):guard.reserve(lease,trade,job,page)
    assert guard.status()['today']==[]


def test_collector_uses_remote_reservation_and_persists_raw_before_local_success(tmp_path):
    root=tmp_path/'source';c=collector(root,lambda *a,**kw:xml());c.close()
    store=LocalD1();backup(root,store);guard=CollectionGuard(store);guard.initialize();lease=guard.acquire(store.head())
    calls=[]
    def transport(*args,**kw):
        assert guard.status()['today'][0]['used']==1
        calls.append(1);return xml()
    c=collector(root,guarded_transport(guard,lease,transport));report=c.collect('fixture-key',max_requests=1,min_interval=0);c.close()
    assert len(calls)==1 and report['coverage']['empty']==1
    row=store.query(store.control,'SELECT phase,raw_digest,raw_bytes FROM collection_reservations')['results'][0]
    assert row['phase']=='stored' and store.get(row['raw_digest'],row['raw_bytes'])==xml()
    result=backup(root,store,lease=lease);guard.release(lease,result['backup'])
    assert guard.status()['owner'][0]['occupied']==0


def test_remote_storage_failure_stops_collector_without_another_api_call(tmp_path):
    store,guard,head=fixture();lease=guard.acquire(head);calls=[]
    def transport(*args,**kw):calls.append(1);return xml()
    def fail(raw):raise RealEstateError('fixture_storage_failure')
    store.put=fail
    c=collector(tmp_path/'source',guarded_transport(guard,lease,transport))
    report=c.collect('fixture-key',max_requests=5,min_interval=0);c.close()
    assert report['stop_reason']=='remote_checkpoint_error' and len(calls)==1
    assert report['coverage']['empty']==0 and guard.status()['today'][0]['used']==1


def test_reflected_key_is_never_persisted_to_remote_storage(tmp_path):
    store,guard,head=fixture();lease=guard.acquire(head)
    c=collector(tmp_path/'source',guarded_transport(guard,lease,lambda *a,**k:b'<fixture>fixture-key</fixture>'))
    report=c.collect('fixture-key',max_requests=2,min_interval=0);c.close()
    assert report['stop_reason']=='secret_reflection'
    assert store.query(store.control,'SELECT phase,raw_digest FROM collection_reservations')['results']==[{'phase':'failed','raw_digest':None}]
