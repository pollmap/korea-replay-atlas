import pytest
from pipeline.bulk_work import bulk_work
from pipeline.real_estate import RealEstateError
from pipeline.vps_runtime import collect_once
from pipeline.property_candidate import run

def test_collector_and_candidate_share_disk_gate_without_calls_or_state_writes(tmp_path):
    data = tmp_path / 'data'; data.mkdir()
    root = data / 'collector'; root.mkdir()
    with bulk_work(data):
        with pytest.raises(RealEstateError, match='vps_bulk_work_busy'):
            collect_once(root, tmp_path/'backups', tmp_path/'missing-secret')
        with pytest.raises(RealEstateError, match='vps_bulk_work_busy'):
            run(data, tmp_path/'candidates')
    assert not (data/'worker-status.json').exists()
    assert not (tmp_path/'candidates/status.json').exists()
    assert not list(root.iterdir())
    with bulk_work(data):
        pass
