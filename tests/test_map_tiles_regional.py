import gzip
import hashlib
import json
import zlib

import pytest
from shapely.geometry import Polygon, box, LineString, mapping
from shapely.ops import transform

from pipeline.admin_boundaries import encoded
from pipeline.map_tiles import PROJECT, TO_GEO, open_work, build_topic_tiles, sha
from pipeline.map_tiles_regional import ingest_regional, select_assets, reuse_all_topics, reuse_v1_index, V1_INGEST_TRANSFORM, V1_TILE_TRANSFORM, DETAIL_SOURCES


def source(tmp_path, identity, layer, features):
    path = tmp_path / (identity + '.json'); body = encoded({'features': features}); path.write_bytes(body)
    return {'id': identity, 'layer': layer, 'path': str(path), 'sha256': hashlib.sha256(body).hexdigest(),
            'count': len(features), 'source_id': 'overture' if layer == 'buildings' else 'osm',
            'version': '2026-08-19.0', 'bbox': [126.9, 37.4, 127.1, 37.7]}


def feature(identity, geom, properties=None):
    return {'type': 'Feature', 'id': identity, 'geometry': mapping(geom), 'properties': properties or {}}


def test_scope_preserves_islands_holes_and_source_identity(tmp_path):
    # Bbox alone would include the hole and the gap between two islands.
    mask = Polygon(box(127, 37.5, 127.01, 37.51).exterior.coords,
                   [box(127.004, 37.504, 127.006, 37.506).exterior.coords])
    mask = mask.union(box(127.02, 37.5, 127.021, 37.501))
    features = [feature('in', box(127.001, 37.501, 127.002, 37.502)),
                feature('hole', box(127.0045, 37.5045, 127.005, 37.505)),
                feature('gap', box(127.015, 37.501, 127.016, 37.502)),
                feature('island', box(127.0201, 37.5001, 127.0202, 37.5002)),
                feature('edge', box(126.9999, 37.5001, 127.0001, 37.5002))]
    asset = source(tmp_path, 'buildings', 'buildings', features)
    db = open_work(tmp_path / 'work/index.sqlite', 'scope')
    ingest_regional(db, [asset], mask, transform(PROJECT.transform, mask), tmp_path)
    rows = [json.loads(zlib.decompress(r[0])) for r in db.execute('SELECT record FROM records')]
    assert {r['source_record_id'] for r in rows} == {'in', 'island', 'edge'}
    for r in rows:
        original = next(f for f in features if f['id'] == r['source_record_id'])
        assert r['geometry_sha256'] == hashlib.sha256(encoded(original['geometry'])).hexdigest()
        assert r['properties']['original_properties'] == original['properties']
    before = db.execute('SELECT stable,geom,record FROM records ORDER BY stable').fetchall()
    ingest_regional(db, [asset], mask, transform(PROJECT.transform, mask), tmp_path)
    assert db.execute('SELECT stable,geom,record FROM records ORDER BY stable').fetchall() == before
    assert db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 1
    audit = build_topic_tiles(db, 'buildings', tmp_path, (14, 14))
    assert audit['14']['represented_feature_count'] == 3
    assert audit['14']['every_source_id_represented']
    db.close()


def test_cross_border_road_is_clipped_but_original_hash_and_id_retained(tmp_path):
    import shapely
    mask = box(127, 37.5, 127.01, 37.51)
    f = feature('way/1', LineString([(126.99, 37.505), (127.02, 37.505)]), {'highway': 'residential', 'name': '테스트길'})
    asset = source(tmp_path, 'road-test', 'infrastructure', [f])
    db = open_work(tmp_path / 'work/index.sqlite', 'road')
    ingest_regional(db, [asset], mask, transform(PROJECT.transform, mask), tmp_path)
    topic, geometry, record = db.execute('SELECT topic,geom,record FROM records').fetchone()
    assert topic == 'detail-roads'
    clipped = transform(TO_GEO.transform, shapely.from_wkb(geometry))
    assert clipped.bounds == pytest.approx((127, 37.505, 127.01, 37.505))
    r = json.loads(zlib.decompress(record))
    assert r['source_record_id'] == 'way/1' and r['properties']['name'] == '테스트길'
    assert r['geometry_sha256'] == hashlib.sha256(encoded(f['geometry'])).hexdigest()
    audit = build_topic_tiles(db, 'detail-roads', tmp_path, (14, 14))
    assert audit['14']['every_source_id_represented']
    db.close()


def test_source_hash_failure_never_commits_a_completed_asset(tmp_path):
    mask = box(127, 37.5, 127.01, 37.51)
    asset = source(tmp_path, 'building', 'buildings', [feature('one', mask)])
    asset['sha256'] = '0' * 64
    db = open_work(tmp_path / 'work/index.sqlite', 'bad')
    with pytest.raises(ValueError, match='hash differs'):
        ingest_regional(db, [asset], mask, transform(PROJECT.transform, mask), tmp_path)
    assert db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 0
    db.close()


