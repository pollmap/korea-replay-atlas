import sqlite3
import pytest
from pipeline.real_estate import RealEstateError
from pipeline.real_estate_local_archive_set import LocalArchiveSet,backup_set
from pipeline.property_backup_idle import reuse,fingerprint
from test_vps_runtime import acquired

def backed(tmp_path):
 data,root=acquired(tmp_path);store=LocalArchiveSet(tmp_path/'archive',reserve_bytes=0);backup_set(root,store)
 return data,root,store

def test_bootstrap_restores_only_checkpoint_then_reuses_verified_head(tmp_path,monkeypatch):
 _,root,store=backed(tmp_path);old=store.head();first=reuse(root,store)
 assert first['status']=='unchanged' and first['raw_reparsed']==0 and first['checkpoint_restored']
 from pipeline import property_backup_idle as idle
 monkeypatch.setattr(idle,'load_set',lambda *args:pytest.fail('unchanged checkpoint rebuilt archive indexes'))
 second=reuse(root,store);assert not second['checkpoint_restored'];assert store.head()==old

def test_lease_changes_are_not_source_changes(tmp_path):
 _,root,store=backed(tmp_path);before=fingerprint(root/'checkpoint.sqlite');reuse(root,store)
 with sqlite3.connect(root/'checkpoint.sqlite') as db:db.execute("INSERT OR REPLACE INTO lease VALUES(1,'temporary-owner',1)")
 assert fingerprint(root/'checkpoint.sqlite')==before;assert reuse(root,store)['status']=='unchanged'

@pytest.mark.parametrize('mutation',["UPDATE meta SET value='changed' WHERE key='planning_months'", "UPDATE jobs SET status='pending' WHERE status='empty'", "UPDATE calls SET day='2000-01-01'", "UPDATE snapshots SET retrieved_at='2000-01-01T00:00:00Z'"])
def test_any_real_checkpoint_change_requires_backup(tmp_path,mutation):
 _,root,store=backed(tmp_path);reuse(root,store)
 with sqlite3.connect(root/'checkpoint.sqlite') as db:db.execute(mutation)
 assert reuse(root,store) is None

def test_missing_head_does_not_skip_initial_backup(tmp_path):
 _,root=acquired(tmp_path);store=LocalArchiveSet(tmp_path/'archive',reserve_bytes=0)
 assert reuse(root,store) is None

def test_corrupted_backup_cannot_become_an_idle_success(tmp_path):
 _,root,store=backed(tmp_path);(store.root/'set-head.json').write_text('{}')
 with pytest.raises(RealEstateError):reuse(root,store)

def test_closed_backup_and_active_wal_reader_have_same_logical_rows(tmp_path):
 _,root,store=backed(tmp_path)
 with sqlite3.connect(root/'checkpoint.sqlite') as db:
  db.execute('PRAGMA journal_mode=WAL');assert reuse(root,store)['status']=='unchanged'

# A cache budget must not become a new collection/storage stop condition.
def test_checkpoint_outgrowing_cache_falls_back_to_existing_backup(tmp_path,monkeypatch):
 _,root,store=backed(tmp_path)
 from pipeline import property_backup_idle as idle
 monkeypatch.setattr(idle,'MAX_CHECKPOINT',1)
 assert idle.reuse(root,store) is None
 assert idle.remember(root,store,store.head()) is False


@pytest.mark.parametrize('requests',[0,1])
def test_runtime_reuses_only_zero_request_zero_retry_backups(tmp_path,monkeypatch,requests):
 _,root,store=backed(tmp_path)
 from pipeline import vps_runtime as runtime
 from collections import namedtuple
 Usage=namedtuple('Usage','total used free')
 monkeypatch.setattr(runtime.shutil,'disk_usage',lambda _:Usage(10**12,0,10**12))
 monkeypatch.setattr(runtime,'read_key',lambda _: 'fixture')
 monkeypatch.setattr(runtime,'requeue_safe_failures',lambda *args,**kwargs:{'requeued':0})
 class FakeCollector:
  def __init__(self,*args,**kwargs):self.db=sqlite3.connect(root/'checkpoint.sqlite')
  def collect(self,*args,**kwargs):return {'requests':requests,'stop_reason':'work_complete'}
  def close(self):self.db.close()
 monkeypatch.setattr(runtime,'Collector',FakeCollector)
 calls=[]
 def backup(*args,**kwargs):
  calls.append(1);return {'status':'verified','head':store.head()}
 monkeypatch.setattr(runtime,'backup',backup)
 result=runtime._collect_once(root,store.root,tmp_path/'secret')
 assert len(calls)==requests
 assert result['backup']['status']==('verified' if requests else 'unchanged')
