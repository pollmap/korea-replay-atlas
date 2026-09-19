import gzip
import hashlib
import json

import mapbox_vector_tile
import pytest
import shapely
from shapely.geometry import MultiPolygon, Polygon, box, shape

from pipeline.admin_boundaries import encoded
from pipeline.map_tiles import EXTENT, HARD, stable_id, tile_bounds
from pipeline.map_tiles_land import (LandIndex, LandSource, encode_land_tile,
    load_baseline, local_path, quantized_fill, reuse_topics, source_registry)


def source(geometry, identity='sgis:20250630:sido:21'):
    return LandSource(stable_id('sgis', identity), geometry,
        {'source_record_id': identity, 'source_id': 'sgis', 'properties': {'name': '부산광역시'}},
        {'name': '부산광역시', 'source_id': 'sgis', 'admin_code': '21'})


def test_narrow_notch_keeps_polygon_fill_instead_of_downgrading_entire_land_to_outline():
    bounds = (0, 0, EXTENT, EXTENT)
    geometry = Polygon([(10, 10), (20, 10), (20, 20), (16.49, 20),
        (16.49, 10.49), (15.51, 10.49), (15.51, 20), (10, 20), (10, 10)])
    before = geometry.wkb
    display, representation, audit = quantized_fill(geometry, bounds)
    assert display.is_valid and display.geom_type in ('Polygon', 'MultiPolygon')
    assert display.area > 95 and representation == 'quantized_valid_fill'
    assert audit['source_polygon_components'] == 1
    assert geometry.wkb == before


def test_subgrid_whole_island_remains_an_explicit_anchor_with_original_source_unchanged():
    original = box(100.1, 100.1, 100.2, 100.2); before = original.wkb
    display, representation, audit = quantized_fill(original, (0, 0, EXTENT, EXTENT))
    assert display.geom_type == 'Point' and representation == 'subpixel_anchor'
    assert audit['source_polygon_components'] == 1 and audit['display_polygon_components'] == 0
    assert original.wkb == before


def test_encoded_land_keeps_holes_fill_and_source_id_on_both_sides_of_a_tile_boundary():
    z, x, y = 12, 3516, 1620
    b = tile_bounds(z, x, y); unit = (b[2] - b[0]) / EXTENT
    original = box(b[2] - 100 * unit, b[1] + 100 * unit, b[2] + 100 * unit, b[1] + 400 * unit)
    original = original.difference(box(b[2] - 70 * unit, b[1] + 150 * unit, b[2] - 30 * unit, b[1] + 200 * unit))
    s = source(original); before = original.wkb; index = LandIndex([s])
    holes = 0
    for column in (x, x + 1):
        rows = index.query(tile_bounds(z, column, y)); body, _, ids = encode_land_tile(rows, z, column, y)
        assert ids == {s.stable}
        decoded = mapbox_vector_tile.decode(gzip.decompress(body))['land']['features']
        assert len(decoded) == 1 and decoded[0]['properties']['stable_id'] == s.stable[:16]
        polygon = shape(decoded[0]['geometry'])
        assert polygon.is_valid and polygon.area > 0
        holes += len(polygon.interiors)
    assert holes == 1 and original.wkb == before


def test_multiple_islands_share_the_original_administrative_identity_without_being_dropped():
    b = tile_bounds(12, 3516, 1620)
    g = MultiPolygon([box(b[0] + 10, b[1] + 10, b[0] + 100, b[1] + 100),
        box(b[0] + 200, b[1] + 200, b[0] + 300, b[1] + 300)])
    s = source(g); rows = LandIndex([s]).query(b)
    assert len(rows) == 1 and rows[0][1].equals(g)
    body, _, ids = encode_land_tile(rows, 12, 3516, 1620)
    decoded = mapbox_vector_tile.decode(gzip.decompress(body))['land']['features']
    assert ids == {s.stable} and len(decoded) == 1
    assert len(shape(decoded[0]['geometry']).geoms) == 2


