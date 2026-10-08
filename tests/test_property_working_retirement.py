from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from pipeline import property_working_retirement as auto
from pipeline import vps_runtime
from pipeline.bulk_work import bulk_work
from pipeline.real_estate import RealEstateError, canonical_bytes, sha256
from pipeline.real_estate_fetch import immutable
from pipeline.real_estate_local_archive_set import LocalArchiveSet, backup_set
from pipeline.real_estate_working_store import INDEX, READER_CONTRACT, migrate
from test_real_estate_fetch import xml
from test_vps_runtime import acquired


def setup(tmp_path, monkeypatch):
    data, root = acquired(tmp_path)
    store = LocalArchiveSet(tmp_path / 'cas'); backup_set(root, store)
    migrate(root, store, limit=1, retire_plaintext=True, readers_deployed=True)
    monkeypatch.setenv(auto.ENABLE, '1')
    monkeypatch.setenv('APP_BUILD', 'abcdef1')
    monkeypatch.delenv('KOREA_REPLAY_WORKING_ARCHIVE', raising=False)
    marker = {'schema_version': 1, 'reader_contract': READER_CONTRACT,
              'api_build': 'abcdef1', 'collector_build': 'abcdef1'}
    (data / auto.READER_MARKER).write_bytes(canonical_bytes(marker))
    def actual_reader(url, timeout):
        assert url == auto.API_PROBE and timeout == 5
        code, body = vps_runtime.PropertyAPI(data).dispatch('/api/v1/property/working-store-reader')
        assert code == 200
        return io.BytesIO(canonical_bytes(body))
    monkeypatch.setattr(auto, 'urlopen', actual_reader)
    return data, root, store


def source_file(root, label):
    body = xml() + ('<!-- ' + label + ' -->').encode()
    return immutable(root, 'raw/sale/11110/202609/' + sha256(body) + '.xml', body)


def file_bytes(folder):
    # Closing a SQLite writer may remove empty WAL/SHM sidecars without changing
    # the logical ledger. They are not immutable source/backup payloads.
    return {str(path.relative_to(folder)): path.read_bytes() for path in folder.rglob('*')
            if path.is_file() and path.name not in ('checkpoint.sqlite-wal', 'checkpoint.sqlite-shm')}


def test_real_worker_hook_releases_lock_then_retires_only_backed_files(tmp_path, monkeypatch):
    data, root, store = setup(tmp_path, monkeypatch)
    protected = source_file(root, 'before-backup')
    backup_set(root, store)
    recovery = {'status': 'verified', 'head': store.head()}
    unbacked = source_file(root, 'not-in-successful-head')
    ledger = (root / 'checkpoint.sqlite').read_bytes()
    objects = file_bytes(store.root)
    query = '/api/v1/property/transactions?regionCode=11110&month=202609&trade=rent'
    expected = vps_runtime.PropertyAPI(data).dispatch(query)
    def collected(*args, **kwargs):
        # The wrapper owns the shared lock during collection/backup.
        with pytest.raises(RealEstateError, match='bulk_work_busy'):
            with bulk_work(data):
                pass
        return {'collection': {'requests': 0, 'stop_reason': 'work_complete'}, 'backup': recovery}
    monkeypatch.setattr(vps_runtime, '_collect_once', collected)
    result = vps_runtime.collect_once(root, store.root, tmp_path / 'no-secret')
    state = result['working_retirement']
    assert state['state'] == 'complete' and state['retired'] > 0
    assert state['source_calls'] == 0
    assert not (root / protected['path']).exists()
    assert (root / unbacked['path']).is_file()
    assert (root / 'checkpoint.sqlite').read_bytes() == ledger
    assert file_bytes(store.root) == objects
    assert vps_runtime.PropertyAPI(data).dispatch(query) == expected
    prior = file_bytes(data)
    monkeypatch.setattr(auto, '_execute', lambda *a, **k: pytest.fail('unchanged successful head respawned child'))
    repeated = vps_runtime.collect_once(root, store.root, tmp_path / 'no-secret')
    assert repeated['working_retirement']['state'] == 'unchanged'
    assert file_bytes(data) == prior


