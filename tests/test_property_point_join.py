import hashlib
import json
from pathlib import Path

from pipeline.property_point_join import match_identities


def seoul(code='A12345678', name='헬리오시티아파트', number='345'):
    return {'APT_CD': code, 'APT_NM': name, 'CTPV_ADDR': '서울',
            'SGG_ADDR': '송파구', 'RDN_ADDR': '송파대로',
            'ROAD_DADDR': number,
            'APT_RDN_ADDR': f'서울특별시 송파구 송파대로 {number}'}


def sale(seq='11710-8865', name='헬리오시티', number='00345'):
    return {'sggCd': '11710', 'aptSeq': seq, 'aptNm': name,
            'roadNmSggCd': '11710', 'roadNm': '송파대로',
            'roadNmBonbun': number, 'roadNmBubun': '00000'}


def match(rows, sales, known=None):
    return match_identities(rows, [('11710', item) for item in sales],
                            {'송파구': '11710'},
                            {'molit-apt:11710:11710-8865'} if known is None else known)


def test_unique_official_road_address_and_name_links_helio_despite_different_lot():
    matches, audit = match([seoul()], [sale()])
    assert matches == {'A12345678': 'molit-apt:11710:11710-8865'}
    assert audit['matched'] == 1


def test_ambiguous_or_conflicting_road_address_never_links():
    assert match([seoul(), seoul(code='B12345678')], [sale()])[0] == {}
    assert match([seoul()], [sale(), sale(seq='11710-123')])[0] == {}
    assert match([seoul()], [sale(), sale(number='00347')])[0] == {}
    assert match([seoul(name='다른 아파트')], [sale()])[0] == {}
    assert match([seoul()], [sale()], set())[0] == {}
    assert match([seoul(number='345')], [sale(number='00346')])[0] == {}


def test_published_point_asset_is_pinned_and_keeps_geometry_quality_separate():
    path = Path(__file__).resolve().parents[1] / 'src/data/seoul-kapt-points-33058dae0a1d86c3.geojson'
    body = path.read_bytes()
    assert hashlib.sha256(body).hexdigest() == '33058dae0a1d86c302b2f1c5b0dff9d71241a60031880f4f738c9fe506611792'
    features = json.loads(body)['features']
    linked = [row for row in features if 'property_complex_id' in row['properties']]
    assert len(features) == 2780 and len(linked) == 848
    assert all(row['properties']['coordinate_status'] == 'provider_xy_crs_unconfirmed' for row in linked)
    assert next(row for row in linked if row['id'] == 'A10025850')['properties']['property_complex_id'] == 'molit-apt:11710:11710-8865'
    assert b'TELNO' not in body and b'phone' not in body and b'price_krw' not in body


def test_recent_sale_map_labels_are_actual_reports_with_area_and_date():
    path = Path(__file__).resolve().parents[1] / 'src/data/seoul-kapt-points-b63b62af834062de.geojson'
    body = path.read_bytes()
    assert hashlib.sha256(body).hexdigest() == 'b63b62af834062de98b142f4caef4f8a2087bd8e713c15ba3351fcf3dc06859c'
    points = json.loads(body)['features']
    priced = [row for row in points if 'recent_sale_label' in row['properties']]
    assert len(points) == 2780 and len(priced) == 847
    assert all('property_complex_id' in row['properties'] and row['properties']['coordinate_status'] == 'provider_xy_crs_unconfirmed' for row in priced)
    helio = next(row for row in priced if row['id'] == 'A10025850')['properties']
    assert (helio['recent_sale_price_krw'], helio['recent_sale_area_m2'], helio['recent_sale_contract_date']) == (2900000000, '84.99', '2026-08-15')
    assert helio['recent_sale_label'] == '최근 신고 29억 · 84.99㎡'
    assert b'TELNO' not in body and b'phone' not in body
