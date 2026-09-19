import json
import sqlite3
import pytest
from pipeline import core


def test_batch_publishes_only_complete_asset_set(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'LOCAL',tmp_path/'private')
    monkeypatch.setattr(core,'PUBLIC',tmp_path/'public')
    def asset(name):
        return {'id':name,'layer':'infrastructure','format':'geojson','sha256':name,'count':1}
    core.register_assets([asset('airport'),asset('port')])
    catalog=json.loads((core.PUBLIC/'catalog.json').read_text(encoding='utf-8'))
    assert [a['id'] for a in catalog['assets']]==['airport','port']
    assert len(list((core.PUBLIC/'releases').glob('*.json')))==1
    previous=(core.PUBLIC/'catalog.json').read_bytes()
    with pytest.raises(ValueError):core.register_assets([asset('road'),asset('road')])
    assert (core.PUBLIC/'catalog.json').read_bytes()==previous
    with sqlite3.connect(core.LOCAL/'catalog.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM assets').fetchone()[0]==2
