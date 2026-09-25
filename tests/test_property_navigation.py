import copy
import json

import pytest

from pipeline.property_navigation import build


def source():
    return {'type': 'FeatureCollection', 'metadata': {'property_release_id': 'property-' + 'a' * 16},
            'features': [{'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [127.1, 37.5]},
                          'properties': {'property_complex_id': 'molit-apt:11710:11710-1',
                                         'kapt_code': 'A10000000', 'property_release_id': 'property-' + 'a' * 16,
                                         'property_aptseq_join': 'unique_official_road_address_and_name',
                                         'coordinate_status': 'provider_xy_crs_unconfirmed'}}]}


@pytest.mark.parametrize('mutation', ['release', 'duplicate', 'coordinate', 'join'])
def test_rejects_unsafe_release_and_identity(mutation):
    value = source()
    if mutation == 'release':
        value['features'][0]['properties']['property_release_id'] = 'property-' + 'b' * 16
    elif mutation == 'duplicate':
        value['features'].append(copy.deepcopy(value['features'][0]))
    elif mutation == 'coordinate':
        value['features'][0]['geometry']['coordinates'] = [37.5, 127.1]
    else:
        value['features'][0]['properties']['property_aptseq_join'] = 'name_only'
    with pytest.raises(ValueError):
        build(json.dumps(value).encode())


def test_keeps_unconfirmed_coordinates_and_omits_unlinked_features():
    value = source()
    value['features'].append({'properties': {'kapt_code': 'A10000001'}})
    body, manifest = build(json.dumps(value).encode())
    result = json.loads(body)
    assert len(result['points']) == 1
    assert result['coordinate_status'] == 'provider_xy_crs_unconfirmed'
    assert manifest['bytes'] == len(body)
    assert manifest['release_id'] == result['property_release_id']