def test_default_is_disabled_and_enabled_fresh_install_needs_manual_migration(tmp_path, monkeypatch):
    data, root = acquired(tmp_path); store = LocalArchiveSet(tmp_path / 'cas')
    monkeypatch.delenv(auto.ENABLE, raising=False)
    assert auto.after_backup(root, store.root, None)['state'] == 'disabled'
    monkeypatch.setenv(auto.ENABLE, '1')
    assert auto.after_backup(root, store.root, None)['state'] == 'needs_manual_migration'
    assert not (root / INDEX).exists()


@pytest.mark.parametrize('bad', ['absent', 'collector-build', 'reader-contract'])
def test_explicit_both_reader_marker_is_required(tmp_path, monkeypatch, bad):
    data, root, store = setup(tmp_path, monkeypatch)
    marker = data / auto.READER_MARKER
    if bad == 'absent':
        marker.unlink()
    else:
        value = json.loads(marker.read_bytes())
        value['collector_build' if bad == 'collector-build' else 'reader_contract'] = 'wrong'
        marker.write_bytes(canonical_bytes(value))
    before = file_bytes(root)
    result = auto.after_backup(root, store.root, {'status': 'verified', 'head': store.head()})
    assert result['state'] == 'deferred' and result['retired'] == 0
    assert file_bytes(root) == before


@pytest.mark.parametrize('state', [None, {'status': 'failed'}, {'status': 'pending'}])
def test_no_successful_backup_means_no_delete(tmp_path, monkeypatch, state):
    _, root, store = setup(tmp_path, monkeypatch)
    before = file_bytes(root)
    assert auto.after_backup(root, store.root, state)['state'] == 'awaiting_verified_backup'
    assert file_bytes(root) == before


def test_api_build_or_compressed_probe_failure_defers_without_exposing_errors(tmp_path, monkeypatch):
    data, root, store = setup(tmp_path, monkeypatch)
    before = file_bytes(root)
    monkeypatch.setattr(auto, 'urlopen', lambda *a, **k: io.BytesIO(canonical_bytes({
        'ready': True, 'read_only_index': True, 'reader_contract': READER_CONTRACT,
        'build': '0000000', 'probe_sha256': '0' * 64})))
    result = auto.after_backup(root, store.root, {'status': 'verified', 'head': store.head()})
    assert result['state'] == 'deferred' and result['retired'] == 0
    def failure(*a, **k):
        raise RuntimeError('serviceKey=do-not-log')
    monkeypatch.setattr(auto, 'urlopen', failure)
    auto.after_backup(root, store.root, {'status': 'verified', 'head': store.head()})
    assert b'do-not-log' not in (data / auto.STATE).read_bytes()
    assert file_bytes(root) == before


def test_batch_budget_resumes_same_head_and_daily_quota_does_not_disable_retirement(tmp_path, monkeypatch):
    _, root, store = setup(tmp_path, monkeypatch)
    monkeypatch.setenv(auto.LIMIT, '1')
    recovery = {'status': 'unchanged', 'head': store.head()}
    states = [auto.after_backup(root, store.root, recovery) for _ in range(5)]
    assert all(row['retired'] <= 1 for row in states)
    assert any(row['state'] == 'batch_complete' for row in states)
    assert states[-1]['state'] == 'unchanged'


def test_exact_head_is_checked_before_and_after_reader_ack(tmp_path, monkeypatch):
    _, root, store = setup(tmp_path, monkeypatch)
    recovery = {'status': 'verified', 'head': store.head()}
    before = file_bytes(root)
    def moved(marker):
        source_file(root, 'advance-after-ack'); backup_set(root, store)
    monkeypatch.setattr(auto, '_acknowledge', moved)
    result = auto.after_backup(root, store.root, recovery)
    assert result['state'] == 'deferred'
    assert all((root / name).read_bytes() == body for name, body in before.items())
    again = auto.after_backup(root, store.root, recovery)
    assert again['error_code'] == 'working_store_head_changed' and again['retired'] == 0


@pytest.mark.parametrize('setting,value', [(auto.LIMIT, '5001'), (auto.TIMEOUT, '301'), (auto.LIMIT, '0')])
def test_invalid_work_budget_fails_closed(tmp_path, monkeypatch, setting, value):
    _, root, store = setup(tmp_path, monkeypatch)
    monkeypatch.setenv(setting, value)
    before = file_bytes(root)
    result = auto.after_backup(root, store.root, {'status': 'verified', 'head': store.head()})
    assert result['error_code'] == 'working_retirement_budget' and result['retired'] == 0
    assert file_bytes(root) == before


