from contextlib import closing
from datetime import timedelta
import json
import sqlite3

import pytest

from pipeline import property_refresh_scheduler as refresh, vps_runtime
from pipeline.real_estate import RealEstateError, utc_instant
from pipeline.real_estate_fetch import Collector
from pipeline.real_estate_archive import audit_checkpoint
from pipeline.real_estate_local_archive_set import LocalArchiveSet, backup_set, restore_set
from pipeline.property_backup_idle import fingerprint, remember, reuse
from test_real_estate_fetch import collector, registry, xml, rent, KEY, STAMP


def prepared(tmp_path):
    clock = [STAMP]
    c = collector(tmp_path, lambda *a, **kw: xml(), clock=lambda: clock[0])
    c.collect(KEY, max_requests=2, min_interval=0)
    clock[0] = '2026-09-21T00:00:00Z'
    return c, clock


def jobs(db):
    return [tuple(row) for row in db.execute('SELECT * FROM jobs ORDER BY id')]


def batch(c, clock, trade='sale', budget=500):
    return refresh.plan(c.db, clock[0], scope='nationwide', trades=(trade,), max_requests=budget)


def run(c, choice, **kwargs):
    return c.collect(KEY, correction=choice, min_interval=0, **kwargs)


def test_success_replaces_only_after_full_pages_and_keeps_old_snapshot(tmp_path):
    c, clock = prepared(tmp_path)
    original = jobs(c.db); old_snapshots = c.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    def source(key, trade, code, month, page, size, **kwargs):
        assert jobs(c.db) == original
        return xml([rent()] if page == 1 else [dict(rent(), floor='3')], page=page, size=1, total=2)
    c.transport = source
    first = run(c, batch(c, clock, 'rent', 1), max_requests=1, page_size=1)
    assert first['requests'] == 1 and jobs(c.db) == original
    assert first['correction']['changed_jobs']==0 and first['correction']['content_revision']==0
    assert c.db.execute("SELECT status FROM property_correction_queue").fetchone()[0] == 'partial'
    # Actual checkpoint backup/restore preserves partial correction pages and the
    # old completed transaction snapshot, rather than treating a refresh as missing.
    store = LocalArchiveSet(tmp_path / 'cas')
    saved = backup_set(c.root, store)
    assert saved['audit']['verified_references'] >= 4
    c.close()
    target = tmp_path / 'restored'
    restore_set(target, store)
    c = Collector(target, registry(), months=1, clock=lambda:clock[0], transport=source, reserve_bytes=0)
    assert jobs(c.db) == original
    second = run(c, batch(c, clock, 'rent'), page_size=1)
    assert second['requests'] == 1
    assert second['correction']['changed_jobs']==1 and second['correction']['content_revision']==1
    row = c.db.execute("SELECT status,pages,snapshot FROM jobs WHERE trade_type='rent'").fetchone()
    assert row['status'] == 'complete' and len(json.loads(row['pages'])) == 2
    assert c.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == old_snapshots + 1
    assert refresh.plan(c.db, clock[0], scope='nationwide', trades=('rent',)) is None
    assert audit_checkpoint(target)['verified_references'] >= 4
    c.close()


def test_failed_refresh_never_changes_completed_job_and_retries_are_durable(tmp_path):
    c, clock = prepared(tmp_path); original=jobs(c.db)
    def timeout(*args, **kwargs): raise RealEstateError('upstream_timeout')
    c.transport = timeout
    result=run(c,batch(c,clock));assert result['requests']==1 and jobs(c.db)==original
    assert batch(c,clock) is None
    for number in range(1,4):
        clock[0]=(utc_instant(clock[0])+timedelta(minutes=31)).strftime('%Y-%m-%dT%H:%M:%SZ')
        choice=batch(c,clock);assert choice['lane']=='retry'
        run(c,choice)
        assert jobs(c.db)==original
        assert c.db.execute('SELECT retries FROM property_correction_queue').fetchone()[0]==number
    clock[0]='2026-09-21T12:00:00Z'
    assert batch(c,clock) is None  # fourth same-day retry is never scheduled
    c.close()


