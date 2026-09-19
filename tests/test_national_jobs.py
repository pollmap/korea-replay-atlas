"""Real spawned workers against isolated synthetic cells; no map downloads/publication."""
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import time

import pytest

from pipeline import national


def sample_manifest(count=6):
    return {'source': {'sha256': 'synthetic-source', 'retrieved_at': '2026-09-16T00:00:00Z'},
            'cells': [{'name': f'kr-test-{i}', 'bbox': [127.4+i*.01,36.5,127.41+i*.01,36.51],
                       'count': i+1, 'directory': 'not-real'} for i in range(count)]}


def synthetic_convert(cell,manifest,root):
    """Exclusive per-cell output detects accidental duplicate assignment across processes."""
    with (Path(root)/(cell['name']+'.json')).open('x') as stream:
        json.dump({'pid': os.getpid(), 'id': cell['name']},stream)
    time.sleep(.04)


def failing_convert(cell,manifest,root):
    if cell['name'].endswith('-0'):raise ValueError('synthetic malformed geometry')
    synthetic_convert(cell,manifest,root)


def crashing_convert(cell,manifest,root):
    os._exit(23)


def waiting_convert(cell,manifest,root):
    (Path(root)/'worker-started').write_text(str(os.getpid()))
    time.sleep(30)


class InterruptingQueue:
    def __init__(self,base,marker):self.base=base;self.marker=marker
    def put(self,*args,**kwargs):return self.base.put(*args,**kwargs)
    def get(self,*args,**kwargs):
        deadline=time.monotonic()+15
        while not self.marker.exists() and time.monotonic()<deadline:time.sleep(.02)
        if not self.marker.exists():raise RuntimeError('Synthetic child never started')
        raise KeyboardInterrupt
    def cancel_join_thread(self):self.base.cancel_join_thread()
    def close(self):self.base.close()


class InterruptingContext:
    def __init__(self,base,marker):self.base=base;self.marker=marker;self.calls=0
    def Queue(self,*args,**kwargs):
        self.calls+=1
        value=self.base.Queue(*args,**kwargs)
        return InterruptingQueue(value,self.marker) if self.calls==1 else value
    def Process(self,*args,**kwargs):return self.base.Process(*args,**kwargs)


@pytest.fixture
def database(tmp_path):
    path=tmp_path/'checkpoint.sqlite'
    national._initialize_jobs(path)
    return path


def rows(database):
    with sqlite3.connect(database) as db:
        return {r[0]:r[1:] for r in db.execute('SELECT id,source_hash,status,finished_at,reason FROM national_jobs')}


def test_checkpoint_resume_excludes_only_current_complete_and_limit_after_filter(database):
    manifest=sample_manifest()
    national._checkpoint(database,'kr-test-0','synthetic-source:m3','complete')
    national._checkpoint(database,'kr-test-1','old-source:m3','complete')
    national._checkpoint(database,'kr-test-2','synthetic-source:m2','complete')
    national._checkpoint(database,'kr-test-3','synthetic-source:m3','failed','earlier failure')
    national._checkpoint(database,'kr-test-4','synthetic-source:m3','running')
    pending=national._pending_cells(manifest,database,limit=3)
    assert [c['name'] for c in pending]==['kr-test-1','kr-test-2','kr-test-3']
    assert len(national._pending_cells(manifest,database))==5


def test_complete_checkpoint_cannot_be_downgraded(database,tmp_path):
    manifest=sample_manifest(1);cell=manifest['cells'][0]
    national._checkpoint(database,cell['name'],'synthetic-source:m3','complete')
    before=rows(database)
    national._checkpoint(database,cell['name'],'synthetic-source:m3','interrupted','late cancellation')
    assert national._execute_cell(cell,manifest,database,tmp_path,synthetic_convert)=='skipped'
    assert rows(database)==before
    assert not (tmp_path/(cell['name']+'.json')).exists()


def test_three_spawned_workers_process_distinct_cells_and_resume_skips_all(database,tmp_path):
    manifest=sample_manifest(9)
    completed=national._run_workers(manifest['cells'],manifest,database,tmp_path,3,synthetic_convert)
    assert completed==9
    outputs=[json.loads(p.read_text()) for p in tmp_path.glob('kr-test-*.json')]
    assert len(outputs)==9
    assert len({o['id'] for o in outputs})==9
    assert len({o['pid'] for o in outputs})==3
    assert all(row[1]=='complete' for row in rows(database).values())
    before=rows(database)
    assert national._pending_cells(manifest,database)==[]
    assert national._run_workers([],manifest,database,tmp_path,3,synthetic_convert)==0
    assert rows(database)==before


def test_failure_keeps_cause_and_only_unfinished_cells_resume(database,tmp_path):
    manifest=sample_manifest(3)
    with pytest.raises(RuntimeError,match='malformed geometry'):
        national._run_workers(manifest['cells'],manifest,database,tmp_path,1,failing_convert)
    failed=rows(database)['kr-test-0']
    assert failed[1]=='failed'
    assert failed[3]=='ValueError: synthetic malformed geometry'
    pending=national._pending_cells(manifest,database)
    assert len(pending)==3
    assert national._run_workers(pending,manifest,database,tmp_path,2,synthetic_convert)==3
    assert national._pending_cells(manifest,database)==[]


def test_abrupt_worker_exit_is_recorded_and_does_not_hang(database,tmp_path):
    manifest=sample_manifest(1)
    with pytest.raises(RuntimeError,match='exited with code 23'):
        national._run_workers(manifest['cells'],manifest,database,tmp_path,1,crashing_convert)
    failed=rows(database)['kr-test-0']
    assert failed[1]=='failed'
    assert 'WorkerExit' in failed[3]
    assert len(national._pending_cells(manifest,database))==1


def test_ctrl_c_cleans_only_owned_children_and_preserves_resume(database,tmp_path,monkeypatch):
    manifest=sample_manifest(1)
    initial={child.pid for child in multiprocessing.active_children()}
    context=InterruptingContext(multiprocessing.get_context('spawn'),tmp_path/'worker-started')
    monkeypatch.setattr(national.multiprocessing,'get_context',lambda mode:context)
    with pytest.raises(KeyboardInterrupt):
        national._run_workers(manifest['cells'],manifest,database,tmp_path,1,waiting_convert)
    assert {child.pid for child in multiprocessing.active_children()}==initial
    assert rows(database)['kr-test-0'][1]=='interrupted'
    assert len(national._pending_cells(manifest,database))==1
    assert (tmp_path/'worker-started').exists()  # No user/source file cleanup.


def test_duplicate_assignment_and_worker_range_rejected_before_spawn(database,tmp_path):
    manifest=sample_manifest(1)
    with pytest.raises(ValueError,match='Duplicate'):
        national._run_workers(manifest['cells']*2,manifest,database,tmp_path,3,synthetic_convert)
    for workers in (0,4,-1):
        with pytest.raises(ValueError,match='workers'):
            national._run_workers([],manifest,database,tmp_path,workers,synthetic_convert)


def test_coordinator_lock_prevents_second_run_and_releases_without_deletion(tmp_path):
    path=tmp_path/'process.lock'
    with national._coordinator_lock(path):
        with pytest.raises(RuntimeError,match='already running'):
            with national._coordinator_lock(path):pass
    with national._coordinator_lock(path):pass
    assert path.exists()
