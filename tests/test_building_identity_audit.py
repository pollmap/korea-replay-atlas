"""Synthetic data only. Never run a full national scan as part of these tests."""
import io
import json
import sqlite3

import pytest

from pipeline.building_identity_audit import (JsonCursor, audit, capture_assets,
    finite_number, inspect_feature, sha256)


def feature(identity='one', height=12, method='source', **properties):
    return {'type': 'Feature', 'id': identity, 'geometry': {'type': 'Polygon', 'coordinates': []},
        'properties': {'height': height, 'height_method': method, 'height_source': 'overture',
            'base_height': 40, 'min_height': 0, 'provenance': {'source_record_id': identity}, **properties}}


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return sha256(path)


def collection(features):
    return {'type': 'FeatureCollection', 'features': features}


def parent_assets(root, parent, features, model_count=None, public_source_sha=None):
    private = root / '.local/silver/buildings/synthetic' / f'{parent}.geojson'
    digest = write(private, collection(features))
    normalized = {'id': 'normalized-buildings-' + parent, '_private': True, 'format': 'geojson',
        'path': str(private), 'sha256': digest, 'count': len(features)}
    unknown = [f for f in features if f['properties']['height'] is None or f['properties']['height'] == 0]
    if model_count is None:
        model_count = len(features) - len(unknown)
    assets = [normalized]
    if model_count:
        path = root / 'public/data/buildings' / parent / 'tileset.json'
        tile_sha = write(path, {'root': {}, 'extras': {'source_sha256': public_source_sha or digest}})
        assets.append({'id': 'buildings-' + parent, 'layer': 'buildings', 'format': '3d-tiles',
            'url': '/data/buildings/' + parent + '/tileset.json', 'sha256': tile_sha, 'count': model_count})
    if unknown:
        path = root / 'public/data/buildings' / parent / 'unknown.geojson'
        unknown_sha = write(path, collection(unknown))
        assets.append({'id': 'unknown-buildings-' + parent, 'layer': 'buildings', 'format': 'geojson',
            'url': '/data/buildings/' + parent + '/unknown.geojson', 'sha256': unknown_sha, 'count': len(unknown)})
    return assets


def snapshot(assets):
    return {'captured_at': '2026-09-16T00:00:00Z', 'sha256': 'fixture-snapshot', 'assets': assets}


def run(root, assets, name='ledger.sqlite'):
    return audit(root, snapshot(assets), root / '.local/audit' / name)


def test_stream_handles_chunk_boundaries_utf8_and_nested_feature_properties():
    value = collection([feature('서울', name='features and } [ strings'), feature('two')])
    data = json.dumps(value, ensure_ascii=False).encode()
    for chunk in (1, 7, 64):
        cursor = JsonCursor(io.BytesIO(data), chunk_size=chunk)
        assert list(cursor.features()) == value['features']
        assert cursor.digest.hexdigest() == __import__('hashlib').sha256(data).hexdigest()


@pytest.mark.parametrize('data', [b'{"type":"FeatureCollection","features":[{},]}',
    b'{"type":"FeatureCollection","features":[]', b'{"type":"Other","features":[]}',
    b'{"type":"FeatureCollection","features":[],"features":[]}',
    b'{"type":"FeatureCollection","features":[]}junk'])
def test_invalid_or_truncated_collection_rejected(data):
    with pytest.raises(ValueError):
        list(JsonCursor(io.BytesIO(data), chunk_size=4).features())


def test_single_feature_memory_bound_is_enforced():
    data = json.dumps(collection([feature('x' * 500)])).encode()
    with pytest.raises(ValueError, match='bounded'):
        list(JsonCursor(io.BytesIO(data), chunk_size=16, max_value_bytes=64).features())


def test_counts_heights_and_duplicate_ids_across_parents(tmp_path):
    assets = parent_assets(tmp_path, 'kr-a', [feature('shared'), feature('unknown', None, 'unknown')])
    assets += parent_assets(tmp_path, 'pilot', [feature('shared', 9, 'floors'),
        feature('grid', 7, 'ghsl_cell_average_2018', height_source='ghsl')])
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = run(tmp_path, assets)
    assert report['scope']['normalized_rows_scanned'] == 4
    assert report['scope']['unique_feature_ids'] == 3
    assert report['all_parent_counts_reconcile']
    assert report['height_methods'] == {'source': 1, 'unknown': 1, 'floors': 1, 'ghsl_cell_average_2018': 1}
    for field in ('feature_id', 'source_record_id'):
        duplicates = report['identity_duplicates'][field]
        assert duplicates['duplicate_ids'] == 1
        assert duplicates['cross_parent_duplicate_ids'] == 1
        assert duplicates['excess_rows'] == 1
        assert duplicates['samples'][0]['id'] == 'shared'
    assert not report['real_world_completeness_verified']
    assert not report['glb_feature_tables_audited']
    assert all(p.read_bytes() == content for p, content in before.items())
    assert str(tmp_path) not in json.dumps(report)  # No private source paths in the report.


