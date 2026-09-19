"""Read-only coverage snapshot; processing coverage is not real-world completeness.

Run: python -m pipeline.coverage_report
No network, credentials, publisher changes, or running-job intervention.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import unquote, urlsplit
from .building_counts import BUILDING_COUNT_FIELDS, building_count_contract

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SOURCE_ROWS = 3_730_526
NOTICE = ('셀 처리율은 확보한 Overture 원천의 지리 셀 변환 진행률입니다. '
          '대한민국 모든 실제 건물의 존재·위치·높이·지붕 형상이 100% 검증되었다는 뜻이 아닙니다.')


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def read_json(path):
    data = Path(path).read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def local_asset_path(root, url):
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith('/data/'):
        raise ValueError('Only local /data/ assets can be audited')
    public = (Path(root) / 'public' / 'data').resolve()
    path = (public / unquote(parsed.path.removeprefix('/data/'))).resolve()
    if not path.is_relative_to(public):
        raise ValueError('Asset escapes public/data')
    return path


def read_places(path):
    text = Path(path).read_text(encoding='utf-8')
    block = text.split('export const PLACES:', 1)[1].split('];', 1)[0]
    pattern = (r"\{id:\s*'([^']+)',name:\s*'([^']+)',region:\s*'([^']+)',"
               r'lon:\s*([-\d.]+),lat:\s*([-\d.]+),range:\s*([\d.]+)\}')
    places = [dict(id=i, name=n, region=r, lon=float(x), lat=float(y))
              for i, n, r, x, y, _ in re.findall(pattern, block)]
    if not places or len(places) != block.count('{id:'):
        raise ValueError('PLACES syntax changed; update parser instead of silently omitting places')
    return places


def inside(bbox, lon, lat):
    return (isinstance(bbox, (list, tuple)) and len(bbox) == 4
            and bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3])


def summarize_jobs(manifest, jobs, mesh_version='m3'):
    cells = manifest['cells']
    if len({c['name'] for c in cells}) != len(cells):
        raise ValueError('Duplicate cell IDs in source manifest')
    version = manifest['source']['sha256'] + ':' + mesh_version
    rows = {row['id']: row for row in jobs}
    result = []
    for cell in cells:
        job = rows.get(cell['name'])
        status = ('not_started' if job is None else 'stale_version'
                  if job['source_hash'] != version else job['status'])
        result.append({'id': cell['name'], 'source_rows': cell['count'], 'bbox': cell['bbox'],
                       'status': status, 'finished_at': job.get('finished_at') if job else None,
                       'reason': job.get('reason') if job else None})
    complete = [c for c in result if c['status'] == 'complete']
    accepted = manifest['summary']['accepted']
    return {'source_accepted_rows': accepted, 'expected_source_rows': EXPECTED_SOURCE_ROWS,
            'expected_baseline_matches': accepted == EXPECTED_SOURCE_ROWS,
            'sum_cell_source_rows': sum(c['count'] for c in cells),
            'source_rows_reconcile': sum(c['count'] for c in cells) == accepted,
            'raw_bbox_rows': manifest['summary'].get('input_rows'),
            'outside_country_mask_rows': manifest['summary'].get('outside_mask'),
            'invalid_source_rows': manifest['summary'].get('invalid'),
            'job_version': version, 'total_cells': len(cells), 'complete_cells': len(complete),
            'incomplete_cells': len(cells) - len(complete),
            'cell_processing_percent': round(100 * len(complete) / len(cells), 4) if cells else None,
            'processed_source_rows': sum(c['source_rows'] for c in complete),
            'unprocessed_source_rows': sum(c['source_rows'] for c in result if c['status'] != 'complete'),
            'status_counts': dict(Counter(c['status'] for c in result)), 'cells': result,
            'real_world_building_completeness': None, 'all_buildings_verified': False, 'notice': NOTICE}


def inspect_asset(root, asset):
    result = {k: asset.get(k) for k in ('id', 'layer', 'format', 'url', 'bbox', 'count',
                                       'source_id', 'version', 'sha256', 'from', 'to', 'label')}
    result.update({key: asset[key] for key in ('feature_count', *BUILDING_COUNT_FIELDS) if key in asset})
    result.update(local_file_exists=False, manifest_hash_matches=None,
                  bbox_basis='catalog_extent_not_feature_presence', detail=None)
    try:
        path = local_asset_path(root, asset['url'])
        result['local_file_exists'] = path.is_file()
        if not path.is_file():
            return result
        if asset['format'] in ('3d-tiles', 'quantized-mesh', 'imagery', 'replay'):
            payload, sha = read_json(path)
            result['manifest_hash_matches'] = sha == asset.get('sha256')
            if asset['format'] == '3d-tiles':
                region = payload.get('root', {}).get('boundingVolume', {}).get('region')
                if region and len(region) == 6:
                    result['content_bbox'] = [math.degrees(v) for v in region[:4]]
                    result['bbox_basis'] = 'tileset_root_extent_not_feature_presence'
            elif asset['format'] == 'quantized-mesh':
                result['detail'] = terrain_detail(path, payload)
            elif asset['format'] == 'imagery':
                result['detail'] = {'frames': [{k: f.get(k) for k in
                    ('time', 'valid_until', 'unit', 'evidence_type', 'label', 'url', 'bbox')}
                    for f in payload.get('frames', [])]}
            else:
                result['detail'] = {'top_level_keys': sorted(payload),
                                    'notice': '운행 기록/계산 위치와 연속 GPS 관측을 구분해야 합니다.'}
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['inspection_error'] = type(error).__name__ + ': ' + str(error)
    return result


def terrain_detail(path, payload):
    levels = []
    available = payload.get('available', [])
    if payload.get('projection') != 'EPSG:4326' or payload.get('scheme') != 'tms':
        return {'inspection_error': 'Unsupported terrain projection/scheme', 'levels': []}
    for level, rectangles in enumerate(available):
        tiles = set()
        for r in rectangles:
            size = (r['endX'] - r['startX'] + 1) * (r['endY'] - r['startY'] + 1)
            if size < 0 or size + len(tiles) > 200_000:
                raise ValueError('Terrain availability exceeds bounded audit size')
            tiles.update((x, y) for x in range(r['startX'], r['endX'] + 1)
                         for y in range(r['startY'], r['endY'] + 1))
        if tiles:
            present = {(x, y) for x, y in tiles
                       if (path.parent / str(level) / str(x) / f'{y}.terrain').is_file()}
            levels.append({'level': level, 'declared_tiles': len(tiles), 'present_tiles': len(present),
                           'missing_tiles': len(tiles - present),
                           'present_xy': [list(xy) for xy in sorted(present)]})
    return {'projection': payload.get('projection'), 'scheme': payload.get('scheme'),
            'declared_minzoom': payload.get('minzoom'), 'declared_maxzoom': payload.get('maxzoom'),
            'levels': levels, 'notice': '최대 level은 일부 지역 상세 타일입니다. 전국 동일 해상도가 아닙니다.'}


def point_terrain_levels(assets, lon, lat):
    found = set()
    for asset in assets:
        if asset['format'] != 'quantized-mesh' or asset.get('manifest_hash_matches') is not True:
            continue
        for data in (asset.get('detail') or {}).get('levels', []):
            z = data['level']
            x = min(2 ** (z + 1) - 1, math.floor((lon + 180) / 360 * 2 ** (z + 1)))
            y = min(2 ** z - 1, math.floor((lat + 90) / 180 * 2 ** z))
            if [x, y] in data['present_xy']:
                found.add(z)
    return sorted(found)


def summarize_public(assets, places):
    buildings = [a for a in assets if a['layer'] == 'buildings']
    models = [a for a in buildings if a['format'] == '3d-tiles']
    footprints = [a for a in buildings if a['format'] == 'geojson']
    declared, count_errors = [], []
    for asset in models:
        try:
            counts = building_count_contract(asset)
            if counts is not None:
                declared.append(counts)
        except ValueError as error:
            count_errors.append({'id': asset['id'], 'error': str(error)})
    regions = []
    for place in places:
        candidates = [a for a in assets if inside(a.get('content_bbox', a.get('bbox')), place['lon'], place['lat'])]
        regions.append({**place, 'bbox_candidate_assets': [{k: a.get(k) for k in
            ('id', 'layer', 'format', 'bbox_basis', 'local_file_exists', 'manifest_hash_matches')}
            for a in candidates],
            'building_3d_bbox_assets': [a['id'] for a in candidates if a['format'] == '3d-tiles'
                                      and a['layer'] == 'buildings' and a['local_file_exists']
                                      and a.get('manifest_hash_matches') is True],
            'terrain_present_levels': point_terrain_levels(assets, place['lon'], place['lat']),
            'entire_city_coverage_verified': False, 'actual_feature_at_point_verified': False})
    return {'buildings': {'published_3d_model_rows': sum(a.get('count') or 0 for a in models),
             'locally_present_3d_model_rows': sum(a.get('count') or 0 for a in models
                 if a['local_file_exists'] and a.get('manifest_hash_matches') is True),
             'national_3d_model_rows': sum(a.get('count') or 0 for a in models if a['id'].startswith('buildings-kr-')),
             'other_3d_model_rows': sum(a.get('count') or 0 for a in models if not a['id'].startswith('buildings-kr-')),
             'footprint_rows': sum(a.get('count') or 0 for a in footprints),
             'declared_logical_source_building_rows': sum(a['logical_source_building_count'] for a in declared) if declared else None,
             'declared_parts_parent_rows': sum(a['building_parts_parent_count'] for a in declared) if declared else None,
             'declared_parts_feature_rows': sum(a['building_parts_feature_count'] for a in declared) if declared else None,
             'logical_count_declared_assets': len(declared),
             'logical_count_unknown_assets': len(models) - len(declared),
             'building_count_contract_errors': count_errors,
             'unique_buildings_verified': None, 'deduplication_verified_in_this_audit': False,
             'count_basis': '공개 catalog count는 부품을 포함한 렌더 피처 행 합계입니다. 논리 원본 건물 수는 별도 명시된 자산만 합산합니다. 자산 간 건물 ID 중복을 다시 검사하지 않았으며 실제 브라우저 렌더링 성공 개수가 아닙니다.',
             'height_notice': '원천 높이, 층수×3m, 2018년 GHSL 100m 평균 추정 높이가 섞여 있습니다. 높이 없는 외곽은 3D 모델 수에서 제외합니다.'},
            'regions': regions,
            'missing_asset_files': [a['id'] for a in assets if not a['local_file_exists']],
            'manifest_hash_failures': [a['id'] for a in assets if a.get('manifest_hash_matches') is False],
            'asset_formats': dict(Counter(a['format'] for a in assets)),
            'assets': assets}


def integration_report(assets, catalog):
    result = {}
    notices = {
        'rail': 'GeoJSON 선로/역과 replay 운행 기록을 구분합니다. KORAIL 역별 시각과 역간 계산 위치는 전국 열차의 연속 GPS 관측이 아닙니다.',
        'bus': '공개 자산이 없으면 버스 실시간 위치가 연결된 상태가 아닙니다. API 키 존재 여부는 이 보고서에서 읽지 않습니다.',
        'depth': '공식 역사 심도 점과 역간 보간 선입니다. 실제 지하 시설의 전체 3D 측량 모델이 아닙니다.',
        'satellite': '프레임의 관측 날짜를 확인해야 합니다. 2022년 공식 예제는 현재 실시간 위성 영상이 아닙니다.',
        'radar': '공개 영상의 색상을 보존한 프레임입니다. 영상 갱신 시각과 수집 날짜를 분리하며 숫자 강우량/결측을 역산하지 않습니다.',
        'infrastructure': '도로·시설의 원천 형상입니다. 자산의 bbox가 넓다고 모든 위치의 시설이 포함되었다고 판단하지 않습니다.',
    }
    for layer, notice in notices.items():
        selected = [a for a in assets if a['layer'] == layer]
        frame_times = [f['time'] for a in selected for f in (a.get('detail') or {}).get('frames', [])
                       if f.get('time')]
        state = next((v for v in catalog.get('layers', []) if v['id'] == layer), {})
        result[layer] = {'catalog_state': state.get('state'), 'catalog_reason': state.get('reason'),
            'published_asset_count': len(selected), 'local_file_count': sum(a['local_file_exists'] for a in selected),
            'formats': dict(Counter(a['format'] for a in selected)),
            'time_from': min((a['from'] for a in selected if a.get('from')), default=None),
            'time_to': max((a['to'] for a in selected if a.get('to')), default=None),
            'frame_observation_from': min(frame_times, default=None),
            'frame_observation_to': max(frame_times, default=None),
            'assets': [{k: a.get(k) for k in ('id', 'format', 'count', 'from', 'to', 'label', 'detail')}
                       for a in selected], 'automatic_refresh_verified': False,
            'live_api_call_verified': False, 'notice': notice}
    return result


def capture(root=ROOT, mesh_version='m3'):
    root = Path(root)
    started = utcnow()
    pointer, pointer_sha = read_json(root / '.local/national/current.json')
    manifest, manifest_sha = read_json(pointer['path'])
    current, _ = read_json(root / 'public/data/catalog.json')
    release = current['release_id']
    if not re.fullmatch(r'pub-[a-f0-9]+', release):
        raise ValueError('Expected immutable published catalog release ID')
    catalog_path = root / 'public/data/releases' / f'{release}.json'
    catalog, catalog_sha = read_json(catalog_path)
    if catalog['release_id'] != release:
        raise ValueError('Immutable release ID mismatch')
    with sqlite3.connect((root / '.local/catalog.sqlite').resolve().as_uri() + '?mode=ro', uri=True, timeout=10) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        jobs = [dict(row) for row in db.execute('SELECT id,source_hash,status,finished_at,reason FROM national_jobs')]
        db.rollback()
    jobs_at = utcnow()
    places_path = root / 'shared/sources.ts'
    places = read_places(places_path)
    assets = [inspect_asset(root, a) for a in catalog['assets']]
    return {'schema_version': 1, 'notice': NOTICE,
        'snapshot': {'started_at': started, 'jobs_read_at': jobs_at, 'finished_at': utcnow(),
            'catalog_release_id': release, 'catalog_generated_at': catalog.get('generated_at'),
            'catalog_sha256': catalog_sha, 'catalog_path': str(catalog_path),
            'national_pointer_sha256': pointer_sha, 'manifest_sha256': manifest_sha,
            'manifest_path': pointer['path'], 'source_sha256': manifest['source']['sha256'],
            'jobs_sha256': hashlib.sha256(json.dumps(jobs, sort_keys=True).encode()).hexdigest(),
            'jobs_snapshot': jobs, 'places_sha256': hashlib.sha256(places_path.read_bytes()).hexdigest(),
            'consistency': '공개 immutable catalog를 한 번 고정한 뒤 SQLite 읽기 트랜잭션을 한 번 취득했습니다. 서로 다른 파일의 원자적 동시 스냅샷은 아닙니다.',
            'deployment_verified': False},
        'processing': summarize_jobs(manifest, jobs, mesh_version),
        'public': summarize_public(assets, places), 'integrations': integration_report(assets, catalog)}


def markdown(report):
    snapshot, processing, public = report['snapshot'], report['processing'], report['public']
    buildings = public['buildings']
    logical_rows = buildings.get('declared_logical_source_building_rows')
    logical_label = f'{logical_rows:,}' if logical_rows is not None else '명시 없음'
    lines = ['# 대한민국 데이터 커버리지', '', f"스냅샷: {snapshot['finished_at']} (UTC)", '',
        f"공개 데이터 릴리스: `{snapshot['catalog_release_id']}` · 생성 {snapshot['catalog_generated_at']}", '',
        NOTICE, '', '## 원천 변환과 공개 건물', '', '| 지표 | 확인값 |', '|---|---:|',
        f"| 국가 마스크 안 확보 원천 행 | {processing['source_accepted_rows']:,} |",
        f"| 완료 / 전체 지리 셀 | {processing['complete_cells']:,} / {processing['total_cells']:,} |",
        f"| 미완료 지리 셀 | {processing['incomplete_cells']:,} |",
        f"| 지리 셀 처리율 | {processing['cell_processing_percent']}% |",
        f"| 완료 셀에 속하는 원천 행 | {processing['processed_source_rows']:,} |",
        f"| 미완료 셀에 속하는 원천 행 | {processing['unprocessed_source_rows']:,} |",
        f"| 공개 3D 모델 행 합계 | {buildings['published_3d_model_rows']:,} |",
        f"| 논리 원본 건물 행 (명시된 자산만) | {logical_label} |",
        f"| 논리 원본 건물 수 미명시 자산 | {buildings.get('logical_count_unknown_assets', 0):,} |",
        f"| 전국 셀 3D 모델 행 / 기타 지역 3D 모델 행 | {buildings['national_3d_model_rows']:,} / {buildings['other_3d_model_rows']:,} |",
        f"| 별도 건물 외곽 GeoJSON 행 | {buildings['footprint_rows']:,} |", '',
        buildings['count_basis'], '', buildings['height_notice'], '',
        '원천 행은 공개 원천에서 확보한 객체 수입니다. 전국 실제 건물 총수의 분모가 아닙니다. '
        '완료 원천 행에는 표현에서 제외된 중복 후보, 부적합 형상, 높이 없는 건물이 포함될 수 있으므로 3D 모델 수와 같지 않습니다.', '',
        '## 주요 탐색 위치', '',
        '아래는 각 지역의 지정 탐색 중심점이 파일 범위에 들어가는지를 검사한 결과입니다. '
        '행정구역 전체 커버리지나 그 점의 실제 객체 존재를 증명하지 않습니다. 건물 3D는 catalog의 셀 범위보다 좁은 tileset root 범위를 사용합니다.', '',
        '| 위치 | 경도, 위도 | 건물 3D 범위 포함 파일 | 존재 확인 지형 level | 기타 범위 포함 형식 |',
        '|---|---|---|---|---|']
    for region in public['regions']:
        formats = sorted({a['layer'] + ':' + a['format'] for a in region['bbox_candidate_assets']
                          if a['layer'] != 'buildings' and a['local_file_exists']})
        levels = region['terrain_present_levels']
        lines.append(f"| {region['name']} ({region['region']}) | {region['lon']}, {region['lat']} | "
                     f"{', '.join(region['building_3d_bbox_assets']) or '없음'} | "
                     f"{', '.join(map(str, levels)) or '없음'} | {', '.join(formats) or '없음'} |")
    lines += ['', '지형의 level은 타일 세분 수준이며 미터 단위 위치 정확도가 아닙니다. '
              '높은 level이 일부 지점에 있어도 전국 고해상도 지형이 완성된 것으로 해석하지 않습니다.', '',
              '| 지형 자산 | level | 선언 타일 / 파일 존재 / 누락 |', '|---|---:|---:|']
    for a in public['assets']:
        if a['format'] == 'quantized-mesh':
            for level in (a.get('detail') or {}).get('levels', []):
                lines.append(f"| {a['id']} | {level['level']} | {level['declared_tiles']} / {level['present_tiles']} / {level['missing_tiles']} |")
    lines += ['', '## 교통·기상 연결', '', '| 구분 | 공개 파일 / 로컬 존재 | 형식 | 재생 유효 시간 범위 (UTC) |',
              '|---|---:|---|---|']
    for layer, item in report['integrations'].items():
        lines.append(f"| {layer} | {item['published_asset_count']} / {item['local_file_count']} | "
                     f"{json.dumps(item['formats'], ensure_ascii=False)} | {item['time_from'] or '없음'} → {item['time_to'] or '없음'} |")
    lines += [''] + [f"- **{layer}**: {item['notice']}" for layer, item in report['integrations'].items()]
    lines += [''] + [f"- **{layer} 프레임 실제 관측 시각**: {item['frame_observation_from']} → {item['frame_observation_to']} (UTC). 재생 유효 종료 시각과 다릅니다."
                    for layer, item in report['integrations'].items() if item.get('frame_observation_from')]
    lines += ['', '정적 공개 파일 존재를 검사했습니다. 이 실행은 실시간 API 호출, 자동 수집 지속 실행, '
              '브라우저 렌더링 성공, 외부 배포를 검증하지 않습니다.', '', '## 검증과 재현', '',
              f"- 원천 셀 합계 대사: {processing['source_rows_reconcile']}; 3,730,526행 기준 일치: {processing['expected_baseline_matches']}",
              f"- 로컬 자산 파일 누락: {len(public['missing_asset_files'])}; 검사한 JSON manifest 해시 불일치: {len(public['manifest_hash_failures'])}",
              f"- catalog SHA-256: `{snapshot['catalog_sha256']}`",
              f"- cells manifest SHA-256: `{snapshot['manifest_sha256']}`",
              '- 공개 immutable catalog 한 버전과 SQLite 읽기 트랜잭션 한 번을 고정했습니다. 서로 다른 파일의 완전한 동시 시점은 아니므로 변환 완료 수와 공개 모델 수가 일시적으로 어긋날 수 있습니다.',
              '- GeoJSON 본문·모든 GLB의 건물 ID 중복·측량 정확도를 전수 검증한 보고서가 아닙니다. 지형은 선언된 타일의 실제 파일 존재까지 확인합니다.',
              '- 셀별 상태, 원천 행 수, 지역별 자산 ID, 영상 프레임 시각, 지형 level별 타일 수와 누락은 `.local/audit/coverage.json`에 보존됩니다.', '',
              '```powershell', '.venv\\Scripts\\python.exe -m pytest tests/test_coverage_report.py -q',
              '.venv\\Scripts\\python.exe -m pipeline.coverage_report', '```', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--mesh-version', default='m3')
    args = parser.parse_args()
    report = capture(args.root, args.mesh_version)
    audit = args.root / '.local/audit/coverage.json'
    document = args.root / 'docs/COVERAGE.md'
    audit.parent.mkdir(parents=True, exist_ok=True)
    document.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    document.write_text(markdown(report), encoding='utf-8')
    print(json.dumps({'release': report['snapshot']['catalog_release_id'],
                      'complete_cells': report['processing']['complete_cells'],
                      'total_cells': report['processing']['total_cells'],
                      'processing_percent': report['processing']['cell_processing_percent'],
                      'published_3d_model_rows': report['public']['buildings']['published_3d_model_rows'],
                      'audit': str(audit), 'document': str(document)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
