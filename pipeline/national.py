"""Resumable country partitioning; per-cell geometry stays bounded in memory."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import queue
import signal
import sqlite3
import shutil
from contextlib import contextmanager
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from .core import LOCAL,PUBLIC,RELEASE,REGIONS,atomic_json,digest,download,now,register_asset

def country_mask():
    path=download('https://download.geofabrik.de/asia/south-korea.poly',LOCAL/'raw'/'osm'/'south-korea.poly')
    lines=path.read_text().splitlines()[1:];rings=[];current=[];hole=False;holes=[]
    for line in lines:
        line=line.strip()
        if not line:continue
        if line=='END':
            if current:
                (holes if hole else rings).append(current);current=[]
            continue
        parts=line.split()
        if len(parts)==1:hole=line.startswith('!');continue
        current.append(tuple(map(float,parts)))
    mask=shapely.union_all([shapely.Polygon(r) for r in rings])
    if holes:mask=mask.difference(shapely.union_all([shapely.Polygon(r) for r in holes]))
    return mask,digest(path)

def partition():
    raw=LOCAL/'raw'/'overture'/RELEASE/'korea-building.parquet'
    if not raw.exists():raise ValueError('Run national building extraction first')
    meta=json.loads(raw.with_suffix('.meta.json').read_text(encoding='utf-8'))
    mask,mask_hash=country_mask();shapely.prepare(mask)
    root=LOCAL/'national'/f'{RELEASE}-{meta["sha256"][:12]}';root.mkdir(parents=True,exist_ok=True)
    state_path=root/'partition-state.json'
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'next_batch':0,'input_rows':0,'accepted':0,'outside_mask':0,'invalid':0,'source_hash':meta['sha256'],'mask_hash':mask_hash}
    if state.get('mask_hash')!=mask_hash:raise ValueError('Country mask changed; create a new partition version')
    for batch_number,batch in enumerate(pq.ParquetFile(raw).iter_batches(batch_size=20000)):
        if batch_number<state['next_batch']:continue
        if shutil.disk_usage(root).free<30*1024**3:raise RuntimeError('National partition paused: 30 GiB reserve')
        geom=shapely.from_wkb(batch.column('geometry').to_pylist(),on_invalid='ignore')
        valid=shapely.is_valid(geom)&~shapely.is_empty(geom)
        points=shapely.point_on_surface(geom)
        inside=valid&shapely.covers(mask,points)
        x=shapely.get_x(points);y=shapely.get_y(points)
        groups={}
        for index in __import__('numpy').flatnonzero(inside):
            key=(math.floor(x[index]*10),math.floor(y[index]*10));groups.setdefault(key,[]).append(int(index))
        for (gx,gy),indices in groups.items():
            target=root/'cells'/f'{gx}-{gy}'/f'{batch_number:06d}.parquet';target.parent.mkdir(parents=True,exist_ok=True)
            temp=target.with_suffix('.part');pq.write_table(pa.Table.from_batches([batch.take(pa.array(indices))]),temp,compression='zstd');temp.replace(target)
        state.update({'next_batch':batch_number+1,'input_rows':state['input_rows']+batch.num_rows,'accepted':state['accepted']+int(inside.sum()),'outside_mask':state['outside_mask']+int((valid&~inside).sum()),'invalid':state['invalid']+int((~valid).sum())})
        atomic_json(state_path,state)
        if batch_number%20==0:print(json.dumps({'stage':'national-partition',**state}),flush=True)
    cells=[]
    for directory in sorted((root/'cells').iterdir()):
        gx,gy=map(int,directory.name.split('-'));name=f'kr-{gx}-{gy}'
        count=sum(pq.ParquetFile(p).metadata.num_rows for p in directory.glob('*.parquet'))
        cells.append({'name':name,'bbox':[gx/10,gy/10,(gx+1)/10,(gy+1)/10],'directory':str(directory),'count':count})
    manifest={'source':meta,'mask_hash':mask_hash,'cells':cells,'summary':state};atomic_json(root/'cells.json',manifest)
    atomic_json(LOCAL/'national'/'current.json',{'path':str(root/'cells.json')})
    print(json.dumps({'stage':'national-partition-complete','cells':len(cells),**state}),flush=True)
    return manifest

def _initialize_jobs(db_path):
    with sqlite3.connect(db_path,timeout=60) as db:
        db.execute('CREATE TABLE IF NOT EXISTS national_jobs (id TEXT PRIMARY KEY, source_hash TEXT, status TEXT, finished_at TEXT, reason TEXT)')


def _complete(db_path,cell_id,version):
    with sqlite3.connect(db_path,timeout=60) as db:
        row=db.execute('SELECT status,source_hash FROM national_jobs WHERE id=?',(cell_id,)).fetchone()
    return row is not None and row[0]=='complete' and row[1]==version


def _checkpoint(db_path,cell_id,version,status,reason=None):
    with sqlite3.connect(db_path,timeout=60) as db:
        db.execute('BEGIN IMMEDIATE')
        # A late cancellation must never downgrade a successfully completed cell.
        row=db.execute('SELECT status,source_hash FROM national_jobs WHERE id=?',(cell_id,)).fetchone()
        if row and row[0]=='complete' and row[1]==version:return
        db.execute('INSERT OR REPLACE INTO national_jobs VALUES(?,?,?,?,?)',
                   (cell_id,version,status,now(),reason))


def _pending_cells(manifest,db_path,limit=0):
    if limit<0:raise ValueError('limit must be non-negative')
    cells=manifest['cells']
    if len({c['name'] for c in cells})!=len(cells):raise ValueError('Duplicate cell IDs in manifest')
    version=manifest['source']['sha256']+':m3'
    with sqlite3.connect(db_path,timeout=60) as db:
        completed={row[0] for row in db.execute('SELECT id FROM national_jobs WHERE status=? AND source_hash=?',('complete',version))}
    pending=sorted((c for c in cells if c['name'] not in completed),
                   key=lambda c:((c['bbox'][0]-127.4)**2+(c['bbox'][1]-36.5)**2,c['name']))
    return pending[:limit] if limit else pending


@contextmanager
def _coordinator_lock(path):
    """OS-owned lock is released after a crash; retain the harmless lock file."""
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        if stream.seek(0,2)==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('Another national process coordinator is already running') from error
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def _convert_cell(cell,manifest,root):
    """Worker-local region registration and cell-specific files; raw input is retained."""
    from .buildings import buildings
    from .mesh import tiles
    if shutil.disk_usage(root).free<30*1024**3:raise RuntimeError('National conversion paused: 30 GiB reserve')
    REGIONS[cell['name']]=tuple(cell['bbox'])
    target=LOCAL/'raw'/'overture'/RELEASE/f'{cell["name"]}-building.parquet'
    if not target.exists():
        parts=sorted(Path(cell['directory']).glob('*.parquet'));temp=target.with_suffix('.part')
        if not parts:raise ValueError('No partition files for '+cell['name'])
        with pq.ParquetWriter(temp,pq.read_schema(parts[0]),compression='zstd') as writer:
            for part in parts:
                for batch in pq.ParquetFile(part).iter_batches(batch_size=5000):writer.write_batch(batch)
        temp.replace(target)
    atomic_json(target.with_suffix('.meta.json'),{'source_id':'overture','dataset_version':RELEASE,'bbox':cell['bbox'],'count':cell['count'],'sha256':digest(target),'retrieved_at':manifest['source']['retrieved_at'],'parent_input_hash':manifest['source']['sha256']})
    buildings(cell['name']);tiles(cell['name'])


def _execute_cell(cell,manifest,db_path,root,convert=_convert_cell):
    version=manifest['source']['sha256']+':m3'
    if _complete(db_path,cell['name'],version):return 'skipped'
    _checkpoint(db_path,cell['name'],version,'running')
    try:
        convert(cell,manifest,root)
        _checkpoint(db_path,cell['name'],version,'complete')
        return 'complete'
    except BaseException as error:
        status='interrupted' if isinstance(error,(KeyboardInterrupt,SystemExit)) else 'failed'
        _checkpoint(db_path,cell['name'],version,status,f'{type(error).__name__}: {str(error)[:500]}')
        raise


def _worker_loop(worker_id,inbox,outbox,manifest,db_path,root,convert):
    # The coordinator handles Ctrl+C and only terminates children it owns.
    signal.signal(signal.SIGINT,signal.SIG_IGN)
    while True:
        cell=inbox.get()
        if cell is None:return
        try:
            status=_execute_cell(cell,manifest,db_path,root,convert)
            outbox.put((worker_id,cell['name'],status,None))
        except BaseException as error:
            outbox.put((worker_id,cell['name'],'failed',f'{type(error).__name__}: {str(error)[:500]}'))
            return


def _run_workers(cells,manifest,db_path,root,workers,convert=_convert_cell,poll_timeout=.25):
    """Bounded one-cell-per-worker scheduler, also used by isolated synthetic tests."""
    if workers not in (1,2,3):raise ValueError('workers must be between 1 and 3')
    if len({c['name'] for c in cells})!=len(cells):raise ValueError('Duplicate cell assignments')
    if not cells:return 0
    context=multiprocessing.get_context('spawn')
    outbox=context.Queue();inboxes=[];children=[];active={};pending=iter(cells)
    processed=0;success=False;failure_reason='Coordinator interrupted before cell completion'
    version=manifest['source']['sha256']+':m3'
    try:
        for i in range(min(workers,len(cells))):
            inbox=context.Queue(maxsize=1);inboxes.append(inbox)
            child=context.Process(target=_worker_loop,args=(i,inbox,outbox,manifest,db_path,root,convert),name=f'national-worker-{i+1}')
            child.start();children.append(child)
            cell=next(pending);active[i]=cell;inbox.put(cell)
        while active:
            try:worker_id,cell_id,status,reason=outbox.get(timeout=poll_timeout)
            except queue.Empty:
                for i in active:
                    if children[i].exitcode is not None:
                        failure_reason=f'WorkerExit: worker {i+1} exited with code {children[i].exitcode}'
                        _checkpoint(db_path,active[i]['name'],version,'failed',failure_reason)
                        raise RuntimeError(failure_reason)
                continue
            if worker_id not in active or active[worker_id]['name']!=cell_id:
                raise RuntimeError('Worker result does not match its assigned cell')
            if status=='failed':
                failure_reason=f'Worker failure: {cell_id}: {reason}'
                raise RuntimeError(failure_reason)
            cell=active.pop(worker_id)
            if status=='complete':processed+=1
            print(json.dumps({'stage':'national-cell-'+status,'name':cell_id,'count':cell['count'],
                              'worker':worker_id+1,'processed_this_run':processed}),flush=True)
            following=next(pending,None)
            if following is not None:active[worker_id]=following;inboxes[worker_id].put(following)
            else:inboxes[worker_id].put(None)
        success=True
        return processed
    finally:
        # SIGINT can arrive repeatedly while cleaning up; finish ownership-scoped cleanup.
        old_handler=signal.signal(signal.SIGINT,signal.SIG_IGN)
        try:
            for child in children:
                if not success and child.is_alive():child.terminate()
            for child in children:
                child.join(timeout=5)
                if child.is_alive():child.kill();child.join(timeout=5)
            for cell in active.values():
                # Preserve the precise worker failure already recorded by _execute_cell.
                with sqlite3.connect(db_path,timeout=60) as db:
                    row=db.execute('SELECT status,source_hash FROM national_jobs WHERE id=?',(cell['name'],)).fetchone()
                if not row or row[1]!=version or row[0] not in ('complete','failed'):
                    _checkpoint(db_path,cell['name'],version,'interrupted',failure_reason)
            for inbox in inboxes:inbox.cancel_join_thread();inbox.close()
            outbox.cancel_join_thread();outbox.close()
        finally:signal.signal(signal.SIGINT,old_handler)


def process(limit=0,workers=1):
    if workers not in (1,2,3):raise ValueError('workers must be between 1 and 3')
    pointer=json.loads((LOCAL/'national'/'current.json').read_text(encoding='utf-8'))
    manifest=json.loads(Path(pointer['path']).read_text(encoding='utf-8'));root=Path(pointer['path']).parent
    db_path=LOCAL/'catalog.sqlite'
    with _coordinator_lock(LOCAL/'national'/'process.lock'):
        _initialize_jobs(db_path)
        cells=_pending_cells(manifest,db_path,limit)
        print(json.dumps({'stage':'national-run-started','workers':workers,'pending_cells':len(cells)}),flush=True)
        processed=_run_workers(cells,manifest,db_path,root,workers)
        print(json.dumps({'stage':'national-run-ended','processed':processed}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['partition','process']);p.add_argument('--limit',type=int,default=0);p.add_argument('--workers',type=int,choices=[1,2,3],default=1);a=p.parse_args()
    if a.command=='partition':partition()
    else:process(a.limit,a.workers)
