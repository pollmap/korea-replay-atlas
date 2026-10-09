import csv
import io
import pytest
from pipeline.official_property_context import fees, schools


def fee_file(rows):
    body = io.StringIO(newline='')
    writer = csv.writer(body)
    writer.writerow(['아파트명', '아파트코드', '비용명', '년월일', '금액'])
    writer.writerows(rows)
    return body.getvalue().encode('cp949')


def test_fee_identity_zero_and_adjustment():
    rows = [['동명', 'A10025850', '급여', '202607', '0', ''],
            ['동명', 'B13307001', '정정', '202607', '-50', '']]
    assert fees(fee_file(rows), '202607') == [
        ['A10025850', '동명', [('급여', 0)]], ['B13307001', '동명', [('정정', -50)]]]
    with pytest.raises(ValueError, match='duplicate'):
        fees(fee_file(rows + rows[:1]), '202607')
    with pytest.raises(ValueError, match='fee_value'):
        fees(fee_file(rows), '202608')


def test_school_scope_preserves_original_point_and_duplicate_fails():
    row = {'SCHOOL_ID': 'B000002123', 'SCHOOL_NM': '학교', 'RDNMADR': '서울특별시 송파구 주소',
           'LNMADR': '', 'LONGITUDE': '127.1204994', 'LATITUDE': '37.51343915',
           'OPER_STTUS': '운영', 'REFERENCE_DATE': '2026-10-01', 'SCHOOL_SE': '초등학교'}
    assert schools([row])[0][4:6] == [127.1204994, 37.51343915]
    assert schools([{**row, 'RDNMADR': '경상북도 주소'}]) == []
    with pytest.raises(ValueError, match='school_identity'):
        schools([row, row])