def test_source_unavailable_outside_scope_and_auth_failures_never_requeue(tmp_path):
    c,clock=prepared(tmp_path)
    with c.db:
        c.db.execute("INSERT INTO jobs SELECT 'rent/11110/200601','rent','11110','200601',99,'source_unavailable','before_source_start','[]',NULL,NULL")
        c.db.execute("INSERT INTO jobs SELECT 'sale/99999/202609','sale','99999','202609',0,'empty',NULL,pages,snapshot,updated_at FROM jobs WHERE trade_type='sale'")
    choice=refresh.plan(c.db,clock[0],scope='priority-nine')
    assert all('99999' not in v and '200601' not in v for v in choice['job_ids'])
    original=jobs(c.db)
    c.transport=lambda *a,**kw: (_ for _ in ()).throw(RealEstateError('upstream_auth'))
    run(c,choice)
    assert jobs(c.db)==original
    clock[0]='2026-09-24T00:00:00Z'
    next_choice=refresh.plan(c.db,clock[0],scope='priority-nine',trades=('sale',))
    assert next_choice is None
    assert refresh.review_required(c.db)=={'upstream_auth':1}
    assert refresh.idle_report(c)['correction']['review_required']=={'upstream_auth':1}
    c.close()


def test_idle_same_kst_day_makes_no_queue_or_backup_and_next_day_becomes_due(tmp_path):
    c,clock=prepared(tmp_path)
    clock[0]=STAMP
    store=LocalArchiveSet(tmp_path/'cas');saved=backup_set(c.root,store);remember(c.root,store,store.head())
    before=fingerprint(c.root/'checkpoint.sqlite')
    for _ in range(3): assert batch(c,clock) is None
    assert not refresh._exists(c.db,refresh.QUEUE)
    assert fingerprint(c.root/'checkpoint.sqlite')==before
    assert reuse(c.root,store)['status']=='unchanged' and store.head()==saved['backup_set']
    clock[0]='2026-09-20T15:00:00Z' # midnight KST, still same UTC date
    assert batch(c,clock)['day']=='2026-09-21'
    c.close()


def test_rolling_history_cadence_and_first_activation_bounded(tmp_path):
    c=Collector(tmp_path,registry(),months=241,clock=lambda:STAMP,reserve_bytes=0)
    # A planner fixture needs only immutable descriptor identity, not source calls.
    with c.db:
        c.db.execute("UPDATE jobs SET status='empty',snapshot='{}',updated_at='2026-06-01T00:00:00Z' WHERE status='pending'")
    original=jobs(c.db)
    recent=refresh.plan(c.db,STAMP,scope='nationwide')
    assert recent['lane']=='recent' and len(recent['job_ids'])<=50
    with c.db:c.db.execute("UPDATE jobs SET updated_at=? WHERE deal_month>='202607'",(STAMP,))
    rolling=refresh.plan(c.db,STAMP,scope='nationwide')
    assert rolling['lane']=='rolling'
    with c.db:c.db.execute("UPDATE jobs SET updated_at=? WHERE deal_month>='202410'",(STAMP,))
    history=refresh.plan(c.db,STAMP,scope='nationwide')
    assert history['lane']=='history' and len(history['job_ids'])==50
    assert all(j[5]!='pending' for j in jobs(c.db))
    assert len(jobs(c.db))==len(original)
    c.close()


def seed_calls(db, trade, count, *, day='2026-09-21', bucket=None, error=None):
    refresh.initialize(db)
    with db:
        for _ in range(count):
            value=db.execute('INSERT INTO calls(day,trade_type,job_id,page_no,started_at,status,error_code) VALUES (?,?,?,?,?,?,?)',
                (day,trade,trade+'/11110/202609',1,day+'T00:00:00Z','failed' if error else 'validated',error))
            if bucket:db.execute('INSERT INTO property_correction_calls VALUES (?,?)',(value.lastrowid,bucket))