def test_conflicting_building_identity_rolls_back_asset_and_can_resume(tmp_path):
    mask = box(127, 37.5, 127.01, 37.51)
    asset = source(tmp_path, 'bad', 'buildings', [feature('same', mask), feature('same', mask.buffer(-.001))])
    db = open_work(tmp_path / 'work/index.sqlite', 'duplicates')
    with pytest.raises(ValueError, match='Conflicting source ID'):
        ingest_regional(db, [asset], mask, transform(PROJECT.transform, mask), tmp_path)
    assert db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 0
    assert db.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 0
    db.close()


def test_candidate_prefilter_is_not_bbox_only(tmp_path):
    mask = box(127, 37.5, 127.01, 37.51)
    a = source(tmp_path, 'a', 'buildings', [])
    assert select_assets([a, {**a, 'id': 'outside', 'bbox': [128, 36, 129, 37]},
                          {**a, 'id': 'rail', 'layer': 'rail'}], mask) == [a]


def test_baseline_reuse_keeps_every_topic_and_immutable_bytes(tmp_path):
    baseline = tmp_path / 'base'; output = tmp_path / 'new'; body = gzip.compress(b'preserved')
    original = baseline / 'data/map-tiles/map2d-old/land/a.json.gz'
    original.parent.mkdir(parents=True); original.write_bytes(body)
    ref = {'url': '/data/map-tiles/map2d-old/land/a.json.gz', 'sha256': hashlib.sha256(body).hexdigest(), 'byte_length': len(body)}
    catalog = {'release_id': 'map2d-old', 'topics': [{'id': 'land', 'chunks': [], 'details': [ref]}]}
    topics, reused = reuse_all_topics(baseline, catalog, output, 'map2d-new')
    assert topics[0]['id'] == 'land' and len(reused) == 1
    assert (output / topics[0]['details'][0]['url'].lstrip('/')).read_bytes() == body
    assert catalog['topics'][0]['details'][0]['url'] == ref['url']
    with pytest.raises(ValueError, match='already has'):
        reuse_all_topics(baseline, {'topics': [{'id': 'buildings'}], 'release_id': 'map2d-old'}, output, 'map2d-new')


def test_only_pinned_complete_equivalent_donor_can_reuse_records(tmp_path):
    mask = box(127, 37.5, 127.01, 37.51)
    asset = source(tmp_path, 'a', 'buildings', [feature('one', mask)])
    donor = tmp_path / 'donor'; donor.mkdir()
    old = {'version': 'regional-detail-1', 'transform_sha256': V1_INGEST_TRANSFORM,
           'source_assets': [{k: asset[k] for k in ('id', 'sha256', 'count')}], 'scope': {'regions': ['서울특별시']}}
    (donor / 'inputs.json').write_bytes(encoded(old))
    source_db = open_work(donor / 'work/index.sqlite', sha(encoded(old)))
    ingest_regional(source_db, [asset], mask, transform(PROJECT.transform, mask), tmp_path)
    before = source_db.execute('SELECT * FROM records').fetchall(); source_db.close()
    current = {**old, 'version': 'regional-detail-2', 'transform_sha256': 'new'}
    target = open_work(tmp_path / 'target/index.sqlite', 'new')
    reuse_v1_index(target, donor, current)
    assert target.execute('SELECT * FROM records').fetchall() == before
    target.close()
    bad = open_work(tmp_path / 'bad/index.sqlite', 'bad')
    with pytest.raises(ValueError, match='input differs: scope'):
        reuse_v1_index(bad, donor, {**current, 'scope': {'regions': ['다른 지역']}})
    old['transform_sha256'] = 'f' * 64; (donor / 'inputs.json').write_bytes(encoded(old))
    with pytest.raises(ValueError, match='Unapproved'):
        reuse_v1_index(bad, donor, current)
    assert bad.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 0
    bad.close()


def test_building_and_height_attribution_is_available_when_input_catalog_has_no_sources():
    assert {s['id'] for s in DETAIL_SOURCES} == {'overture', 'ghsl'}
    assert all(s['url'].startswith('https://') and s['license'] and s['description'] for s in DETAIL_SOURCES)


