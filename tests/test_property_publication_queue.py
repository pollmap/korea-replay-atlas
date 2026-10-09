import json
import sqlite3
import pytest
from pipeline.property_publication_queue import reconcile
from pipeline.real_estate import RealEstateError
from test_property_candidate import prepare
from pipeline.vps_runtime import publication_status, write_json
from pipeline.property_read_model import publish as read_model
from pipeline.property_publication_queue import input_identity


def test_pending_candidate_is_not_rebuilt_for_new_collector_generation(tmp_path):
    data, model = prepare(tmp_path); output = tmp_path / 'publication'
    first = reconcile(data, output)
    assert first['action'] == 'awaiting_publisher'
    assert not first['public_release']
    def forbidden(*args, **kwargs):
        raise AssertionError('Unpublished candidate must be reused')
    assert reconcile(data, output, builder=forbidden)['generation'] == model['generation']


def test_failed_publication_keeps_candidate_and_requires_matching_ack(tmp_path):
    data, _ = prepare(tmp_path); output = tmp_path / 'publication'
    first = reconcile(data, output)
    def wrong(*args):
        return {'verified': True, 'property_release': 'wrong'}
    with pytest.raises(RealEstateError, match='publication_failed'):
        reconcile(data, output, publisher=wrong)
    state = json.loads((data / 'public-release-status.json').read_bytes())
    assert state['state'] == 'publish_failed' and not state['public_release']
    def success(path, state):
        assert path.is_file()
        return {'verified': True, 'property_release': state['release_id'],
                'artifact_sha256': 'a' * 64, 'public_origin': 'https://korea-replay.pages.dev'}
    final = reconcile(data, output, publisher=success)
    assert final['state'] == 'published' and final['public_release']
    assert final['release_id'] == first['release_id']
    assert reconcile(data, output, builder=lambda *args, **kwargs: pytest.fail('must skip'))['action'] == 'unchanged'
    # A fresh closed DB can change only bookkeeping, with identical publication inputs.
    with sqlite3.connect(data / 'collector/checkpoint.sqlite') as db:
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('private-last-call','new')")
    next_model = read_model(data, reserve_bytes=0)
    assert next_model['generation'] != final['generation']
    assert reconcile(data, output, builder=lambda *args, **kwargs: pytest.fail('must skip'))['action'] == 'unchanged'


def test_changed_snapshot_reference_invalidates_input_identity(tmp_path):
    data, model = prepare(tmp_path)
    before = input_identity(data / 'read-model' / (model['generation'] + '.sqlite'))
    with sqlite3.connect(data / 'collector/checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET snapshot=NULL WHERE snapshot IS NOT NULL")
    next_model = read_model(data, reserve_bytes=0)
    assert input_identity(data / 'read-model' / (next_model['generation'] + '.sqlite')) != before


def test_api_distinguishes_prepared_and_published_and_does_not_expose_paths(tmp_path):
    write_json(tmp_path / 'public-release-status.json', {'state': 'candidate_ready', 'automatic': False,
               'candidate': '/private/candidate.json', 'public_release': False, 'release_id': 'pinned'})
    result = publication_status(tmp_path)
    assert result['acquired_is_not_published'] and not result['automatic']
    assert 'candidate' not in result and '/private' not in json.dumps(result)
