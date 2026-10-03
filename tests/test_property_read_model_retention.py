import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
from collections import namedtuple

import pytest

from pipeline import vps_runtime
from pipeline.property_read_model import publish, ReadModel
from pipeline.property_read_model_retention import reclaim_generations
from pipeline.real_estate import RealEstateError
from test_vps_runtime import acquired
from test_real_estate_fetch import collector, xml, KEY


def copies(tmp_path):
    data, _ = acquired(tmp_path)
    manifests = [publish(data, reserve_bytes=0) for _ in range(5)]
    paths = sorted((data / 'read-model').glob('*.sqlite'), key=lambda p: p.stat().st_mtime_ns)
    for i, path in enumerate(paths):
        os.utime(path, (100 + i, 100 + i))
    return data, manifests[-1], paths


def test_keeps_current_three_latest_and_unrelated_files(tmp_path):
    data, model, paths = copies(tmp_path)
    extra = data / 'read-model/manual.sqlite'; extra.write_text('preserve')
    candidate = data / 'read-model/unpublished.candidate.sqlite'; candidate.write_text('preserve')
    result = reclaim_generations(data, acknowledged_generation=model['generation'], now=10000)
    assert result['deleted_files'] == len(paths) - 3
    assert all(p.exists() for p in paths[-3:])
    assert extra.read_text() == candidate.read_text() == 'preserve'
    assert ReadModel(data).resolve()[1]['generation'] == model['generation']
    assert (data / 'collector/checkpoint.sqlite').exists()


def test_reader_ack_and_grace_are_required(tmp_path):
    data, model, paths = copies(tmp_path)
    with pytest.raises(RealEstateError, match='reader_not_ready'):
        reclaim_generations(data, acknowledged_generation='older', now=10000)
    assert all(p.exists() for p in paths)
    assert reclaim_generations(data, acknowledged_generation=model['generation'], now=200)['deleted_files'] == 0
    with pytest.raises(RealEstateError, match='retention_policy'):
        reclaim_generations(data, acknowledged_generation=model['generation'], keep=1)


def test_corrupt_or_changed_copies_are_preserved(tmp_path):
    data, model, paths = copies(tmp_path)
    paths[0].write_bytes(b'not a verified generation'); os.utime(paths[0], (100, 100))
    result = reclaim_generations(data, acknowledged_generation=model['generation'], now=10000)
    assert paths[0].exists() and result['skipped_files'] == 1


def test_pointer_change_stops_retirement(tmp_path, monkeypatch):
    data, model, paths = copies(tmp_path)
    from pipeline import property_read_model_retention as retention
    original = retention.sha256
    def change_head(path):
        pointer = data / 'read-model/current.json'
        value = json.loads(pointer.read_bytes()); value['generation'] = paths[-2].stem
        pointer.write_text(json.dumps(value))
        return original(path)
    monkeypatch.setattr(retention, 'sha256', change_head)
    result = reclaim_generations(data, acknowledged_generation=model['generation'], now=10000)
    assert result['state'] == 'head_changed' and result['deleted_files'] == 0 and paths[-2].exists()


def test_unacknowledged_api_never_reclaims(tmp_path, monkeypatch):
    data, model, paths = copies(tmp_path)
    monkeypatch.setattr(vps_runtime, 'urlopen', lambda *a, **k: io.BytesIO(b'{"service":"korea-replay","read_model":{"generation":"older"}}'))
    vps_runtime.retire_acknowledged_read_models(data, model)
    assert all(p.exists() for p in paths)
    assert json.loads((data / 'read-model-retention.json').read_bytes())['state'] == 'deferred'


def test_vps_finishes_pending_jobs_with_retained_snapshots(tmp_path, monkeypatch):
    root = tmp_path / 'collector'
    c = collector(root, lambda *a, **k: xml())
    c.collect(KEY, max_requests=2, min_interval=0)
    prior = c.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    c.db.execute("UPDATE jobs SET status='pending',pages='[]' WHERE trade_type='sale'"); c.db.commit()
    disk = namedtuple('disk', 'total used free')(1000 * 1024**3, 0, 1000 * 1024**3)
    monkeypatch.setattr(shutil, 'disk_usage', lambda p: disk)
    monkeypatch.setattr(vps_runtime, 'Collector', lambda *a, **k: c)
    monkeypatch.setattr(vps_runtime, 'read_key', lambda p: KEY)
    monkeypatch.setattr(vps_runtime, 'backup', lambda *a, **k: {'status': 'verified'})
    result = vps_runtime._collect_once(root, tmp_path / 'backup', 'fixture', max_requests=1)
    assert result['collection']['requests'] == 1
    with sqlite3.connect(root / 'checkpoint.sqlite') as db:
        assert db.execute("SELECT status FROM jobs WHERE trade_type='sale'").fetchone()[0] == 'empty'
        assert db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] >= prior
        assert db.execute("SELECT status FROM jobs WHERE trade_type='rent'").fetchone()[0] == 'empty'
