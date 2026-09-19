import pytest
from pipeline.depth import station_vertical_evidence


def row(**overrides):
    return {'지반고': '129.99', '레일면고': '117.04', '선로기준정거장깊이': '12.95', '정거장깊이': '11.85', **overrides}


def test_official_offset_arithmetic_does_not_claim_datum_verification():
    result = station_vertical_evidence(row())
    assert result['official_ground_elevation_m'] == pytest.approx(29.69)
    assert result['official_rail_elevation_m'] == pytest.approx(16.74)
    assert result['official_ground_rail_residual_m'] == pytest.approx(0)
    assert result['vertical_quality_flags'] == ['official_absolute_vertical_datum_unverified']
    assert not result['absolute_vertical_datum_verified']


def test_inconsistent_source_is_flagged_without_rewriting_values():
    result = station_vertical_evidence(row(지반고='140'))
    assert result['official_ground_level_raw_m'] == 140
    assert 'official_ground_rail_depth_inconsistent' in result['vertical_quality_flags']


@pytest.mark.parametrize('value', [None, '', 'NaN', 'Infinity'])
def test_missing_and_nonfinite_values_cannot_become_absolute_heights(value):
    result = station_vertical_evidence(row(레일면고=value))
    assert result['official_rail_elevation_m'] is None
    assert 'missing_or_invalid_rail' in result['vertical_quality_flags']
