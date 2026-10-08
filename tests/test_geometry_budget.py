import hashlib
import json
import pytest
from pipeline.geometry_budget import verify_geometry_budgets

def geo_fixture(tmp_path):
    source = {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'geometry': {'type': 'GeometryCollection', 'geometries': [
            {'type': 'Point', 'coordinates': [127, 36, 3]},
            {'type': 'LineString', 'coordinates': [[127, 36], [128, 37], [129, 38]]}]}, 'properties': {}},
        {'type': 'Feature', 'geometry': None, 'properties': {}}]}
    payload = json.dumps(source).encode()
    (tmp_path/'water.geojson').write_bytes(payload)
    return {'id': 'water', 'format': 'geojson', 'url': '/data/water.geojson',
            'sha256': hashlib.sha256(payload).hexdigest()}


def test_geojson_budget_fills_every_missing_field_even_when_vertex_count_exists(tmp_path):
    asset = geo_fixture(tmp_path)
    asset['vertex_count'] = 4
    report = verify_geometry_budgets([asset], tmp_path, fill_missing=True)
    assert asset['feature_count'] == 2
    assert asset['byte_length'] == (tmp_path/'water.geojson').stat().st_size
    assert report['filled'][0]['filled'] == ['byte_length', 'feature_count']
    assert verify_geometry_budgets([asset], tmp_path)['passed']


@pytest.mark.parametrize('field,wrong', [('vertex_count', 3), ('feature_count', 1), ('byte_length', 1)])
def test_geojson_budget_rejects_underreported_or_missing_actual_work(tmp_path, field, wrong):
    asset = geo_fixture(tmp_path)
    with pytest.raises(ValueError, match='Missing GeoJSON'):
        verify_geometry_budgets([asset], tmp_path)
    verify_geometry_budgets([asset], tmp_path, fill_missing=True)
    asset[field] = wrong
    with pytest.raises(ValueError, match='Incorrect GeoJSON work budget '+field):
        verify_geometry_budgets([asset], tmp_path, fill_missing=True)
