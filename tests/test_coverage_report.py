"""Synthetic reporting contracts; fixtures must never become public map data."""
import hashlib
import json
import math
import sqlite3

import pytest

from pipeline.coverage_report import (capture, inspect_asset, integration_report,
    local_asset_path, markdown, point_terrain_levels, read_places,
    summarize_jobs, summarize_public, terrain_detail)


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def manifest():
    return {'source': {'sha256': 'source'}, 'summary': {'accepted': 30, 'input_rows': 35,
        'outside_mask': 5, 'invalid': 0}, 'cells': [
            {'name': 'kr-a', 'count': 10, 'bbox': [127, 36, 127.1, 36.1]},
            {'name': 'kr-b', 'count': 20, 'bbox': [127.1, 36, 127.2, 36.1]}]}


def job(id='kr-a', version='source:m3', status='complete'):
    return {'id': id, 'source_hash': version, 'status': status, 'finished_at': '2026-09-16T00:00:00Z', 'reason': None}


def asset(**changes):
    return dict({'id': 'buildings-kr-a', 'layer': 'buildings', 'format': '3d-tiles',
        'bbox': [127, 36, 127.1, 36.1], 'count': 8, 'local_file_exists': True,
        'manifest_hash_matches': True}, **changes)


def test_stale_mesh_version_is_not_completed():
    report = summarize_jobs(manifest(), [job(), job('kr-b', 'source:m2')])
    assert report['cell_processing_percent'] == 50
    assert report['processed_source_rows'] == 10
    assert report['unprocessed_source_rows'] == 20
    assert report['status_counts'] == {'complete': 1, 'stale_version': 1}
    assert report['source_rows_reconcile']
    assert report['real_world_building_completeness'] is None
    assert not report['all_buildings_verified']


def test_all_cells_processed_never_claims_all_real_buildings():
    report = summarize_jobs(manifest(), [job(), job('kr-b')])
    assert report['cell_processing_percent'] == 100
    assert report['all_buildings_verified'] is False
    assert report['expected_baseline_matches'] is False


def test_duplicate_manifest_cells_rejected_and_failed_jobs_preserved():
    duplicate = manifest()
    duplicate['cells'].append(duplicate['cells'][0])
    with pytest.raises(ValueError, match='Duplicate'):
        summarize_jobs(duplicate, [])
    report = summarize_jobs(manifest(), [job(status='failed')])
    assert report['status_counts'] == {'failed': 1, 'not_started': 1}


def test_counts_keep_3d_footprints_and_possible_duplicates_separate():
    assets = [asset(), asset(id='buildings-pilot'),
              asset(id='unknown-buildings-kr-a', format='geojson', count=2)]
    report = summarize_public(assets, [])['buildings']
    assert report['published_3d_model_rows'] == 16
    assert report['national_3d_model_rows'] == 8
    assert report['other_3d_model_rows'] == 8
    assert report['footprint_rows'] == 2
    assert report['unique_buildings_verified'] is None
    assert not report['deduplication_verified_in_this_audit']


def test_actual_tileset_extent_prevents_nominal_cell_false_positive(tmp_path):
    path = tmp_path / 'public/data/test/tileset.json'
    sha = save(path, {'root': {'boundingVolume': {'region':
         [math.radians(v) for v in [127.01, 36.01, 127.02, 36.02]] + [0, 20]}}})
    inspected = inspect_asset(tmp_path, asset(url='/data/test/tileset.json', sha256=sha))
    places = [{'id': 'outside-root', 'name': '검증', 'region': '가상', 'lon': 127.05, 'lat': 36.05}]
    report = summarize_public([inspected], places)
    assert inspected['manifest_hash_matches']
    assert report['regions'][0]['building_3d_bbox_assets'] == []
    assert report['regions'][0]['entire_city_coverage_verified'] is False


