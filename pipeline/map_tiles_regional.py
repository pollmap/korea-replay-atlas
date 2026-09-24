"""Add source-preserving regional detail to the existing nationwide base map.

This is an offline, resumable candidate builder. It never updates public pointers.
SGIS census geometry defines collection scope, not address or parcel accuracy.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

import shapely
from shapely.geometry import box, shape
from shapely.ops import transform

from .admin_boundaries import encoded, immutable, read_source_level, require
from .core import digest
from .height_quality import evaluate_feature
from .map_tiles import (ADMIN_ARCHIVE, ADMIN_SHA, SOURCE_CATALOG, NATIVE, TO_GEO,
    PROJECT, HARD, LOW_TARGET, DETAIL_TARGET, EXTENT, source_inputs, source_features,
    classify, add_record, open_work, sha, reserve, build_topic_tiles, pack_archives, pack_details)
from .map_tiles_land import load_baseline, local_path

VERSION = 'regional-detail-2'
ZOOMS = {'buildings': (14, 14), 'detail-roads': (14, 14)}
DEFAULT_REGIONS = ('서울특별시', '인천광역시')
V1_INGEST_TRANSFORM = '0a7c2ffd835ca90514afa0673bb49c90602d27949019f4e2f6880643e0e908f9'
DETAIL_SOURCES = (
    {'id': 'overture', 'title': 'Overture Maps · Buildings',
     'url': 'https://docs.overturemaps.org/guides/buildings/', 'license': 'ODbL 및 원천별 출처 표시',
     'description': '보존된 건물 원본의 ID와 형상. 실제 모든 건물의 완전성·측량 정확성을 보증하지 않습니다.'},
    {'id': 'ghsl', 'title': 'European Commission JRC · GHS-BUILT-H',
     'url': 'https://data.jrc.ec.europa.eu/dataset/85005901-3a49-48dd-9d19-6261354f56fe',
     'license': 'CC BY 4.0 · European Union',
     'description': '상세 속성에 보존된 2018년 100m 격자 평균 추정 높이. 개별 건물 실측 높이가 아닙니다.'},
)


def reuse_v1_index(db, donor, current):
    """Copy one pinned v1 transform's complete index for attribution-only repair.

    No geometry or tile bytes change. Source selection, height transform and MVT
    transform must all match; arbitrary old work cannot be declared equivalent.
    """
    donor = Path(donor).resolve()
    old = json.loads((donor / 'inputs.json').read_bytes())
    require(old.get('version') == 'regional-detail-1' and old.get('transform_sha256') == V1_INGEST_TRANSFORM,
            'Unapproved regional index migration')
    for key in ('baseline_catalog_sha256', 'source_catalog_sha256', 'scope', 'zooms',
                'source_assets', 'tile_transform_sha256', 'height_transform_sha256'):
        require(encoded(old.get(key)) == encoded(current.get(key)), 'Regional donor input differs: ' + key)
    require(db.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 0, 'Migration requires empty destination')
    db.execute('ATTACH DATABASE ? AS donor', (f'file:{(donor / "work/index.sqlite").as_posix()}?mode=ro',))
    try:
        db.execute('BEGIN')
        require(db.execute("SELECT value FROM donor.meta WHERE key='fingerprint'").fetchone() == (sha(encoded(old)),), 'Donor fingerprint differs')
        expected = sorted((a['id'], a['sha256']) for a in current['source_assets'])
        require(db.execute('SELECT id,sha FROM donor.sources ORDER BY id').fetchall() == expected, 'Donor source ingestion is incomplete')
        with db:
            for table in ('records', 'spatial', 'sources', 'tiles', 'stages'):
                db.execute(f'INSERT INTO {table} SELECT * FROM donor.{table}')
            db.execute('INSERT INTO stages VALUES(?,?)', ('source-registry-migration', json.dumps({
                'donor_inputs_sha256': digest(donor / 'inputs.json'), 'original_records_and_tiles_copied_exactly': True})))
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute('DETACH DATABASE donor')


def load_scope(names):
    require(names and len(set(names)) == len(names), 'Region names must be unique')
    require(digest(ADMIN_ARCHIVE) == ADMIN_SHA, 'SGIS source archive changed')
    rows, geometries, _ = read_source_level(ADMIN_ARCHIVE, 'sido')
    chosen = [(r, g) for r, g in zip(rows, geometries) if r['SIDO_NM'] in names]
    require({r['SIDO_NM'] for r, _ in chosen} == set(names), 'Unknown census region name')
    evidence = []
    projected = []
    for row, geometry in sorted(chosen, key=lambda pair: pair[0]['SIDO_CD']):
        require(row['BASE_DATE'] == '20250630', 'Unexpected census reference date')
        evidence.append({'name': row['SIDO_NM'], 'sgis_code': row['SIDO_CD'],
                         'native_geometry_sha256': sha(geometry.wkb)})
        projected.append(transform(NATIVE.transform, geometry))
    mask = shapely.union_all(projected)
    require(mask.is_valid and not mask.is_empty, 'Invalid regional selection geometry')
    shapely.prepare(mask)
    geographic = transform(TO_GEO.transform, mask)
    shapely.prepare(geographic)
    return geographic, mask, {'regions': evidence, 'reference_date': '2025-06-30',
        'boundary_kind': 'SGIS census administrative', 'archive_sha256': ADMIN_SHA}


def select_assets(assets, geographic):
    # Asset bbox is only a coarse prefilter. Every feature is checked against the
    # actual island/hole-preserving census polygons below.
    return [a for a in assets if a['layer'] in ('buildings', 'infrastructure')
            and geographic.intersects(box(*a['bbox']))]


def ingest_regional(db, assets, geographic, projected, work):
    for index, asset in enumerate(assets):
        previous = db.execute('SELECT sha FROM sources WHERE id=?', (asset['id'],)).fetchone()
        if previous:
            require(previous[0] == asset['sha256'], 'Resumed source hash differs')
            continue
        path = Path(asset['path'])
        require(path.is_file() and path.stat().st_size < 256 * 1024**2, 'Source missing or oversized')
        reserve(work, max(64 * 1024**2, path.stat().st_size * 3))
        raw = path.read_bytes()
        require(sha(raw) == asset['sha256'], 'Source hash differs')
        document = json.loads(raw); del raw
        require(len(document['features']) == asset['count'], 'Source feature inventory differs')
        matched = inserted = 0
        with db:
            for feature, properties in source_features(document):
                require(feature.get('geometry'), 'Source geometry missing')
                original = shape(feature['geometry'])
                require(not original.is_empty and original.is_valid, 'Invalid source geometry; no silent repair')
                topic, _ = classify(asset, properties, original)
                if topic not in ('buildings', 'roads') or not geographic.intersects(original):
                    continue
                identity = feature.get('id') if feature.get('id') is not None else properties.get('source_record_id')
                require(identity is not None, 'Detail source has no stable identity')
                geometry = transform(PROJECT.transform, original)
                geometry_sha = sha(encoded(feature['geometry']))
                if topic == 'roads':
                    topic = 'detail-roads'
                    # Keep long cross-border roads inside the declared scope.
                    # Original source hash/ID remain in the selection record.
                    if not projected.covers(geometry):
                        geometry = geometry.intersection(projected)
                    require(not geometry.is_empty, 'CRS transform changed regional intersection')
                    properties = {**properties, 'display_scope_clip': 'SGIS census regions 2025-06-30'}
                    display_identity = f'{identity}\0geometry:{geometry_sha}'
                else:
                    properties = {**properties, **evaluate_feature(feature), 'original_properties': properties}
                    display_identity = None
                matched += 1
                inserted += add_record(db, topic, identity, asset['source_id'], asset['version'],
                    properties, geometry, asset['id'], geometry_sha, ZOOMS[topic][0], display_identity)
            db.execute('INSERT INTO sources VALUES(?,?,?)', (asset['id'], asset['sha256'], inserted))
            db.execute('INSERT INTO stages VALUES(?,?)', ('source-proof:' + asset['id'], json.dumps({
                'source_features': asset['count'], 'intersecting_features': matched,
                'new_unique_records': inserted, 'sha256': asset['sha256']})))
        del document
        print(json.dumps({'stage': 'regional-source', 'done': index + 1, 'total': len(assets),
            'matched': matched, 'inserted': inserted}), flush=True)


def reuse_all_topics(baseline, catalog, output, release):
    topics = deepcopy(catalog['topics']); reused = []
    old_prefix = '/data/map-tiles/' + catalog['release_id'] + '/'
    for topic in topics:
        require(topic['id'] not in ZOOMS, 'Baseline already has a regional detail topic')
        for ref in topic['chunks'] + topic['details']:
            original_url = ref['url']
            require(original_url.startswith(old_prefix), 'Unexpected baseline URL')
            ref['url'] = '/data/map-tiles/' + release + '/' + original_url[len(old_prefix):]
            source = local_path(baseline, original_url.lstrip('/'))
            target = local_path(output, ref['url'].lstrip('/'))
            require(digest(source) == ref['sha256'], 'Baseline file changed')
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                os.link(source, target)
            require(target.stat().st_size == ref['byte_length'] and digest(target) == ref['sha256'], 'Reused bytes changed')
            reused.append({'original_url': original_url, **ref})
    return topics, reused


def build(baseline, output, regions=DEFAULT_REGIONS, reuse_index=None):
    baseline = Path(baseline).resolve(); output = Path(output).resolve()
    require(output != baseline and not output.is_relative_to(baseline), 'Output must be separate from baseline')
    reserve(output)
    publication, catalog = load_baseline(baseline)
    geographic, projected, scope = load_scope(regions)
    source_catalog = json.loads(SOURCE_CATALOG.read_bytes())
    assets = select_assets(source_inputs(source_catalog), geographic)
    require(assets, 'No matching source assets')
    identity = {'version': VERSION, 'baseline_catalog_sha256': publication['map_catalog']['sha256'],
        'source_catalog_sha256': digest(SOURCE_CATALOG), 'scope': scope, 'zooms': ZOOMS,
        'source_assets': [{k: a[k] for k in ('id', 'sha256', 'count')} for a in assets],
        'transform_sha256': digest(Path(__file__)),
        'tile_transform_sha256': digest(Path(__file__).with_name('map_tiles.py')),
        'height_transform_sha256': digest(Path(__file__).with_name('height_quality.py'))}
    fingerprint = sha(encoded(identity)); release = 'map2d-' + fingerprint[:20]
    output.mkdir(parents=True, exist_ok=True); immutable(output / 'inputs.json', identity)
    work = output / 'work'; db = open_work(work / 'index.sqlite', fingerprint)
    if reuse_index and db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 0:
        reuse_v1_index(db, reuse_index, identity)
    ingest_regional(db, assets, geographic, projected, work)
    require(db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == len(assets), 'Source collection incomplete')
    topics, reused = reuse_all_topics(baseline, catalog, output, release)
    audits = {}; base = output / 'data/map-tiles' / release; prefix = '/data/map-tiles/' + release
    for topic, zooms in ZOOMS.items():
        count = db.execute('SELECT COUNT(*) FROM records WHERE topic=?', (topic,)).fetchone()[0]
        require(count > 0, 'Regional detail topic has no features')
        audits[topic] = build_topic_tiles(db, topic, work, zooms)
        a, b, c, d = db.execute('SELECT MIN(s.minx),MIN(s.miny),MAX(s.maxx),MAX(s.maxy) FROM records r JOIN spatial s ON r.n=s.n WHERE r.topic=?', (topic,)).fetchone()
        lo, la = TO_GEO.transform(a, b); hi, ha = TO_GEO.transform(c, d); bounds = [lo, la, hi, ha]
        chunks = pack_archives(db.execute('SELECT tileid,body FROM tiles WHERE topic=? ORDER BY tileid', (topic,)), topic, bounds, base / topic / 'tiles', prefix + '/' + topic + '/tiles')
        details = pack_details(db, topic, base / topic / 'details', prefix + '/' + topic + '/details')
        topics.append({'id': topic, 'source_layer': topic, 'minzoom': zooms[0], 'maxzoom': zooms[1],
            'feature_count': count, 'display_id_hex_length': 16, 'bounds': bounds, 'chunks': chunks, 'details': details,
            'description': ' · '.join(regions) + (' 원본 건물 윤곽' if topic == 'buildings' else ' OSM 상세 도로'),
            'geometry_precision': 'MVT 8192 display grid; original IDs and geometry hashes retained; overzoom magnifies display error'})
        print(json.dumps({'stage': 'regional-packed', 'topic': topic, 'features': count, 'chunks': len(chunks), 'details': len(details)}), flush=True)
    db.close()
    sources = {s['id']: s for s in catalog['sources']}
    sources.update({s['id']: s for s in DETAIL_SOURCES})
    for source in source_catalog['sources']:
        if source['id'] in {a['source_id'] for a in assets}:
            sources.setdefault(source['id'], source)
    result = {**catalog, 'release_id': release, 'topics': topics, 'sources': list(sources.values()),
        'attribution': catalog['attribution'] + ' · Overture Maps · European Union/JRC',
        'regional_detail': {**scope, 'source_assets_complete': True, 'topics': list(ZOOMS),
            'selection_rule': 'full buildings intersecting census scope; roads clipped to scope',
            'coverage_claim': 'all matching preserved source records, not all real-world buildings or roads'},
        'coverage': {**catalog.get('coverage', {}), 'regional_detail_names': list(regions)}}
    ref = immutable(base / 'catalog.json', result)
    files = [{'path': p.relative_to(output).as_posix(), 'sha256': digest(p), 'byte_length': p.stat().st_size}
             for p in sorted(base.rglob('*')) if p.is_file()]
    require(len(files) < 3870 and all(f['byte_length'] <= HARD for f in files), 'Map file count/size budget exceeded')
    report = {'schema_version': 1, 'status': 'validated', 'profile': 'national-basemap-regional-detail',
        'map_catalog': {'path': (base / 'catalog.json').relative_to(output).as_posix(), 'sha256': ref['sha256'], 'release_id': release},
        'files': files, 'file_count': len(files), 'bytes': sum(f['byte_length'] for f in files),
        'sources': identity, 'topics': {**publication.get('topics', {}), **audits}, 'reused_file_count': len(reused),
        'limits': {'archive_hard_bytes': HARD, 'low_zoom_target_bytes': LOW_TARGET, 'detail_target_bytes': DETAIL_TARGET},
        'limitations': ['Regional OSM/Overture source coverage is not a survey or proof of real-world completeness.',
            'Original properties/IDs/hash remain preserved; display precision and height estimates are not surveyed values.',
            'Generalized national roads remain underneath the regional detailed overlay.',
            'Offline validation is not HTTP, GPU performance, or public deployment validation.']}
    immutable(output / 'publication.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--regions', nargs='+', default=list(DEFAULT_REGIONS))
    parser.add_argument('--reuse-index', type=Path, help='Complete pinned v1 index; only attribution metadata is repaired')
    args = parser.parse_args()
    report = build(args.baseline, args.output, tuple(args.regions), args.reuse_index)
    print(json.dumps({k: report[k] for k in ('status', 'file_count', 'bytes', 'map_catalog')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
