import gzip
import json

import mapbox_vector_tile
import pytest
import shapely
from pmtiles.reader import MemorySource, Reader
from pmtiles.tile import zxy_to_tileid
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box, shape

from pipeline.admin_boundaries import encoded, immutable
from pipeline.core import digest
from pipeline.map_tiles import EXTENT, HARD, archive_bytes, sha, tile_bounds
from pipeline.map_tiles_land import load_baseline
from pipeline.map_tiles_overview import (DONOR, build, build_topic, donor_features,
    encode_overview, quantized_overview)


def projected(geometry, tile=DONOR):
    bounds = tile_bounds(*tile); unit = (bounds[2] - bounds[0]) / EXTENT
    return shapely.transform(geometry, lambda c: c * unit + bounds[:2])


def make_baseline(tmp_path, seam=False, duplicate_label=False):
    root = tmp_path / 'baseline'; release = 'map2d-' + 'a' * 20
    topics = []; files = []
    props = {'stable_id': '1' * 16, 'name': '원본 명칭', 'source_id': 'sgis', 'representation': 'quantized'}
    polygon = box(10 if not seam else 0, 10, 8000, 8000)
    data = {
        'land': [(polygon, props), (Point(5900, 6100), {**props, 'stable_id': '2' * 16, 'source_id': 'osm', 'name': '원본 독도'})],
        'admin-sido': [(LineString([(100, 100), (200, 200)]), props)] * 2 + [(Point(300, 300), props)],
    }
    if duplicate_label:
        data['admin-sido'].append((Point(400, 400), props))
    for name, features in data.items():
        raw = mapbox_vector_tile.encode({'name': name, 'features': [
            {'geometry': g, 'properties': p} for g, p in features]}, default_options={'extents': EXTENT})
        tid = zxy_to_tileid(*DONOR)
        body = archive_bytes([(tid, gzip.compress(raw, mtime=0))], name, [124, 33, 132, 39])
        path = f'data/map-tiles/{release}/{name}/tiles/original.pmtiles'; immutable(root / path, body)
        ref = {'url': '/' + path, 'sha256': sha(body), 'byte_length': len(body),
            'first_tile_id': tid, 'last_tile_id': tid, 'tile_count': 1}
        ids = sorted({p['stable_id'] for _, p in features})
        records = [{'stable_id': sid * 4, 'source_record_id': sid, 'source_id': 'sgis', 'properties': {'original': True}} for sid in ids]
        detail = gzip.compress(encoded({'records': records}), mtime=0)
        dp = f'data/map-tiles/{release}/{name}/details/original.json.gz'; immutable(root / dp, detail)
        dr = {'url': '/' + dp, 'sha256': sha(detail), 'byte_length': len(detail),
            'first_id': ids[0] * 4, 'last_id': ids[-1] * 4, 'record_count': len(ids)}
        topics.append({'id': name, 'source_layer': name, 'minzoom': 5, 'maxzoom': 9,
            'feature_count': len(ids), 'display_id_hex_length': 16, 'bounds': [124, 33, 132, 39],
            'chunks': [ref], 'details': [dr], 'description': 'preserved', 'geometry_precision': 'z5 original display'})
        files.extend([{'path': path, 'sha256': ref['sha256'], 'byte_length': len(body)},
            {'path': dp, 'sha256': dr['sha256'], 'byte_length': len(detail)}])
    catalog = {'schema_version': 1, 'release_id': release, 'bounds': [124, 33, 132, 39], 'topics': topics,
        'sources': [], 'reference_dates': {'sgis': '2025-06-30'}, 'source_release_id': 'fixture', 'attribution': 'fixture'}
    cp = f'data/map-tiles/{release}/catalog.json'; cr = immutable(root / cp, catalog)
    files.append({'path': cp, 'sha256': cr['sha256'], 'byte_length': (root / cp).stat().st_size})
    publication = {'schema_version': 1, 'status': 'validated', 'map_catalog': {
        'path': cp, 'sha256': cr['sha256'], 'release_id': release}, 'files': files, 'file_count': len(files),
        'bytes': sum(r['byte_length'] for r in files), 'topics': {}, 'limitations': ['fixture']}
    immutable(root / 'publication.json', publication)
    return root, catalog


def test_donor_groups_lines_without_duplicating_original_label_or_source_ids(tmp_path):
    root, catalog = make_baseline(tmp_path)
    features, audit = donor_features(root, catalog['topics'][1])
    assert len(features) == 2
    assert {g.geom_type for g, _ in features} == {'Point', 'MultiLineString'}
    assert audit['label_points'] == 1 and audit['exact_duplicate_fragments_removed'] == 1
    assert audit['source_clip_seams'] == 0 and audit['all_geometry_strictly_inside_donor']
    tiles, reports = build_topic('admin-sido', features)
    assert len(tiles) == 2
    assert all(r['tiles'][0]['label_count'] == 1 for r in reports.values())
    assert all(r['all_source_ids_represented'] for r in reports.values())