def test_all_lanes_and_retries_share_8000_reservation_limit_and_other_trade_continues(tmp_path):
    c,clock=prepared(tmp_path)
    seed_calls(c.db,'sale',7999,bucket='history')
    choice=batch(c,clock);assert choice['max_requests']==1
    run(c,choice)
    assert c.db.execute("SELECT COUNT(*) FROM calls WHERE day='2026-09-21' AND trade_type='sale'").fetchone()[0]==8000
    assert batch(c,clock) is None
    assert batch(c,clock,'rent') is not None
    c.close()


def test_reservation_blocks_budget_race_and_midnight_before_transport(tmp_path):
    c,clock=prepared(tmp_path); choice=batch(c,clock)
    seed_calls(c.db,'sale',8000,bucket='recent')
    c.transport=lambda *a,**kw:pytest.fail('full reservation ledger called source')
    assert run(c,choice)['requests']==0
    assert jobs(c.db)[1][5]=='empty'
    # Frozen day cannot borrow yesterday's unused lane allocation after midnight.
    clock[0]='2026-09-22T00:00:00Z'
    with pytest.raises(RealEstateError,match='correction_day_changed'):run(c,choice)
    c.close()


def test_quota_response_blocks_only_that_service_for_same_kst_day(tmp_path):
    c,clock=prepared(tmp_path)
    seed_calls(c.db,'sale',1,error='upstream_quota')
    assert vps_runtime.available_trades(c.db,day='2026-09-21')==['rent']
    assert vps_runtime.available_trades(c.db,day='2026-09-22')==['sale','rent']
    c.close()


def test_lane_reserves_and_unused_budget_reallocation(tmp_path):
    c,clock=prepared(tmp_path)
    with c.db:
        c.db.execute("INSERT INTO jobs SELECT 'sale/11110/202001','sale','11110','202001',80,'empty',NULL,pages,snapshot,'2026-01-01T00:00:00Z' FROM jobs WHERE trade_type='sale'")
    # No retries due: their 800 calls are available, but historical's 1600 stay reserved.
    assert batch(c,clock)['daily_lane_cap']==6400
    seed_calls(c.db,'sale',6400,bucket='recent')
    chosen=batch(c,clock)
    assert chosen['lane']=='history' and chosen['max_requests']<=500
    assert chosen['daily_lane_cap']==2400
    c.close()


def test_partial_correction_reference_is_in_backup_closure(tmp_path):
    c,clock=prepared(tmp_path)
    c.transport=lambda *a,**kw:xml([rent()],size=1,total=2)
    run(c,batch(c,clock,'rent',1),max_requests=1,page_size=1)
    row=c.db.execute('SELECT pages FROM property_correction_queue').fetchone()
    path=c.root/json.loads(row[0])[0]['path']
    path.write_bytes(b'corrupt')
    with pytest.raises(RealEstateError):audit_checkpoint(c.root)
    c.close()


def test_identical_pages_preserve_snapshot_and_skip_normalizer_after_verified_receipt(tmp_path,monkeypatch):
    c,clock=prepared(tmp_path)
    original=jobs(c.db)
    payloads=lambda:{str(p.relative_to(c.root)):p.read_bytes() for folder in ('raw','snapshots','changes')
                     for p in (c.root/folder).rglob('*') if p.is_file()}
    before=payloads()
    store=LocalArchiveSet(tmp_path/'cas');backup_set(c.root,store)
    previous_head=store.head()
    assert run(c,batch(c,clock))['requests']==1
    assert jobs(c.db)==original and payloads()==before
    assert batch(c,clock) is None
    # Quota and last-check receipts must survive backup, but no historical raw or
    # normalized payload may be re-created merely for a new collection timestamp.
    receipt=backup_set(c.root,store)
    assert receipt['checkpoint_changed_bytes']>0 and store.head()!=previous_head
    clock[0]='2026-09-22T00:00:00Z'
    monkeypatch.setattr(c,'_partition',lambda *a:pytest.fail('unchanged code+SHA reparsed historical payload'))
    monkeypatch.setattr(c,'_snapshot',lambda *a:pytest.fail('unchanged payload regenerated normalized snapshot'))
    run(c,batch(c,clock))
    assert jobs(c.db)==original and payloads()==before
    assert c.db.execute('SELECT checked_at FROM property_correction_queue').fetchone()[0]==clock[0]
    c.close()


