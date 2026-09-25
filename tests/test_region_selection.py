import pytest
from shapely.geometry import MultiPolygon, Polygon

from pipeline.region_selection import compact_geometry, decode_ring, encode_ring, topology


def test_signed_polyline_round_trip_and_longitude_precision():
    points = [[127.1234567, 37.8765432], [127.13, 37.86], [126.9, 37.99], [127.1234567, 37.8765432]]
    assert decode_ring(encode_ring(points)) == points
    assert decode_ring(encode_ring(points, 9), 9) == points
    with pytest.raises(ValueError):
        decode_ring('_')


def test_simplification_preserves_disconnected_island_and_hole():
    mainland = Polygon([(950000, 1950000), (950030, 1950001), (950060, 1950000),
                        (950100, 1950000), (950100, 1950100), (950000, 1950100)],
                       [[(950030, 1950030), (950070, 1950030), (950070, 1950070), (950030, 1950070)]])
    tiny_island = Polygon([(950200, 1950000), (950200.2, 1950000), (950200.2, 1950000.2), (950200, 1950000.2)])
    original = MultiPolygon([mainland, tiny_island])
    encoded, precision, bound, count = compact_geometry(original, 20)
    decoded_parts = [Polygon(decode_ring(p[0], precision), [decode_ring(r, precision) for r in p[1:]]) for p in encoded]
    decoded = MultiPolygon(decoded_parts)
    assert decoded.is_valid and topology(decoded) == [1, 0]
    assert len(encoded) == 2 and len(encoded[0]) == 2
    assert bound <= 20.02 and count > 0
    assert all(decode_ring(r, precision)[0] == decode_ring(r, precision)[-1] for p in encoded for r in p)


def test_reject_invalid_polygon_instead_of_repairing_source():
    with pytest.raises(ValueError, match='Valid polygon'):
        compact_geometry(Polygon([(0, 0), (10, 10), (0, 10), (10, 0)]))