def test_hash_mismatch_quarantines_whole_asset_without_ledger_rows(tmp_path):
    assets = parent_assets(tmp_path, 'kr-a', [feature()])
    assets[0]['sha256'] = 'wrong-hash'
    report = run(tmp_path, assets)
    assert report['scope']['quarantined_assets'] == 1
    assert report['scope']['normalized_rows_scanned'] == 0
    assert not report['scope']['scan_complete']
    assert not report['all_parent_counts_reconcile']


def test_truncated_file_rolls_back_already_streamed_id_rows(tmp_path):
    assets = parent_assets(tmp_path, 'kr-a', [feature('a'), feature('b')])
    path = __import__('pathlib').Path(assets[0]['path'])
    path.write_bytes(path.read_bytes()[:-1])
    assets[0]['sha256'] = sha256(path)
    report = run(tmp_path, assets)
    assert report['scope']['normalized_rows_scanned'] == 0
    assert report['scope']['quarantined_assets'] == 1


def test_public_count_and_stale_source_mismatch_are_reported(tmp_path):
    assets = parent_assets(tmp_path, 'count', [feature()], model_count=2)
    assets += parent_assets(tmp_path, 'stale', [feature('other')], public_source_sha='older-input')
    report = run(tmp_path, assets)
    assert report['scope']['scan_complete']
    assert not report['all_parent_counts_reconcile']
    assert all(not parent['counts_reconcile'] for parent in report['parents'])
    assert not report['parents'][1]['public_models']['normalized_source_matches']


def test_finite_height_checks_and_high_height_review_is_not_quarantine(tmp_path):
    high = feature('high', 1200, base_height=-20, min_height=100)
    assert inspect_feature(high)['flags'] == ['review_height_above_1000m']
    assert not inspect_feature(high)['quarantined']
    invalid = feature('bad', float('nan'), base_height=float('inf'), min_height=-2)
    parsed = inspect_feature(invalid)
    assert 'height_non_finite_or_missing' in parsed['flags']
    assert 'base_height_non_finite_or_missing' in parsed['flags']
    assert 'min_height_negative' in parsed['flags']
    assert not finite_number(True)
    assert not finite_number(10**1000)
    assert 'min_height_above_height' in inspect_feature(feature('min', 10, min_height=20))['flags']
    assert 'height_non_positive' in inspect_feature(feature('negative', -1))['flags']
    assets = parent_assets(tmp_path, 'heights', [high, invalid])
    report = run(tmp_path, assets)
    assert report['height_flags']['review_height_above_1000m'] == 1
    assert report['scope']['quarantined_feature_rows'] == 1
    source = report['height_by_source']['source/overture']
    assert source['statistics']['height']['max'] == 1200
    assert source['statistics']['height']['invalid_count'] == 1
    assert source['statistics']['base_height']['min'] == -20
    json.dumps(report, allow_nan=False)


def test_missing_and_disagreeing_ids_stay_quarantined_without_invented_ids(tmp_path):
    missing = feature(None)
    mismatch = feature('feature-id', provenance={'source_record_id': 'source-id'})
    report = run(tmp_path, parent_assets(tmp_path, 'identity', [missing, mismatch]))
    assert report['scope']['quarantined_feature_rows'] == 2
    assert report['scope']['unique_feature_ids'] == 1
    assert report['scope']['unique_source_record_ids'] == 1
    assert report['height_flags']['feature_provenance_id_mismatch'] == 1


def test_duplicate_and_source_issue_samples_bounded_to_twenty(tmp_path):
    values = [feature(f'id-{i}', 1001+i) for i in range(30)]
    assets = parent_assets(tmp_path, 'a', values) + parent_assets(tmp_path, 'b', values)
    report = run(tmp_path, assets)
    assert report['identity_duplicates']['feature_id']['duplicate_ids'] == 30
    assert len(report['identity_duplicates']['feature_id']['samples']) == 20
    assert len(report['height_by_source']['source/overture']['samples']) == 20
    assert len(report['height_by_source']['source/overture']['high_height_review_samples']) == 20
    assert all('issue_samples' not in p and 'by_height_source' not in p for p in report['parents'])


def test_snapshot_is_fixed_and_existing_ledger_is_never_overwritten(tmp_path):
    assets = parent_assets(tmp_path, 'snapshot', [feature()])
    database = tmp_path / '.local/catalog.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,payload TEXT)')
        db.executemany('INSERT INTO assets VALUES(?,?)', [(a['id'], json.dumps(a)) for a in assets])
    fixed = capture_assets(database)
    with sqlite3.connect(database) as db:
        db.execute('DELETE FROM assets')  # Synthetic fixture changes after snapshot only.
    ledger = tmp_path / '.local/audit/identity.sqlite'
    report = audit(tmp_path, fixed, ledger)
    assert report['scope']['normalized_rows_scanned'] == 1
    before = ledger.read_bytes()
    with pytest.raises(FileExistsError):
        audit(tmp_path, fixed, ledger)
    assert ledger.read_bytes() == before


def test_unknown_public_file_count_is_read_not_trusted(tmp_path):
    assets = parent_assets(tmp_path, 'unknown', [feature('u', None, 'unknown')])
    assets[-1]['count'] = 2
    report = run(tmp_path, assets)
    checked = report['parents'][0]['public_unknown']
    assert checked['actual_feature_rows'] == 1
    assert not checked['valid']
    assert not report['all_parent_counts_reconcile']
