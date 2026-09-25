import pytest
from pipeline.property_facts import normalize

def test_allowlist_and_missing_count_semantics():
    result=normalize({'TNOHSH':100.,'WHOL_DONG_CNT':2.,'PRK_CNTOM':0.,'MN_MTHD':'지역난방',
                      'USE_APRV_YMD':'2018-12-28 00:00:00.0','TELNO':'private-contact'})
    assert result['households']==100 and result['parking']==0
    assert result['approved_on']=='2018-12-28' and result['road_address'] is None
    assert 'private-contact' not in str(result)

def test_reject_fractional_negative_and_invalid_dates():
    result=normalize({'TNOHSH':1.2,'WHOL_DONG_CNT':-1,'PRK_CNTOM':'','USE_APRV_YMD':'2026-02-31 00:00:00.0'})
    assert all(result[key] is None for key in ('households','buildings','parking','approved_on'))
    with pytest.raises(ValueError): normalize({'BLDR':'a\ncontact'})
