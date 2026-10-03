import json
import sqlite3
import shutil
import pytest
from pipeline.real_estate import RealEstateError
from pipeline.property_read_model import publish as read_model
from pipeline.property_candidate import run
from test_real_estate_publish import setup


def prepare(tmp_path):
    source = setup(tmp_path)
    data = tmp_path / 'data'; data.mkdir()
    shutil.move(source, data / 'collector')
    model = read_model(data, reserve_bytes=0)
    return data, model


def test_candidate_pins_closed_model_and_preserves_writer(tmp_path):
    data, model = prepare(tmp_path)
    # Change the writer AFTER the closed generation is published.
    with sqlite3.connect(data / 'collector/checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET status='pending', snapshot=NULL, pages='[]'")
    output = tmp_path / 'candidates'; events = []
    result = run(data, output, reserve_bytes=0, progress=events.append)
    assert result['read_model'] == model
    assert result['audit']['source_rows'] == 4
    assert result['audit']['raw_pages_reparsed']
    assert result['audit']['all_transaction_ids_preserved']
    assert result['public_release'] is False
    assert result['source_calls'] == result['ledger_writes'] == 0
    assert json.loads((output / 'last-verified.json').read_bytes()) == result
    with sqlite3.connect(data / 'collector/checkpoint.sqlite') as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE snapshot IS NOT NULL").fetchone()[0] == 0
    assert {e['phase'] for e in events} >= {'raw_audit','monthly_summary','summary_pack','verified'}
    assert run(data, output, reserve_bytes=0) == result


def test_failed_raw_audit_keeps_previous_candidate_and_resumes_pin(tmp_path):
    data, model = prepare(tmp_path); output = tmp_path / 'candidates'
    first = run(data, output, reserve_bytes=0)
    previous = (output / 'last-verified.json').read_bytes()
    # Force a different fingerprint so the immutable existing candidate isn't reused.
    with sqlite3.connect(data / 'collector/checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET updated_at='2026-09-16T00:00:00Z'")
    next_model = read_model(data, reserve_bytes=0)
    raw = next((data / 'collector/raw').rglob('*.xml')); original = raw.read_bytes()
    raw.write_bytes(b'corrupted')
    with pytest.raises(RealEstateError): run(data, output, reserve_bytes=0)
    failed = json.loads((output / 'status.json').read_bytes())
    assert failed['state'] == 'failed'
    assert failed['read_model'] == next_model
    assert (output / 'last-verified.json').read_bytes() == previous
    raw.write_bytes(original)
    read_model(data, reserve_bytes=0)  # Current advances while retry must stay pinned.
    result = run(data, output, reserve_bytes=0)
    assert result['read_model'] == next_model
    assert result['read_model'] != first['read_model']


def test_missing_model_and_forbidden_output_do_not_publish(tmp_path):
    data, _ = prepare(tmp_path)
    with pytest.raises(RealEstateError, match='public_output_forbidden'):
        run(data, tmp_path / 'public', reserve_bytes=0)
    (data / 'read-model/current.json').write_text('{}')
    with pytest.raises(RealEstateError, match='read_model_not_ready'):
        run(data, tmp_path / 'candidates', reserve_bytes=0)
    assert not (tmp_path / 'candidates/last-verified.json').exists()


def test_concurrent_candidate_does_not_start_second_writer(tmp_path):
    from pipeline.real_estate_local_archive import _lock
    data, _ = prepare(tmp_path); output = tmp_path / 'candidates'; output.mkdir()
    with _lock(output / 'candidate.lock'):
        with pytest.raises(RealEstateError, match='local_archive_writer_active'):
            run(data, output, reserve_bytes=0)
    assert not (output / 'status.json').exists()
