"""Synthetic cases except the explicitly versioned OSM contract values."""
from copy import deepcopy
import pytest
from pipeline.height_quality import evaluate_feature


def feature(**props):
    return {'id': 'fixture', 'properties': {'height': 12., 'base_height': 30.,
        'min_height': 0., 'height_method': 'source', 'dataset_version': '2026-08-19.0', **props}}


def test_source_preserved_without_mutation():
    source = feature(height=0.01, num_floors=3)
    original = deepcopy(source)
    result = evaluate_feature(source)
    assert source == original
    assert result['raw_height'] == 0.01 and result['render_height'] is None
    assert 'source_height_below_1m_review' in result['quality_flags']


@pytest.mark.parametrize('base', [-78.74834622886921, -33.123891037655376, -30.409142864730146])
def test_low_ground_not_clamped_or_rendered(base):
    source = feature(base_height=base)
    result = evaluate_feature(source)
    assert not result['render_eligible']
    assert source['properties']['base_height'] == base
    assert 'ellipsoidal_base_below_minus_30m_review' in result['quality_flags']


@pytest.mark.parametrize('identity,height,minimum,upstream', [
    ('c883dd77-ca5c-47e0-82d7-888ee80f1bf7', 18., 15., 'w711239221@3'),
    ('35366230-3166-3235-B463-376261376263', 112., 100., 'w1214950322@1'),
])
def test_osm_ground_to_top_not_double_added(identity, height, minimum, upstream):
    source = feature(height=height, min_height=minimum)
    source['id'] = identity
    result = evaluate_feature(source)
    assert result['render_height'] == height
    assert result['render_min_height'] == minimum
    assert result['render_height'] - result['render_min_height'] == height - minimum
    assert result['upstream_record_id'] == upstream
    source['properties']['dataset_version'] = 'another-release'
    assert not evaluate_feature(source)['render_eligible']


def test_unverified_floating_height_cannot_self_certify():
    result = evaluate_feature(feature(height=18, min_height=15, height_semantics='ground_to_top'))
    assert result['render_height'] is None
    assert result['height_semantics'] == 'unresolved'


@pytest.mark.parametrize('method', ['floors', 'ghsl_cell_average_2018'])
def test_estimates_retained_but_not_source_or_surveyed(method):
    result = evaluate_feature(feature(height_method=method))
    assert result['render_eligible'] and result['quality_state'] == 'estimated'
    assert result['raw_height'] is None and not result['ground_accuracy_verified']


@pytest.mark.parametrize('props', [{'height': float('nan')}, {'height': True},
    {'height': -1}, {'base_height': float('inf')}, {'min_height': -1}, {'min_height': 12}])
def test_invalid_numeric_geometry_never_rendered(props):
    assert not evaluate_feature(feature(**props))['render_eligible']


def test_unknown_and_record_id_preserved():
    result = evaluate_feature(feature(height=None, height_method='unknown', provenance={'source_record_id': 'source-17'}))
    assert result['quality_state'] == 'unknown' and result['source_record_id'] == 'source-17'


def test_changed_verified_value_requires_new_evidence():
    source = feature(height=19, min_height=15)
    source['id'] = 'c883dd77-ca5c-47e0-82d7-888ee80f1bf7'
    assert not evaluate_feature(source)['render_eligible']


def test_snapshot_audit_reconciles_and_refuses_tampered_input(tmp_path, monkeypatch):
    import hashlib
    import json
    from pipeline.height_quality import audit
    import pipeline.building_identity_audit as identity
    path = tmp_path / '.local/silver/buildings/test.geojson'
    path.parent.mkdir(parents=True)
    data = json.dumps({'type': 'FeatureCollection', 'features': [feature(), feature(height=.01)]}).encode()
    path.write_bytes(data)
    asset = {'id': 'normalized-buildings-fixture', '_private': True, 'format': 'geojson',
             'path': str(path), 'count': 2, 'sha256': hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(identity, 'capture_assets', lambda _: {'assets': [asset], 'sha256': 'fixture'})
    report = audit(tmp_path, tmp_path / 'quality.json')
    assert report['counts'] == {'rows': 2, 'render_eligible': 1, 'footprint_only': 1}
    assert path.read_bytes() == data
    path.write_bytes(data + b' ')
    with pytest.raises(ValueError, match='hash/count'):
        audit(tmp_path, tmp_path / 'rejected.json')
    assert not (tmp_path / 'rejected.json').exists()