def test_terrain_counts_deduplicate_and_check_actual_missing_files(tmp_path):
    path = tmp_path / 'layer.json'
    rectangle = {'startX': 0, 'endX': 1, 'startY': 0, 'endY': 0}
    data = {'scheme': 'tms', 'projection': 'EPSG:4326', 'minzoom': 0, 'maxzoom': 1,
            'available': [[rectangle, rectangle], [{'startX': 3, 'endX': 3, 'startY': 1, 'endY': 1}]]}
    tile = tmp_path / '0/1/0.terrain'
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b'fixture')
    detail = terrain_detail(path, data)
    assert detail['levels'][0]['declared_tiles'] == 2
    assert detail['levels'][0]['present_tiles'] == 1
    assert detail['levels'][1]['missing_tiles'] == 1
    terrains = [asset(format='quantized-mesh', detail=detail)]
    assert point_terrain_levels(terrains, 127, 36) == [0]


def test_paths_are_local_and_cannot_escape_public(tmp_path):
    for url in ('https://example.com/a', '/data/%2e%2e/secret', '/other/file'):
        with pytest.raises(ValueError):
            local_asset_path(tmp_path, url)


def test_missing_asset_and_wrong_hash_are_not_locally_verified(tmp_path):
    absent = inspect_asset(tmp_path, asset(url='/data/absent.json', sha256='no'))
    assert not absent['local_file_exists']
    save(tmp_path / 'public/data/test.json', {})
    wrong = inspect_asset(tmp_path, asset(url='/data/test.json', sha256='no'))
    assert wrong['manifest_hash_matches'] is False
    assert summarize_public([wrong, absent], [])['buildings']['locally_present_3d_model_rows'] == 0


def test_weather_dates_and_missing_bus_not_promoted_to_live():
    selected = [asset(id='satellite-old', layer='satellite', format='imagery',
        count=1, detail={'frames': [{'time': '2022-01-01T00:00:00Z'}]},
        **{'from': '2022-01-01T00:00:00Z', 'to': '2022-01-01T00:10:00Z'})]
    report = integration_report(selected, {'layers': []})
    assert report['satellite']['time_from'].startswith('2022')
    assert report['satellite']['frame_observation_to'] == '2022-01-01T00:00:00Z'
    assert not report['satellite']['live_api_call_verified']
    assert report['bus']['published_asset_count'] == 0
    assert not report['radar']['automatic_refresh_verified']


def test_capture_reads_one_immutable_catalog_and_leaves_inputs_untouched(tmp_path):
    cells_path = tmp_path / '.local/national/v/cells.json'
    save(cells_path, manifest())
    save(tmp_path / '.local/national/current.json', {'path': str(cells_path)})
    release = 'pub-abcdef'
    save(tmp_path / 'public/data/catalog.json', {'release_id': release, 'assets': [asset()]})
    save(tmp_path / f'public/data/releases/{release}.json', {'release_id': release,
        'generated_at': '2026-09-16T00:00:00Z', 'assets': [], 'layers': []})
    places_path = tmp_path / 'shared/sources.ts'
    places_path.parent.mkdir()
    places_path.write_text("export const PLACES: Place[] = [\n"
        "{id:'sample',name:'검증',region:'가상',lon:127.05,lat:36.05,range:100}\n];", encoding='utf-8')
    with sqlite3.connect(tmp_path / '.local/catalog.sqlite') as db:
        db.execute('CREATE TABLE national_jobs(id TEXT, source_hash TEXT, status TEXT, finished_at TEXT, reason TEXT)')
        db.execute('INSERT INTO national_jobs VALUES(?,?,?,?,?)', tuple(job().values()))
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = capture(tmp_path)
    assert report['public']['assets'] == []  # Never mix the mutable pointer's assets.
    assert report['processing']['complete_cells'] == 1
    assert len(report['public']['regions']) == 1
    assert not report['snapshot']['deployment_verified']
    document = markdown(report)
    assert '50.0%' in document and '100% 검증' in document
    assert all(p.read_bytes() == content for p, content in before.items())


def test_places_parser_fails_on_unrecognized_record(tmp_path):
    path = tmp_path / 'places.ts'
    path.write_text("export const PLACES: Place[] = [{id:'oops',name:'sample'}];")
    with pytest.raises(ValueError):
        read_places(path)