@pytest.mark.skipif(os.name == 'nt', reason='Linux worker child process deadline')
def test_timeout_kills_child_and_reports_unknown_partial_count(tmp_path, monkeypatch):
    _, root, store = setup(tmp_path, monkeypatch)
    real_popen = subprocess.Popen; children = []
    def sleeping_child(command, **kwargs):
        process = real_popen([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(auto.subprocess, 'Popen', sleeping_child)
    with pytest.raises(RealEstateError, match='working_retirement_timeout'):
        auto._execute(root, store.root, store.head(), 1, .05)
    assert children and children[0].poll() is not None
    def timed_out(*a, **k):
        raise RealEstateError('working_retirement_timeout')
    monkeypatch.setattr(auto, '_execute', timed_out)
    result = auto.after_backup(root, store.root, {'status': 'verified', 'head': store.head()})
    assert result['retired'] is None and result['state'] == 'deferred'


def test_exhausted_daily_quota_still_returns_verified_unchanged_backup(tmp_path, monkeypatch):
    from pipeline.property_backup_idle import remember
    _, root, store = setup(tmp_path, monkeypatch)
    remember(root, store, store.head())
    monkeypatch.setattr(vps_runtime, 'available_trades', lambda *a, **k: [])
    monkeypatch.setattr(vps_runtime, 'read_key', lambda *a: pytest.fail('quota wait read provider key'))
    class ExistingCollector:
        def __init__(self, *a, **k):
            self.db = sqlite3.connect(root / 'checkpoint.sqlite')
        def close(self):
            self.db.close()
    monkeypatch.setattr(vps_runtime, 'Collector', ExistingCollector)
    from collections import namedtuple
    Usage = namedtuple('Usage', 'total used free')
    monkeypatch.setattr(vps_runtime.shutil, 'disk_usage', lambda _: Usage(10**12, 0, 10**12))
    result = vps_runtime._collect_once(root, store.root, tmp_path / 'no-secret')
    assert result['collection'] == {'requests': 0, 'stop_reason': 'local_daily_budget'}
    assert result['backup']['status'] == 'unchanged'


def test_explicit_container_mount_supports_host_created_index_hint(tmp_path, monkeypatch):
    _, root, store = setup(tmp_path, monkeypatch)
    with closing(sqlite3.connect(root / INDEX)) as db:
        db.execute("UPDATE meta SET value='/host-only/cas/path' WHERE key='archive_root'")
        db.commit()
    monkeypatch.setenv('KOREA_REPLAY_WORKING_ARCHIVE', str(store.root))
    result = auto.after_backup(root, store.root, {'status': 'verified', 'head': store.head()})
    assert result['state'] == 'complete' and result['retired'] > 0


def test_child_source_verification_failure_preserves_pending_plaintext_and_api(tmp_path, monkeypatch):
    from pipeline.real_estate_local_archive_set import load_set
    data, root, store = setup(tmp_path, monkeypatch)
    pending = source_file(root, 'new-batch-will-fail-cas-verification')
    backup_set(root, store)
    row = next(row for row in load_set(store, store.head())['files'] if row['path'] == pending['path'])
    target = store._path(row['object']['sha256'])
    if not target.exists():
        target = target.with_suffix('.encoded')
    from pipeline.property_backup_idle import fingerprint
    before = file_bytes(root)
    ledger = fingerprint(root / 'checkpoint.sqlite')
    target.write_bytes(b'corrupt-pack')
    monkeypatch.setattr(auto, '_acknowledge', lambda _: None)
    result = auto.after_backup(root, store.root, {'status': 'verified', 'head': store.head()})
    assert result['state'] == 'deferred' and result['retired'] is None
    assert file_bytes(root) == before
    assert fingerprint(root / 'checkpoint.sqlite') == ledger
    # Still-pending plaintext does not depend on its damaged backup.
    assert vps_runtime.PropertyAPI(data).dispatch('/api/v1/property/transactions?regionCode=11110&month=202609&trade=rent')[0] == 200


def test_reader_proof_fails_without_index_or_accessible_cas(tmp_path, monkeypatch):
    data, root = acquired(tmp_path)
    api = vps_runtime.PropertyAPI(data)
    assert api.dispatch('/api/v1/property/working-store-reader')[0] == 503
    store = LocalArchiveSet(tmp_path / 'cas'); backup_set(root, store)
    migrate(root, store, limit=1)
    monkeypatch.setenv('KOREA_REPLAY_WORKING_ARCHIVE', str(tmp_path / 'missing-mount'))
    code, body = api.dispatch('/api/v1/property/working-store-reader')
    assert code == 503 and body == {'ready': False, 'error_code': 'working_store_reader_not_ready'}


@pytest.mark.skipif(os.name == 'nt', reason='Linux shared-lock subprocess')
def test_real_child_busy_is_safe_deferred_and_next_cycle_resumes(tmp_path,monkeypatch):
    data,root,store=setup(tmp_path,monkeypatch)
    source=source_file(root,'backed-before-competing-stage');backup_set(root,store)
    before=file_bytes(root);head=store.head()
    query='/api/v1/property/transactions?regionCode=11110&month=202609&trade=rent'
    expected=vps_runtime.PropertyAPI(data).dispatch(query)
    # Reproduce a candidate/stage taking the shared lock after collection released it.
    with bulk_work(data):
        result=auto.after_backup(root,store.root,{'status':'verified','head':head})
    assert result=={'state':'deferred','source_calls':0,'retired':0,
                    'error_code':'vps_bulk_work_busy','child_exit_code':1,'reason':'busy','retryable':True}
    assert file_bytes(root)==before and (root/source['path']).exists()
    assert vps_runtime.PropertyAPI(data).dispatch(query)==expected
    # No inner retry is added. The next normal cycle uses the same successful head.
    next_result=auto.after_backup(root,store.root,{'status':'unchanged','head':head})
    assert next_result['state']=='complete' and next_result['retired']>0
    assert not (root/source['path']).exists() and store.head()==head
    assert vps_runtime.PropertyAPI(data).dispatch(query)==expected


@pytest.mark.parametrize('stderr,expected',[
    (b'working_store: working_store_reference_mismatch\n','working_store_reference_mismatch'),
    (b'working_store: local_archive_writer_active\n','local_archive_writer_active'),
    (b'working_store: archive_collector_active\n','archive_collector_active'),
    (b'working_store: invented_error_serviceKey_secret\n','working_retirement_child_failed'),
    (b'working_store: vps_bulk_work_busy\nserviceKey=never-log\n','working_retirement_child_failed'),
    (b'Traceback: /secret/provider.json serviceKey=never-log','working_retirement_child_failed'),
    (b'\xff\xfe secret','working_retirement_child_failed'),
    (b'x'*(auto.MAX_MESSAGE+1),'working_retirement_child_failed'),
])
def test_child_diagnostics_only_keep_exact_allowlisted_code(tmp_path,monkeypatch,stderr,expected):
    data,root,store=setup(tmp_path,monkeypatch)
    class Failed:
        returncode=1
        def communicate(self,timeout):return b'',stderr
    monkeypatch.setattr(auto.subprocess,'Popen',lambda *a,**k:Failed())
    result=auto.after_backup(root,store.root,{'status':'verified','head':store.head()})
    assert result['error_code']==expected and result['child_exit_code']==1
    assert result['retired']==(0 if expected in auto.BUSY_CODES else None)
    assert b'serviceKey' not in (data/auto.STATE).read_bytes()
    assert b'never-log' not in (data/auto.STATE).read_bytes()
    assert b'Traceback' not in (data/auto.STATE).read_bytes()
    assert b'provider.json' not in (data/auto.STATE).read_bytes()


def test_child_signal_is_recorded_without_claiming_oom(tmp_path,monkeypatch):
    _,root,store=setup(tmp_path,monkeypatch)
    class Killed:
        returncode=-9
        def communicate(self,timeout):return b'',b''
    monkeypatch.setattr(auto.subprocess,'Popen',lambda *a,**k:Killed())
    result=auto.after_backup(root,store.root,{'status':'verified','head':store.head()})
    assert result['child_exit_code']==-9 and result['error_code']=='working_retirement_child_failed'
    assert result['retired'] is None and 'oom' not in json.dumps(result)