def test_known_tile_transform_change_reuses_records_but_not_old_tiles_or_zoom_completion(tmp_path):
    donor=tmp_path/'donor';donor.mkdir()
    old={'version':'regional-detail-1','transform_sha256':V1_INGEST_TRANSFORM,
         'tile_transform_sha256':V1_TILE_TRANSFORM,'source_assets':[]}
    (donor/'inputs.json').write_bytes(encoded(old))
    source_db=open_work(donor/'work/index.sqlite',sha(encoded(old)))
    source_db.execute('INSERT INTO tiles VALUES(?,?,?)',('buildings',1,b'old'))
    source_db.execute('INSERT INTO stages VALUES(?,?)',('buildings-z14','{}'))
    source_db.execute('INSERT INTO stages VALUES(?,?)',('source-proof:test','{}'))
    source_db.commit();source_db.close()
    target=open_work(tmp_path/'target/index.sqlite','new')
    reuse_v1_index(target,donor,{**old,'tile_transform_sha256':'changed'})
    assert target.execute('SELECT COUNT(*) FROM tiles').fetchone()[0]==0
    assert target.execute("SELECT 1 FROM stages WHERE key='buildings-z14'").fetchone() is None
    assert target.execute("SELECT 1 FROM stages WHERE key='source-proof:test'").fetchone()
    target.close()


def test_broader_display_preserves_donor_and_every_source_identity(tmp_path):
    from pathlib import Path
    import sqlite3
    from pipeline.core import digest
    from pipeline.map_tiles import pack_archives
    from pipeline.map_tiles_regional import extend_display_zooms, V2_INGEST_TRANSFORM
    base = tmp_path / 'base'; base.mkdir()
    mask = box(127, 37.5, 127.01, 37.51)
    assets = [source(tmp_path, 'building', 'buildings', [feature('tiny', box(127.001, 37.501, 127.00101, 37.50101)), feature('large', mask)]),
              source(tmp_path, 'road', 'infrastructure', [feature('way/1', LineString([(127, 37.505), (127.01, 37.505)]), {'highway': 'residential'})])]
    old = {'version': 'regional-detail-2', 'transform_sha256': V2_INGEST_TRANSFORM,
           'tile_transform_sha256': digest(Path('pipeline/map_tiles.py')),
           'zooms': {'buildings': [14, 14], 'detail-roads': [14, 14]}}
    (base / 'inputs.json').write_bytes(encoded(old))
    db = open_work(base / 'work/index.sqlite', sha(encoded(old)))
    ingest_regional(db, assets, mask, transform(PROJECT.transform, mask), tmp_path)
    before = db.execute('SELECT * FROM records ORDER BY n').fetchall()
    topics = []; audits = {}; prefix = '/data/map-tiles/map2d-test'
    for name in ('buildings', 'detail-roads'):
        audits[name] = build_topic_tiles(db, name, tmp_path, (14, 14))
        chunks = pack_archives(db.execute('SELECT tileid,body FROM tiles WHERE topic=? ORDER BY tileid', (name,)),
                              name, list(mask.bounds), base / prefix.lstrip('/') / name, prefix + '/' + name)
        topics.append({'id': name, 'minzoom': 14, 'maxzoom': 14, 'chunks': chunks, 'details': [],
                       'bounds': list(mask.bounds), 'feature_count': 2 if name == 'buildings' else 1})
    db.close()
    catalog = base / prefix.lstrip('/') / 'catalog.json'
    catalog.write_bytes(encoded({'release_id': 'map2d-test', 'topics': topics}))
    files = [{'path': p.relative_to(base).as_posix(), 'sha256': digest(p), 'byte_length': p.stat().st_size}
             for p in sorted((base / 'data').rglob('*')) if p.is_file()]
    (base / 'publication.json').write_bytes(encoded({'status': 'validated', 'map_catalog': {
        'path': catalog.relative_to(base).as_posix(), 'sha256': digest(catalog), 'release_id': 'map2d-test'},
        'files': files, 'file_count': len(files), 'topics': audits}))
    result = extend_display_zooms(base, tmp_path / 'new')
    overviews = [name for name in result['topics'] if name.startswith('buildings-overview-')]
    for zoom in ('12', '13'):
        assert sum(result['topics'][name][zoom]['represented_feature_count'] for name in overviews) == 2
        assert all(result['topics'][name][zoom]['every_source_id_represented'] for name in overviews)
    for zoom in ('13',):
        assert result['topics']['detail-roads'][zoom]['every_source_id_represented']
    after = sqlite3.connect(base / 'work/index.sqlite')
    assert after.execute('SELECT * FROM records ORDER BY n').fetchall() == before
    assert after.execute('SELECT COUNT(*) FROM stages').fetchone()[0] == 4
    after.close()
    updated = json.loads((tmp_path / 'new' / result['map_catalog']['path']).read_bytes())
    for topic, original in zip(updated['topics'], topics):
        old_hashes = {r['sha256'] for r in original['chunks']}
        assert old_hashes.issubset({r['sha256'] for r in topic['chunks']})
    # Resuming only consumes committed new zooms and leaves the donor unchanged.
    assert extend_display_zooms(base, tmp_path / 'new')['map_catalog'] == result['map_catalog']