def test_shared_administrative_edges_keep_continuous_fill_after_independent_quantization():
    extent = (0, 0, EXTENT, EXTENT)
    first = Polygon([(100, 100), (300, 100), (300, 173.49), (200.51, 220.49), (100, 170.51)])
    second = box(100, 100, 300, 300).difference(first)
    a, _, _ = quantized_fill(first, extent); b, _, _ = quantized_fill(second, extent)
    assert a.intersection(b).area == 0
    assert shapely.union_all([a, b]).equals(box(100, 100, 300, 300))


def baseline(tmp_path):
    root = tmp_path / 'baseline'; release = 'map2d-' + 'a' * 20
    path = f'data/map-tiles/{release}/roads/tiles/a.pmtiles'
    target = root / path; target.parent.mkdir(parents=True); target.write_bytes(b'unchanged binary tile')
    ref = {'url': '/' + path, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'byte_length': target.stat().st_size, 'first_tile_id': 1, 'last_tile_id': 1, 'tile_count': 1}
    catalog = {'release_id': release, 'topics': [{'id': 'roads', 'chunks': [ref], 'details': [], 'feature_count': 1}]}
    cp = f'data/map-tiles/{release}/catalog.json'; (root / cp).write_bytes(encoded(catalog))
    entry = {'path': cp, 'sha256': hashlib.sha256((root / cp).read_bytes()).hexdigest(), 'release_id': release}
    files = [{'path': path, 'sha256': ref['sha256'], 'byte_length': ref['byte_length']},
        {'path': cp, 'sha256': entry['sha256'], 'byte_length': (root / cp).stat().st_size}]
    (root / 'publication.json').write_bytes(encoded({'status': 'validated', 'map_catalog': entry, 'files': files, 'file_count': 2}))
    return root, catalog


def test_reused_theme_changes_only_immutable_release_paths_and_preserves_every_byte(tmp_path):
    root, catalog = baseline(tmp_path); before = encoded(catalog); output = tmp_path / 'next'
    load_baseline(root)
    topics, reused = reuse_topics(root, catalog, output, 'map2d-' + 'b' * 20)
    assert encoded(catalog) == before and len(reused) == 1
    new = output / topics[0]['chunks'][0]['url'].lstrip('/')
    assert new.read_bytes() == b'unchanged binary tile'
    assert reused[0]['sha256'] == catalog['topics'][0]['chunks'][0]['sha256']
    assert topics[0]['feature_count'] == catalog['topics'][0]['feature_count']


def test_baseline_hash_corruption_and_reference_escape_are_rejected(tmp_path):
    root, catalog = baseline(tmp_path)
    (root / catalog['topics'][0]['chunks'][0]['url'].lstrip('/')).write_bytes(b'different')
    with pytest.raises(ValueError, match='hash differs'):
        load_baseline(root)
    for relative in ('../escape', '/absolute', 'data/../../escape', 'C:/outside', 'data\\escape'):
        with pytest.raises(ValueError):
            local_path(root, relative)


def test_source_registry_excludes_replaced_natural_earth_and_unused_overture():
    registry = source_registry({'sgis', 'osm'})
    assert {s['id'] for s in registry} == {'sgis', 'osm'}
    assert '공식 해안측량' in next(s for s in registry if s['id'] == 'sgis')['description']
    with pytest.raises(ValueError, match='attribution'):
        source_registry({'unknown'})


def test_single_decoded_tile_hard_limit_is_not_raised_for_large_properties():
    z, x, y = 12, 3516, 1620; b = tile_bounds(z, x, y)
    s = source(box(b[0] + 100, b[1] + 100, b[0] + 200, b[1] + 200))
    s.render['name'] = 'x' * HARD
    with pytest.raises(ValueError, match='1 MiB'):
        encode_land_tile([(s, s.geometry)], z, x, y)