def test_normalizer_change_invalidates_receipt_and_proves_original_again(tmp_path,monkeypatch):
    c,clock=prepared(tmp_path);run(c,batch(c,clock));clock[0]='2026-09-22T00:00:00Z'
    with c.db:c.db.execute("UPDATE property_correction_queue SET normalizer_sha='old-normalizer'")
    original=c._partition;seen=[]
    def checked(*args):seen.append(1);return original(*args)
    monkeypatch.setattr(c,'_partition',checked)
    run(c,batch(c,clock))
    assert len(seen)==1
    c.close()


def test_vps_hook_disabled_then_enabled_and_idle_skips_key_and_backup(tmp_path,monkeypatch):
    from collections import namedtuple
    c,clock=prepared(tmp_path/'data'/'collector');c.close()
    root=tmp_path/'data'/'collector';store=LocalArchiveSet(tmp_path/'cas');backup_set(root,store);remember(root,store,store.head())
    created=[]
    def factory(root,registered,**kwargs):
        inst=Collector(root,registered,months=1,clock=lambda:clock[0],transport=lambda *a,**kw:xml(),reserve_bytes=0)
        created.append(inst);return inst
    Usage=namedtuple('Usage','total used free')
    monkeypatch.setattr(vps_runtime,'Collector',factory)
    monkeypatch.setattr(vps_runtime.shutil,'disk_usage',lambda _:Usage(10**12,0,10**12))
    monkeypatch.setattr(vps_runtime,'instant',lambda:clock[0])
    monkeypatch.setattr(vps_runtime,'read_key',lambda _:KEY)
    monkeypatch.delenv(refresh.ENABLE,raising=False)
    disabled=vps_runtime._collect_once(root,store.root,tmp_path/'key')
    assert disabled['collection']['requests']==0
    monkeypatch.setenv(refresh.ENABLE,'1')
    enabled=vps_runtime._collect_once(root,store.root,tmp_path/'key')
    assert enabled['collection']['requests']==1 and enabled['collection']['correction']['lane']=='recent'
    # Other source still due: one more bounded cycle, then no source or full backup.
    vps_runtime._collect_once(root,store.root,tmp_path/'key')
    monkeypatch.setattr(vps_runtime,'read_key',lambda _:pytest.fail('not-due refresh read provider credential'))
    monkeypatch.setattr(vps_runtime,'backup',lambda *a,**kw:pytest.fail('unchanged cycle rebuilt whole backup'))
    final=vps_runtime._collect_once(root,store.root,tmp_path/'key')
    assert final['collection']['requests']==0 and final['backup']['status']=='unchanged'


