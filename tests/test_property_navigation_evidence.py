import copy
import json

import pytest

from pipeline.property_navigation_evidence import build


RELEASE = 'property-' + 'a' * 16
PAGE = b'function fn_moveMap(target, aptCode, x, y) { map.setView([ y, x ], 15); }'
MARKERS = b'<dl onclick="fn_moveMap(this, \'A10000000\',\'127.1\',\'37.5\')">same name</dl>'
STAMP = '2026-10-08T16:14:03Z'


def source():
    return {'type': 'FeatureCollection', 'metadata': {'property_release_id': RELEASE},
            'features': [{'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [127.1, 37.5]},
                          'properties': {'property_complex_id': 'molit-apt:11710:11710-1',
                                         'kapt_code': 'A10000000', 'property_release_id': RELEASE,
                                         'property_aptseq_join': 'unique_official_road_address_and_name',
                                         'coordinate_status': 'provider_xy_crs_unconfirmed'}}]}


def test_confirms_navigation_only_and_keeps_source_identity_contract():
    result, manifest, audit = build(json.dumps(source()).encode(), PAGE, MARKERS, STAMP)
    data = json.loads(result)
    assert data['point_semantics'] == 'provider_map_navigation_marker'
    assert data['identity_rule'] == 'unique_official_road_address_and_name'
    assert data['points'] == [['molit-apt:11710:11710-1', 'A10000000', 127.1, 37.5]]
    assert manifest['bytes'] == len(result)
    assert audit['confirmed'] == 1
    assert 'crs' not in data and 'boundary' not in data
    assert result == build(json.dumps(source()).encode(), PAGE, MARKERS, STAMP)[0]


@pytest.mark.parametrize('fault', ['id', 'coordinate', 'name-only', 'release', 'duplicate', 'axis'])
def test_rejects_unsafe_confirmation(fault):
    data, page, markers = source(), PAGE, MARKERS
    if fault == 'id':
        markers = markers.replace(b'A10000000', b'A10000001')
    elif fault == 'coordinate':
        markers = markers.replace(b'127.1', b'127.2')
    elif fault == 'name-only':
        data['features'][0]['properties']['property_aptseq_join'] = 'name_only'
    elif fault == 'release':
        data['features'][0]['properties']['property_release_id'] = 'property-' + 'b' * 16
    elif fault == 'duplicate':
        markers += markers
    else:
        page = page.replace(b'[ y, x ]', b'[ x, y ]')
    with pytest.raises(ValueError):
        build(json.dumps(data).encode(), page, markers, STAMP)


def test_missing_or_changed_markers_do_not_remove_or_promote_existing_points():
    data = source()
    other = copy.deepcopy(data['features'][0])
    other['properties'].update(property_complex_id='molit-apt:11710:11710-2', kapt_code='A10000002')
    data['features'].append(other)
    result, _, audit = build(json.dumps(data).encode(), PAGE, MARKERS, STAMP)
    assert len(json.loads(result)['points']) == 1
    assert audit['missing_provider_id'] == ['molit-apt:11710:11710-2']
    assert len(data['features']) == 2
