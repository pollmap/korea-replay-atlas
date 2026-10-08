import io
import json
import multiprocessing
from pathlib import Path
import time

import pytest

from pipeline import core


class Response:
    status_code=200
    headers={'Content-Length':'7'}
    def __init__(self,delay=0):self.raw=self;self.content=io.BytesIO(b'fixture');self.delay=delay
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def raise_for_status(self):pass
    def read1(self,size,decode_content=True):
        time.sleep(self.delay)
        return self.content.read(size)


def download_worker(path,marker,start):
    def request(*args,**kwargs):
        # Only one worker may perform the transfer. Others must use its verified cache.
        with Path(marker).open('x') as stream:stream.write('one network request')
        return Response(.2)
    core.requests.get=request
    start.wait()
    core.download('https://public.invalid/test',Path(path),min_free_gb=0,deadline_seconds=10)


def test_spawned_download_writers_fetch_same_file_once(tmp_path):
    context=multiprocessing.get_context('spawn');start=context.Event()
    path=tmp_path/'shared.zip';marker=tmp_path/'network-count'
    processes=[context.Process(target=download_worker,args=(path,marker,start)) for _ in range(3)]
    try:
        for process in processes:process.start()
        start.set()
        for process in processes:process.join(timeout=20)
        assert all(p.exitcode==0 for p in processes)
        assert path.read_bytes()==b'fixture'
        assert json.loads(path.with_suffix('.zip.meta.json').read_text())['sha256']==core.digest(path)
    finally:
        for process in processes:
            if process.is_alive():process.terminate();process.join()


def test_total_deadline_never_publishes_partial_file(tmp_path,monkeypatch):
    monkeypatch.setattr(core.requests,'get',lambda *a,**k:Response(.04))
    path=tmp_path/'slow.zip'
    with pytest.raises(TimeoutError,match='total deadline'):
        core.download('https://public.invalid/slow',path,min_free_gb=0,deadline_seconds=.02)
    assert not path.exists()
    assert not path.with_suffix('.zip.meta.json').exists()
    assert list(tmp_path.glob('*.part'))  # Incomplete artifacts are retained, never called valid data.


def test_waiting_for_same_writer_has_a_deadline(tmp_path):
    path=tmp_path/'source.zip'
    with core._download_lock(path,time.monotonic()+1):
        with pytest.raises(TimeoutError,match='writer-lock'):
            with core._download_lock(path,time.monotonic()+.05):pass




def test_2d_footprints_preserve_ids_without_terrain_or_height_downloads(tmp_path,monkeypatch):
    from pipeline import buildings
    import pyarrow as pa
    import pyarrow.parquet as pq
    from shapely.geometry import box
    raw=tmp_path/'input.parquet'
    rows=[{'id':'source','height':22.,'num_floors':None,'geometry':box(127,36,127.0001,36.0001).wkb},
          {'id':'floors','height':None,'num_floors':3.,'geometry':box(127,36,127.0001,36.0001).wkb},
          {'id':'unknown','height':None,'num_floors':None,'geometry':box(127,36,127.0001,36.0001).wkb}]
    pq.write_table(pa.Table.from_pylist(rows),raw)
    raw.with_suffix('.meta.json').write_text(json.dumps({'sha256':'synthetic','retrieved_at':'2026-09-16T00:00:00Z'}))
    monkeypatch.setattr(buildings,'LOCAL',tmp_path)
    monkeypatch.setitem(buildings.REGIONS,'synthetic-test',(127,36,127.001,36.001))
    monkeypatch.setattr(buildings,'extract',lambda region:raw)
    monkeypatch.setattr(buildings,'register_asset',lambda *args,**kwargs:None)
    def unexpected_request(*args,**kwargs):
        raise AssertionError('2D normalization must not request terrain or height grids')
    monkeypatch.setattr(core.requests,'get',unexpected_request)
    result=buildings.buildings('synthetic-test')
    values={f['id']:f['properties'] for f in json.loads(result.read_text(encoding='utf-8'))['features']}
    assert values['source']['height']==22
    assert values['floors']['height']==9
    assert values['unknown']['height'] is None
    document=json.loads(result.read_text(encoding='utf-8'))
    assert set(values)=={'source','floors','unknown'}
    for row,feature in zip(rows,document['features']):
        assert feature['id']==row['id']==feature['properties']['source_record_id']
        assert feature['geometry']['coordinates']==json.loads(json.dumps(box(127,36,127.0001,36.0001).__geo_interface__['coordinates']))
        assert 'base_height' not in feature['properties']
    assert not (tmp_path/'raw/terrain').exists()
    assert not (tmp_path/'raw/ghsl').exists()
    assert not (tmp_path/'audit/building-height-retries').exists()