def test_retired_unchanged_payloads_reused_but_new_sources_verified(tmp_path,monkeypatch):
    from pipeline.real_estate_working_store import migrate, ArchivedFile
    from pipeline.real_estate_fetch import immutable
    c,clock=prepared(tmp_path/'data'/'collector');original=jobs(c.db)
    store=LocalArchiveSet(tmp_path/'cas');backup_set(c.root,store)
    retired=migrate(c.root,store,limit=100,retire_plaintext=True,readers_deployed=True)
    assert retired['retired']>0
    run(c,batch(c,clock))
    assert jobs(c.db)==original
    monkeypatch.setattr(ArchivedFile,'read_bytes',lambda self:pytest.fail('incremental backup expanded retired unchanged payload'))
    receipt=backup_set(c.root,store,reuse_archived=True)
    assert receipt['space']['retired_files_reused']==retired['retired']
    assert receipt['space']['retired_source_bytes_reused']==retired['retired_logical_bytes']
    assert receipt['checkpoint_changed_bytes']>0
    from pipeline.real_estate import sha256
    payload=xml()+b'<!-- new-source -->'
    row=immutable(c.root,'raw/sale/11110/202609/'+sha256(payload)+'.xml',payload)
    (c.root/row['path']).write_bytes(b'bad')
    head=store.head()
    with pytest.raises(RealEstateError,match='archive_content_address'):
        backup_set(c.root,store,reuse_archived=True)
    assert store.head()==head
    c.close()


def test_full_audit_option_reads_retired_sources_and_missing_object_blocks_reuse(tmp_path,monkeypatch):
    from pipeline.real_estate_working_store import migrate, ArchivedFile
    c,clock=prepared(tmp_path/'data'/'collector');store=LocalArchiveSet(tmp_path/'cas')
    backup_set(c.root,store);migrate(c.root,store,limit=100,retire_plaintext=True,readers_deployed=True)
    original=ArchivedFile.read_bytes;reads=[]
    def observed(self):reads.append(1);return original(self)
    monkeypatch.setattr(ArchivedFile,'read_bytes',observed)
    backup_set(c.root,store)
    assert reads
    from pipeline.real_estate_local_archive_set import load_set
    row=next(r for r in load_set(store,store.head())['files'] if r['path'].startswith('raw/'))
    path=store._path(row['object']['sha256'])
    if not path.exists():path=path.with_suffix('.encoded')
    path.unlink() # isolated fixture simulates missing protected archive
    head=store.head()
    with pytest.raises(RealEstateError,match='archive_object_missing'):
        backup_set(c.root,store,reuse_archived=True)
    assert store.head()==head
    c.close()


def test_calls_delta_does_not_decode_warm_unchanged_raw_packs(tmp_path,monkeypatch):
    from pipeline.real_estate_working_store import migrate
    from pipeline.real_estate_local_archive_set import load_set
    c,clock=prepared(tmp_path/'data'/'collector');store=LocalArchiveSet(tmp_path/'cas')
    backup_set(c.root,store);migrate(c.root,store,limit=100,retire_plaintext=True,readers_deployed=True)
    backup_set(c.root,store,reuse_archived=True) # one cold receipt validation
    raw_packs={r['object']['sha256'] for r in load_set(store,store.head())['files'] if r['path'].startswith(('raw/','snapshots/'))}
    run(c,batch(c,clock))
    original=store.get
    def guard(digest,size):
        assert digest not in raw_packs, 'unchanged source pack decoded for calls-only delta'
        return original(digest,size)
    monkeypatch.setattr(store,'get',guard)
    result=backup_set(c.root,store,reuse_archived=True)
    assert result['checkpoint_changed_bytes']>0 and result['space']['retired_files_reused']>0
    c.close()


def test_cache_checks_file_identity_and_validator_version(tmp_path,monkeypatch):
    from pipeline.real_estate_archive_verification import verification
    from pipeline import real_estate_archive_verification as checked
    store=LocalArchiveSet(tmp_path/'cas');row=store.put(b'test source '*1000)
    with verification(store,True) as check:check(row['sha256'],row['bytes'])
    original=store.get;calls=[]
    def observed(*args):calls.append(1);return original(*args)
    monkeypatch.setattr(store,'get',observed)
    with verification(store,True) as check:check(row['sha256'],row['bytes'])
    assert not calls
    monkeypatch.setattr(checked,'validator_version',lambda:'new-code')
    with verification(store,True) as check:check(row['sha256'],row['bytes'])
    assert len(calls)==1
    path=store._path(row['sha256'])
    if not path.exists():path=path.with_suffix('.encoded')
    payload=path.read_bytes();path.write_bytes(b'X'+payload[1:])
    with pytest.raises((RealEstateError,OSError)):
        with verification(store,True) as check:check(row['sha256'],row['bytes'])
    assert len(calls)==2


