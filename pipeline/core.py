from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from contextlib import contextmanager
import requests
from urllib3.exceptions import HTTPError as UrllibHTTPError

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
PUBLIC = ROOT / 'public' / 'data'
TRANSFORM_VERSION = 'korea-replay-1'
RELEASE = '2026-08-19.0'
REGIONS = {
    'daejeon': (127.405, 36.310, 127.455, 36.355),
    'sejong': (127.235, 36.475, 127.290, 36.530),
    'cheongju': (127.465, 36.625, 127.520, 36.675),
    'korea': (124.5, 33.0, 132.0, 38.7),
}

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content=json.dumps(value, ensure_ascii=False, separators=(',',':'), allow_nan=False).encode('utf-8')
    if path.exists() and path.read_bytes()==content:return
    part = path.with_suffix(f'.{os.getpid()}.tmp')
    part.write_bytes(content)
    for attempt in range(15):
        try:
            part.replace(path)
            break
        except PermissionError:
            if attempt==14:raise
            time.sleep(.05*2**min(attempt,4))

@contextmanager
def _download_lock(path:Path,deadline:float):
    """One writer per public source file across Windows spawned workers."""
    lock=path.with_suffix(path.suffix+'.download.lock')
    with lock.open('a+b') as stream:
        if stream.seek(0,2)==0:stream.write(b'0');stream.flush()
        while True:
            stream.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>=deadline:raise TimeoutError('Public source writer-lock deadline exceeded') from None
                time.sleep(min(.2,max(0,deadline-time.monotonic())))
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def _cached_download(path,metadata):
    metadata=path.with_suffix(path.suffix+'.meta.json')
    if path.exists() and metadata.exists():
        try:return json.loads(metadata.read_text(encoding='utf-8')).get('sha256')==digest(path)
        except (OSError,ValueError):return False
    return False


def download(url: str, path: Path, *, min_free_gb: float = 30, deadline_seconds:float=300) -> Path:
    """Credential-free downloads with a shared writer lock and total elapsed budget.

    The deadline is checked around each read1 (one buffered/network read), not
    just when a full MiB accumulates. A blocking socket read is bounded by 30s;
    after the total deadline no partial content is promoted to the final file.
    """
    if deadline_seconds<=0:raise ValueError('Download deadline must be positive')
    metadata=path.with_suffix(path.suffix+'.meta.json')
    if _cached_download(path,metadata):return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(path.parent).free < min_free_gb * 1024**3: raise RuntimeError('Disk reserve below 30 GiB; download paused.')
    deadline=time.monotonic()+deadline_seconds
    with _download_lock(path,deadline):
        # Another worker may have completed while this worker was waiting.
        if _cached_download(path,metadata):return path
        last_status=None
        for attempt in range(4):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError(f'Public source total deadline exceeded: {path.name}')
            try:
                with requests.get(url,stream=True,timeout=(min(20,remaining),min(30,remaining)),
                    headers={'User-Agent':'KoreaReplay/0.1 (open data archive)','Accept-Encoding':'identity'}) as response:
                    last_status=response.status_code;response.raise_for_status()
                    part=path.with_suffix(path.suffix+f'.{os.getpid()}.part')
                    bytes_written=0;last_progress=time.monotonic()
                    with part.open('wb') as stream:
                        while True:
                            if time.monotonic()>=deadline:raise TimeoutError(f'Public source total deadline exceeded: {path.name}')
                            chunk=response.raw.read1(65536,decode_content=True)
                            if time.monotonic()>=deadline:raise TimeoutError(f'Public source total deadline exceeded: {path.name}')
                            if not chunk:break
                            stream.write(chunk);bytes_written+=len(chunk)
                            if time.monotonic()-last_progress>=15:
                                stream.flush();last_progress=time.monotonic()
                                print(json.dumps({'stage':'public-download','file':path.name,'bytes':bytes_written,'attempt':attempt+1}),flush=True)
                    declared=response.headers.get('Content-Length')
                    if declared and not response.headers.get('Content-Encoding') and bytes_written!=int(declared):
                        raise RuntimeError('Public source response length mismatch')
                    if not bytes_written:raise RuntimeError('Public source response was empty')
                    part.replace(path)
                    atomic_json(metadata,{'url':url,'retrieved_at':now(),'sha256':digest(path),'bytes':path.stat().st_size})
                    return path
            except (requests.RequestException,UrllibHTTPError):
                if attempt==3 or time.monotonic()>=deadline:
                    raise RuntimeError(f'Public source download failed or exceeded deadline (HTTP {last_status}): {path.name}') from None
                time.sleep(min(2**attempt,8,max(0,deadline-time.monotonic())))
    raise RuntimeError('Unreachable')

