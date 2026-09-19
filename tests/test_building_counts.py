import pytest

from pipeline.building_counts import building_count_contract
from pipeline.coverage_report import summarize_public


def published(**changes):
    return dict(id='parts', layer='buildings', format='3d-tiles', count=3_687_718,
                feature_count=3_687_718, logical_source_building_count=3_687_673,
                building_parts_parent_count=16, building_parts_feature_count=61,
                building_parts_publication_version='building-parts-publication-1',
                local_file_exists=True, manifest_hash_matches=True) | changes


def test_parts_are_rendered_features_not_new_logical_buildings():
    counts = building_count_contract(published())
    assert counts['logical_source_building_count'] == 3_687_673
    assert counts['building_parts_feature_count'] == 61
    result = summarize_public([published()], [])['buildings']
    assert result['published_3d_model_rows'] == 3_687_718
    assert result['declared_logical_source_building_rows'] == 3_687_673
    assert result['declared_parts_parent_rows'] == 16
    assert result['declared_parts_feature_rows'] == 61
    assert result['logical_count_unknown_assets'] == 0
    assert result['unique_buildings_verified'] is None


@pytest.mark.parametrize('changes', [
    {'count': 3_687_719}, {'feature_count': 3_687_673},
    {'building_parts_parent_count': True}, {'logical_source_building_count': -1},
    {'building_parts_feature_count': 61.0}, {'building_parts_parent_count': 0},
    {'building_parts_feature_count': 0}, {'building_parts_parent_count': 3_687_674},
    {'building_parts_feature_count': 15}, {'building_parts_publication_version': ''},
    {'building_parts_publication_version': None}, {'format': 'geojson'},
    {'count': 2**53}, {'layer': 'terrain'},
])
def test_inconsistent_publication_counts_are_rejected(changes):
    with pytest.raises(ValueError, match='[Bb]uilding count contract'):
        building_count_contract(published(**changes))


def test_legacy_assets_remain_unknown_and_are_not_inferred_unique():
    legacy = dict(id='legacy', layer='buildings', format='3d-tiles', count=12,
                  local_file_exists=True, manifest_hash_matches=True)
    assert building_count_contract(legacy) is None
    report = summarize_public([legacy], [])['buildings']
    assert report['declared_logical_source_building_rows'] is None
    assert report['logical_count_unknown_assets'] == 1
    mixed = summarize_public([published(), legacy], [])['buildings']
    assert mixed['declared_logical_source_building_rows'] == 3_687_673
    assert mixed['logical_count_unknown_assets'] == 1
    assert mixed['unique_buildings_verified'] is None


def test_audit_records_count_errors_instead_of_inventing_a_correction():
    result = summarize_public([published(count=1)], [])['buildings']
    assert result['published_3d_model_rows'] == 1
    assert result['declared_logical_source_building_rows'] is None
    assert result['building_count_contract_errors'][0]['id'] == 'parts'
