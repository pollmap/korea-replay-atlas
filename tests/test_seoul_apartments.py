import hashlib
import json

import pytest

from pipeline.seoul_apartments import SOURCE, canonical, points


def fixture(tmp_path):
    rows = [
        {'APT_CD': 'A12345678', 'APT_NM': '테스트아파트', 'XCRD': '127.02',
         'YCRD': '37.52', 'TNOHSH': 420.0, 'TELNO': 'not-for-publication'},
        {'APT_CD': 'B12345678', 'APT_NM': '목록에 없는 단지', 'XCRD': '127.03',
         'YCRD': '37.53', 'TNOHSH': 50.0},
    ]
    body = canonical({'OpenAptInfo': {'RESULT': {'CODE': 'INFO-000'}, 'row': rows}})
    digest = hashlib.sha256(body).hexdigest()
    (tmp_path / (digest + '.json')).write_bytes(body)
    receipt = {'source': SOURCE, 'dataset': 'OpenAptInfo', 'declared_rows': 2,
               'collected_rows': 2, 'retrieved_at': '2026-09-23T00:00:00Z',
               'pages': [{'sha256': digest, 'bytes': len(body), 'rows': 2}]}
    kapt = {'complexes': [{'kapt_code': 'A12345678'}],
            'retrieved_until': '2026-09-20T00:00:00Z'}
    return receipt, kapt, digest


def test_only_exact_kapt_identity_is_displayed_without_private_fields_or_price(tmp_path):
    receipt, kapt, _ = fixture(tmp_path)
    result, audit = points(receipt, tmp_path, kapt)
    assert audit['matched_points'] == 1
    assert audit['not_in_kapt_list'] == 1
    feature = result['features'][0]
    assert feature['id'] == 'A12345678'
    assert feature['geometry']['coordinates'] == [127.02, 37.52]
    assert feature['properties']['coordinate_status'] == 'provider_xy_crs_unconfirmed'
    assert feature['properties']['property_aptseq_join'] == 'not_established'
    assert b'not-for-publication' not in canonical(result)
    assert b'price' not in canonical(result)


def test_changed_raw_page_cannot_be_published(tmp_path):
    receipt, kapt, digest = fixture(tmp_path)
    (tmp_path / (digest + '.json')).write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='raw_page_hash_mismatch'):
        points(receipt, tmp_path, kapt)


def test_duplicate_provider_id_is_rejected(tmp_path):
    receipt, kapt, digest = fixture(tmp_path)
    path = tmp_path / (digest + '.json')
    value = json.loads(path.read_bytes())
    value['OpenAptInfo']['row'][1]['APT_CD'] = 'A12345678'
    body = canonical(value)
    updated = hashlib.sha256(body).hexdigest()
    (tmp_path / (updated + '.json')).write_bytes(body)
    receipt['pages'][0].update(sha256=updated, bytes=len(body))
    with pytest.raises(ValueError, match='invalid_or_duplicate_kapt_code'):
        points(receipt, tmp_path, kapt)
