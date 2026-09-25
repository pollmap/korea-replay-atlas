"""Compact, source-backed polygons for one selected administrative area.

This display overlay is not a legal-dong crosswalk or aggregation geography.
Original SGIS data is retained; every island and hole must survive simplification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import transform

from pipeline.admin_boundaries import (
    REFERENCE_DATE, SOURCE_URL, read_source_level, ring_displacement_bound, vertex_count,
)

ARCHIVE_SHA = 'f1cf0f9de453ac7eaacb273f39cee52851183372b9ddfda428a967c3a670b2c6'


def parts(geometry):
    return [geometry] if geometry.geom_type == 'Polygon' else list(geometry.geoms)


def topology(geometry):
    return [len(p.interiors) for p in parts(geometry)]


def encode_ring(coordinates, precision=7):
    """Google-style signed delta polyline, longitude first; closed rings retained."""
    scale = 10 ** precision
    previous = [0, 0]
    output = []
    for point in coordinates:
        for axis in range(2):
            value = round(point[axis] * scale)
            delta = value - previous[axis]
            previous[axis] = value
            number = delta * 2 if delta >= 0 else -delta * 2 - 1
            while number >= 32:
                output.append(chr((number & 31) + 95))
                number >>= 5
            output.append(chr(number + 63))
    return ''.join(output)


def decode_ring(encoded, precision=7):
    previous = [0, 0]
    output = []
    number = shift = axis = 0
    for char in encoded:
        value = ord(char) - 63
        if not 0 <= value <= 63:
            raise ValueError('Invalid coordinate encoding')
        number |= (value & 31) << shift
        if value & 32:
            shift += 5
            if shift > 55:
                raise ValueError('Coordinate overflow')
            continue
        previous[axis] += -(number // 2) - 1 if number & 1 else number // 2
        if axis == 1:
            output.append([previous[0] / 10 ** precision, previous[1] / 10 ** precision])
        axis = 1 - axis
        number = shift = 0
    if axis or shift:
        raise ValueError('Incomplete coordinate encoding')
    return output


def compact_geometry(original, tolerance=20):
    if original.geom_type not in ('Polygon', 'MultiPolygon') or not original.is_valid:
        raise ValueError('Valid polygon required')
    simplified, bound = original, 0.0
    for attempt in (tolerance, tolerance / 2, tolerance / 4, 0):
        candidate = original.simplify(attempt, preserve_topology=True) if attempt else original
        if topology(candidate) != topology(original):
            continue
        try:
            measured = ring_displacement_bound(original, candidate) if attempt else 0.0
        except ValueError:
            continue
        if measured <= tolerance + 1e-6:
            simplified, bound = candidate, measured
            break
    project = Transformer.from_crs(5179, 4326, always_xy=True).transform
    inverse = Transformer.from_crs(4326, 5179, always_xy=True).transform
    geographic = transform(project, simplified)
    for precision in (7, 8, 9):
        encoded = [[encode_ring(r.coords, precision) for r in [p.exterior, *p.interiors]] for p in parts(geographic)]
        polygons = [Polygon(decode_ring(p[0], precision), [decode_ring(r, precision) for r in p[1:]]) for p in encoded]
        decoded = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
        if decoded.is_valid and topology(decoded) == topology(original):
            native = transform(inverse, decoded)
            # Same rings, order and vertex count: paired vertex displacement
            # bounds every interpolated segment without a quadratic curve scan.
            quantization = float(np.linalg.norm(shapely.get_coordinates(simplified) - shapely.get_coordinates(native), axis=1).max())
            if quantization <= .02:
                return encoded, precision, bound + quantization, vertex_count(simplified)
    raise ValueError('Quantization invalidated source topology')


def build(archive, output, report_path, tolerance=20, dongs=False):
    archive = Path(archive)
    with archive.open('rb') as handle:
        if hashlib.file_digest(handle, 'sha256').hexdigest() != ARCHIVE_SHA:
            raise ValueError('SGIS archive SHA differs from audited source')
    names = {}
    rows_out = []
    maximum = 0
    original_vertices = simplified_vertices = islands = holes = 0
    cache_root = Path(report_path).parent / 'feature-cache'
    cache_root.mkdir(parents=True, exist_ok=True)
    levels = [('eupmyeondong', 'ADM')] if dongs else [('sido', 'SIDO'), ('sigungu', 'SIGUNGU')]
    for level, prefix in levels:
        rows, geometries, _ = read_source_level(archive, level)
        for row, geometry in zip(rows, geometries):
            if row['BASE_DATE'] != '20250630':
                raise ValueError('Boundary date mismatch')
            code, name = row[prefix + '_CD'], row[prefix + '_NM']
            if level == 'sido':
                names[code] = name
            elif level == 'sigungu':
                name = names[code[:2]] + ' ' + name
            cache = cache_root / f'{level}-{code}-{tolerance:g}.json'
            if cache.exists():
                cached = json.loads(cache.read_bytes())
                if cached['source_sha'] != hashlib.sha256(geometry.wkb).hexdigest():
                    raise ValueError('Geometry cache source differs')
                encoded, precision, bound, vertices = cached['values']
            else:
                encoded, precision, bound, vertices = compact_geometry(geometry, tolerance)
                cache.write_text(json.dumps({'source_sha': hashlib.sha256(geometry.wkb).hexdigest(),
                                            'values': [encoded, precision, bound, vertices]}, separators=(',', ':')), encoding='utf-8')
            rows_out.append([code, name, precision, encoded])
            maximum = max(maximum, bound)
            original_vertices += vertex_count(geometry)
            simplified_vertices += vertices
            islands += len(parts(geometry))
            holes += sum(topology(geometry))
            if len(rows_out) % 100 == 0:
                print(json.dumps({'level': level, 'done': len(rows_out)}), flush=True)
        print(json.dumps({'level': level, 'features': len(rows), 'done': len(rows_out)}), flush=True)
    expected_count = 3559 if dongs else 269
    if len(rows_out) != expected_count or len({row[0] for row in rows_out}) != expected_count or (not dongs and len({row[1] for row in rows_out}) != expected_count):
        raise ValueError('Unexpected region count or duplicate full name')
    metadata = {'schema': 1, 'referenceDate': REFERENCE_DATE, 'namespace': 'SGIS administrative',
               'sourceUrl': SOURCE_URL, 'archiveSha256': ARCHIVE_SHA,
               'maximumDisplayErrorMetres': round(maximum, 4)}
    groups = {}
    for row in sorted(rows_out):
        key = row[0][:5] if dongs else row[0]
        groups.setdefault(key, []).append(row)
    chunk_root = Path(output).parent / ('region-selection-dongs' if dongs else 'region-selection-areas')
    chunk_root.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    largest = 0
    refs = []
    for code, group in groups.items():
        body = json.dumps(group, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'
        if len(body) > 1024 * 1024:
            raise ValueError(f'One selection chunk exceeds 1 MiB: {code}: {len(body)}')
        filename = ('dongs-' if dongs else 'boundary-') + code + '.json'
        (chunk_root / filename).write_bytes(body)
        refs.append([code, len(body), hashlib.sha256(body).hexdigest()])
        total_bytes += len(body)
        largest = max(largest, len(body))
    payload = {**metadata, 'regions': [[r[0], r[1]] for r in sorted(rows_out)] if not dongs else [],
               'chunks': refs, 'featureCount': len(rows_out)}
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(data)
    report = {'regionCount': len(rows_out), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
              'originalVertices': original_vertices, 'displayVertices': simplified_vertices,
              'preservedPolygonComponents': islands, 'preservedHoles': holes,
              'maximumDisplayErrorMetres': maximum, 'toleranceMetres': tolerance,
              'lazyChunkCount': len(groups), 'lazyTotalBytes': total_bytes, 'largestLazyChunkBytes': largest,
              'sourceArchiveSha256': ARCHIVE_SHA, 'referenceDate': REFERENCE_DATE,
              'scope': 'One-area display highlight only; no legal-dong crosswalk or data aggregation'}
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--tolerance', type=float, default=20)
    parser.add_argument('--dongs', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.tolerance <= 25:
        parser.error('Selection overlay tolerance must be between 0 and 25 metres')
    build(args.archive, args.output, args.report, args.tolerance, args.dongs)
