"""Synthetic contracts only. These fixtures are never public source data."""
import csv
import json

import pytest
from pyproj import Transformer
from shapely.geometry import box, mapping
from shapely.ops import transform

from pipeline.official_buildings import SCHEMAS, normalize_record, prepare, propose_matches


GEOMETRY = box(127.43, 36.33, 127.4302, 36.3302)


def properties(table='AL_D010', **changes):
    values = {field: '0' for field in SCHEMAS[table]}
    values.update({'A0': '123', 'A1': '0000000000000000000000000123',
                   'A2': '3011010100100010000', 'A3': '3011010100',
                   'A4': '검증용 지역', 'A5': '1', 'A13': '2020-01-01',
                   'A16': '12.5', 'A19': '0000000000000000000000000010',
                   'A22': '2026-09-09', 'A23': '30110', 'A26': '4',
                   'A27': '1', 'A28': '2026-09-08'})
    if table == 'CH_D010':
        values.update({'A23': '98765432101234567890', 'A29': 'U', 'A30': '30110'})
    return {**values, **changes}


def record(**changes):
    return normalize_record(properties(**changes), mapping(GEOMETRY), table='AL_D010', source_crs=4326)


def target(id='osm-1', geometry=GEOMETRY):
    return {'type': 'Feature', 'id': id, 'geometry': mapping(geometry), 'properties': {}}


def test_official_crs_transform_preserves_height_and_string_identifiers():
    metric = transform(Transformer.from_crs(4326, 5186, always_xy=True).transform, GEOMETRY)
    result = normalize_record(properties(), mapping(metric), table='AL_D010', source_crs=5186)
    assert result['quality']['complete']
    assert result['source_record_id'].startswith('000000')
    assert result['height_m'] == 12.5
    assert result['floors'] == {'above_ground': 4, 'below_ground': 1}
    assert result['geometry']['coordinates'][0][0] == pytest.approx(mapping(GEOMETRY)['coordinates'][0][0])
    assert result['publication_state'] == 'review_only'
    assert result['raw_height_m'] == 12.5 and result['render_height_m'] is None
    assert not result['height_accuracy_verified']


def test_submetre_official_height_preserved_for_review_without_floor_replacement():
    result = record(A16='0.01', A26='3')
    assert result['height_m'] == .01 and result['raw_height_m'] == .01
    assert result['render_height_m'] is None
    assert not result['quality']['complete']
    assert 'source_height_floor_count_conflict_review' in result['height_quality_flags']


def test_change_a23_is_sequence_not_jurisdiction_and_requires_explicit_mapping():
    with pytest.raises(ValueError, match='explicit'):
        normalize_record(properties('CH_D010'), mapping(GEOMETRY), table='CH_D010', source_crs=4326)
    result = normalize_record(properties('CH_D010'), mapping(GEOMETRY), table='CH_D010', source_crs=4326, event_map={'U': 'update'})
    assert result['event'] == 'update'
    assert result['jurisdiction_code'] == '30110'
    assert result['change_sequence'] == '98765432101234567890'
    assert result['quality']['complete']


def test_sparse_delete_is_valid_journal_event_but_not_complete_building():
    result = normalize_record({'A1': 'a', 'A22': '2026-09-09', 'A23': '100', 'A29': 'D', 'A30': '30110'},
                              None, table='CH_D010', source_crs=4326, event_map={'D': 'delete'})
    assert result['event'] == 'delete'
    assert result['quality']['event_valid']
    assert not result['quality']['complete']
    assert result['height_m'] is None and result['geometry'] is None


@pytest.mark.parametrize('height', ['0', '-3', 'NaN', 'Infinity', '900', '', None, True])
def test_invalid_height_never_falls_back_to_floors(height):
    result = record(A16=height, A26='50')
    assert result['height_m'] is None and not result['quality']['complete']
    assert result['floors']['above_ground'] == 50


def test_missing_schema_fields_unknown_change_codes_and_numeric_ids_are_visible():
    values = properties('CH_D010', A29='?', A1=123)
    del values['A24']
    result = normalize_record(values, mapping(GEOMETRY), table='CH_D010', source_crs=4326, event_map={'U': 'update'})
    assert not result['quality']['complete'] and not result['quality']['event_valid']
    assert 'A24' in result['quality']['missing_fields']
    assert 'unmapped_change_code' in result['quality']['issues']
    assert 'building_id_must_be_string_to_preserve_precision' in result['quality']['issues']


def test_csv_import_is_idempotent_and_preserves_original_and_journal(tmp_path):
    source = tmp_path/'official.csv'
    with source.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=[*SCHEMAS['AL_D010'], 'geometry_wkt'])
        writer.writeheader()
        writer.writerow({**properties(), 'geometry_wkt': GEOMETRY.wkt})
    before = source.read_bytes()
    kwargs = {'table': 'AL_D010', 'source_crs': 4326, 'output_root': tmp_path/'journal'}
    first, path = prepare(source, **kwargs)
    journal_before = path.read_bytes()
    second, again = prepare(source, **kwargs)
    assert first == second and path == again and path.read_bytes() == journal_before
    assert source.read_bytes() == before
    assert first['counts']['complete'] == 1 and first['applied_to_existing_data'] is False


def test_duplicate_snapshot_identity_is_preserved_but_quarantined(tmp_path):
    source = tmp_path/'source.geojson'
    features = [{'type': 'Feature', 'properties': properties(), 'geometry': mapping(GEOMETRY)}] * 2
    source.write_text(json.dumps({'type': 'FeatureCollection', 'features': features}), encoding='utf-8')
    report, path = prepare(source, table='AL_D010', source_crs=4326, output_root=tmp_path/'journal')
    assert len(path.read_text(encoding='utf-8').splitlines()) == 2
    assert report['counts']['complete'] == 0 and report['counts']['valid_events'] == 0


def test_only_unique_high_overlap_matches_are_proposed():
    matching = propose_matches([record()], [target()])
    assert len(matching['matches']) == 1 and matching['applied'] is False
    assert matching['matches'][0]['iou'] == pytest.approx(1)
    assert matching['matches'][0]['height_m'] == 12.5
    ambiguous = propose_matches([record()], [target(), target('osm-2')])
    assert not ambiguous['matches'] and len(ambiguous['ambiguous']) == 2
    assert not propose_matches([record(A16='0')], [target()])['matches']
    assert not propose_matches([record()], [target(geometry=box(127.431, 36.331, 127.4312, 36.3312))])['matches']


def test_many_sources_for_one_target_and_duplicate_target_ids_never_auto_match():
    result = propose_matches([record(), record(A1='different')], [target()])
    assert not result['matches'] and len(result['ambiguous']) == 2
    result = propose_matches([record()], [target(), target()])
    assert not result['matches'] and result['duplicate_target_ids'] == 1


def test_wrong_crs_and_non_polygon_are_incomplete():
    result = normalize_record(properties(), mapping(GEOMETRY), table='AL_D010', source_crs=5186)
    assert not result['quality']['complete']
    result = normalize_record(properties(), {'type': 'Point', 'coordinates': [127.43, 36.33]}, table='AL_D010', source_crs=4326)
    assert not result['quality']['complete']


def test_binary_gis_input_has_explicit_unsupported_error(tmp_path):
    source = tmp_path/'source.shp'
    source.write_bytes(b'synthetic unsupported format')
    with pytest.raises(ValueError, match='Export SHP/GPKG'):
        prepare(source, table='AL_D010', source_crs=5186, output_root=tmp_path/'journal')