def test_worker_quota_wait_does_not_latch_permanent_hold(tmp_path,monkeypatch):
    from contextlib import nullcontext
    data=tmp_path/'data';data.mkdir()
    (data/'migration-verified.json').write_text('{"status":"verified","audit":{},"files":1}')
    (data/'collection-enabled').touch()
    monkeypatch.setattr(vps_runtime,'collector_lock',lambda path:nullcontext())
    monkeypatch.setattr(vps_runtime,'collect_once',lambda *a,**kw:{
        'collection':{'requests':1,'stop_reason':'upstream_quota'},'finished_at':STAMP})
    monkeypatch.setattr(vps_runtime.time,'sleep',lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):vps_runtime.worker(data,tmp_path/'cas',tmp_path/'key')
    assert not (data/'collection-hold.json').exists()
    assert json.loads((data/'worker-status.json').read_bytes())['state']=='waiting'


@pytest.mark.parametrize('requests,correction,expected',[
    (1,{'changed_jobs':0,'content_revision':0},False), # identical or failed
    (1,{'changed_jobs':1,'content_revision':1},True),
    (0,{'changed_jobs':0,'content_revision':1},True),  # restart after commit before publication
    (0,{'changed_jobs':0,'content_revision':0},False),
    (1,None,True), # legacy first acquisition retains its publication behavior
])
def test_worker_publishes_only_changed_correction_revision(tmp_path,monkeypatch,requests,correction,expected):
    from contextlib import nullcontext
    data=tmp_path/'data';data.mkdir()
    (data/'migration-verified.json').write_text('{"status":"verified","audit":{},"files":1}')
    (data/'collection-enabled').touch()
    (data/'read-model').mkdir();(data/'read-model/current.json').write_text('{}')
    (data/'read-model-status.json').write_text('{"state":"ready","generation":"old","correction_revision":0}')
    monkeypatch.setattr(vps_runtime,'collector_lock',lambda path:nullcontext())
    report={'requests':requests,'stop_reason':'work_complete'}
    if correction is not None:report['correction']=correction
    monkeypatch.setattr(vps_runtime,'collect_once',lambda *a,**kw:{'collection':report,'finished_at':STAMP})
    publications=[]
    monkeypatch.setattr(vps_runtime,'publish_read_model',lambda *a,**kw:publications.append(1) or {'generation':'new'})
    monkeypatch.setattr(vps_runtime,'retire_acknowledged_read_models',lambda *a:None)
    monkeypatch.setattr(vps_runtime.time,'sleep',lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):vps_runtime.worker(data,tmp_path/'cas',tmp_path/'key')
    assert bool(publications)==expected
    state=json.loads((data/'read-model-status.json').read_bytes())
    assert state['generation']==('new' if expected else 'old')
    if correction is not None:assert state['correction_revision']==correction['content_revision']
    assert not (data/'collection-hold.json').exists()


def test_cold_verification_resumes_committed_object_receipts(tmp_path,monkeypatch):
    from pipeline.real_estate_archive_verification import verification
    store=LocalArchiveSet(tmp_path/'cas')
    rows=[store.put((str(i)+' source '*40).encode()) for i in range(101)]
    with pytest.raises(RuntimeError):
        with verification(store,True) as check:
            for row in rows[:100]:check(row['sha256'],row['bytes'])
            raise RuntimeError('simulated interruption after receipt checkpoint')
    original=store.get;seen=[]
    def checked(digest,size):seen.append(digest);return original(digest,size)
    monkeypatch.setattr(store,'get',checked)
    with verification(store,True) as check:
        for row in rows:check(row['sha256'],row['bytes'])
    assert seen==[rows[-1]['sha256']]