def test_donor_refuses_unverified_source_tile_seams(tmp_path):
    root, catalog = make_baseline(tmp_path, seam=True)
    with pytest.raises(ValueError, match='seam or buffer'):
        donor_features(root, catalog['topics'][0])


def test_donor_refuses_different_label_points_for_same_original_id(tmp_path):
    root, catalog = make_baseline(tmp_path, duplicate_label=True)
    with pytest.raises(ValueError, match='Duplicate or missing administrative label'):
        donor_features(root, catalog['topics'][1])


def test_tiny_island_keeps_original_id_and_name_as_explicit_anchor():
    z, x, y = 3, 6, 3; b = tile_bounds(z, x, y); unit = (b[2] - b[0]) / EXTENT
    g = box(b[0] + unit * 100.1, b[1] + unit * 100.1, b[0] + unit * 100.2, b[1] + unit * 100.2)
    props = {'stable_id': 'a' * 16, 'name': '작은 섬', 'source_id': 'osm', 'quality': 'source'}
    body, report, ids = encode_overview('land', [(g, props)], z, x, y)
    feature = mapbox_vector_tile.decode(gzip.decompress(body))['land']['features'][0]
    assert ids == {'a' * 16} and feature['geometry']['type'] == 'Point'
    assert all(feature['properties'][k] == v for k, v in props.items())
    assert feature['properties']['representation'] == 'subpixel_anchor'
    assert report['all_geometry_roundtrip_valid']


def test_collapsed_admin_line_never_becomes_a_duplicate_label():
    bounds = (0, 0, EXTENT, EXTENT)
    line = LineString([(10.1, 10.1), (10.2, 10.2)])
    display, notice = quantized_overview(line, bounds)
    assert display.is_empty and notice == 'subpixel_line_collapsed'


def test_adjacent_land_fill_retains_shared_edge_without_gap_or_overlap():
    first = Polygon([(100, 100), (300, 100), (300, 173.49), (200.51, 220.49), (100, 170.51)])
    second = box(100, 100, 300, 300).difference(first)
    a, _ = quantized_overview(first, (0, 0, EXTENT, EXTENT))
    b, _ = quantized_overview(second, (0, 0, EXTENT, EXTENT))
    assert a.intersection(b).area == 0
    assert a.union(b).equals(box(100, 100, 300, 300))


def test_encode_keeps_valid_polygon_hole_and_unchanged_properties():
    g = Polygon([(100, 100), (500, 100), (500, 500), (100, 500)], [[(200, 200), (200, 400), (400, 400), (400, 200)]])
    props = {'stable_id': 'a' * 16, 'source_id': 'sgis', 'name': '정확한 원본 명칭'}
    body, _, _ = encode_overview('land', [(projected(g, (3, 6, 3)), props)], 3, 6, 3)
    feature = mapbox_vector_tile.decode(gzip.decompress(body))['land']['features'][0]
    assert shape(feature['geometry']).equals(g)
    assert len(shape(feature['geometry']).interiors) == 1
    assert feature['properties']['name'] == props['name']


def test_decoded_single_tile_limit_is_never_raised():
    props = {'stable_id': 'a' * 16, 'name': 'x' * HARD}
    with pytest.raises(ValueError, match='1 MiB'):
        encode_overview('land', [(projected(box(10, 10, 100, 100), (3, 6, 3)), props)], 3, 6, 3)


def test_new_candidate_reuses_every_original_archive_and_selection_byte(tmp_path):
    root, old_catalog = make_baseline(tmp_path)
    original = {p.relative_to(root): digest(p) for p in root.rglob('*') if p.is_file()}
    out = tmp_path / 'overview'; result = build(root, out)
    _, catalog = load_baseline(out)
    assert result['status'] == 'validated' and result['reused_file_count'] == 4
    assert catalog['release_id'] != old_catalog['release_id']
    for before, after in zip(old_catalog['topics'], catalog['topics']):
        assert after['minzoom'] == 3 and after['maxzoom'] == before['maxzoom']
        assert after['feature_count'] == before['feature_count']
        assert after['chunks'][0]['last_tile_id'] < before['chunks'][0]['first_tile_id']
        for a, b in zip(before['chunks'] + before['details'], after['chunks'][1:] + after['details']):
            assert a['sha256'] == b['sha256'] and a['byte_length'] == b['byte_length']
            assert (root / a['url'].lstrip('/')).read_bytes() == (out / b['url'].lstrip('/')).read_bytes()
        archive = Reader(MemorySource((out / after['chunks'][0]['url'].lstrip('/')).read_bytes()))
        assert archive.get(3, 6, 3) is not None and archive.get(4, 13, 6) is not None
        assert archive.get(2, 3, 1) is None
    assert original == {p.relative_to(root): digest(p) for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='new directory'):
        build(root, out)


def test_corrupt_donor_archive_rejected_before_candidate_written(tmp_path):
    root, catalog = make_baseline(tmp_path)
    (root / catalog['topics'][0]['chunks'][0]['url'].lstrip('/')).write_bytes(b'invalid')
    out = tmp_path / 'overview'
    with pytest.raises(ValueError, match='hash differs'):
        build(root, out)
    assert not out.exists()