def register_asset(asset: dict, *, private: bool = False) -> None:
    register_assets([asset],private=private)


def register_assets(batch: list[dict], *, private: bool = False) -> None:
    """Commit a coherent batch under the same writer lock used by source jobs."""
    if not batch:return
    ids=[a['id'] for a in batch]
    if len(set(ids))!=len(ids):raise ValueError('Asset batch contains duplicate IDs')
    LOCAL.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(LOCAL/'catalog.sqlite',timeout=60)
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        connection.execute('BEGIN IMMEDIATE')
        for asset in batch:
            saved={**asset,'_private':True} if private else asset
            connection.execute('INSERT OR REPLACE INTO assets VALUES (?,?)',(asset['id'],json.dumps(saved,ensure_ascii=False)))
        if private:
            connection.commit()
            return
        assets=[json.loads(row[0]) for row in connection.execute('SELECT payload FROM assets ORDER BY id')]
        assets=[a for a in assets if not a.get('_private')]
        if any(a['id']=='terrain-combined' for a in assets):
            assets=[a for a in assets if a['format']!='quantized-mesh' or a['id']=='terrain-combined']
        layers=[]
        specs=[('terrain','지형',['terrain','natural-earth','overture-transportation']),('buildings','건물',['overture','ghsl']),('infrastructure','도로·도시 시설',['osm']),('rail','철도',['overture-transportation','korail']),('bus','버스',['tago']),('depth','지하 역사',['seoul-depth']),('satellite','위성',['kma-satellite']),('radar','레이더',['kma-radar']),('sun','햇빛과 그림자',['sun'])]
        for key,label,sources in specs:
            matching=[a for a in assets if a['layer']==key and a['format']!='search-index']
            times_from=[a['from'] for a in matching if a.get('from')]
            times_to=[a['to'] for a in matching if a.get('to')]
            reason=None if key=='sun' or matching else '검수된 자료가 아직 연결되지 않았습니다.'
            layers.append({'id':key,'label':label,'source_ids':sources,'state':'ready' if key=='sun' else 'partial' if matching else 'unavailable','reason':reason,'record_count':sum(a.get('count',0) for a in matching),'from':min(times_from) if times_from else None,'to':max(times_to) if times_to else None,'updated_at':now() if matching else None})
        fingerprint=hashlib.sha256(json.dumps(assets,sort_keys=True).encode()).hexdigest()[:16]
        release_path=PUBLIC/'releases'/f'pub-{fingerprint}.json'
        catalog=json.loads(release_path.read_text(encoding='utf-8')) if release_path.exists() else {'schema_version':1,'release_id':f'pub-{fingerprint}','generated_at':now(),'base_date':RELEASE[:10],'sources':[],'layers':layers,'assets':assets}
        atomic_json(PUBLIC/'catalog.json',catalog)
        if not release_path.exists(): atomic_json(release_path,catalog)
        connection.commit()
    finally: connection.close()

def publish_file(path:Path,*,asset_id:str,layer:str,format:str,bbox,source_id:str,version:str,count:int,**extra):
    asset={'id':asset_id,'layer':layer,'format':format,'url':'/data/'+path.relative_to(PUBLIC).as_posix(),'bbox':list(bbox),'source_id':source_id,'version':version,'count':count,'sha256':digest(path),**extra}
    register_asset(asset)
    return asset
