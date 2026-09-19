"""Source-preserving height decisions for Korea building rendering.

This policy is a review gate, not a claim of surveyed accuracy. Original
normalized features remain immutable. ``render_height`` always means distance
from the terrain base to the top, regardless of upstream height terminology.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path

VERSION = 'korea-height-quality-1'
SCHEMA_COMMIT = '79016c3a56e5c0d7660f911117d59801451e918d'
# Values compared against the exact historical OSM record, not its current state.
VERIFIED_OSM = {
    'c883dd77-ca5c-47e0-82d7-888ee80f1bf7': (18.0, 15.0, 'w711239221@3'),
    '35366230-3166-3235-B463-376261376263': (112.0, 100.0, 'w1214950322@1'),
}


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def evaluate_feature(feature):
    """Return metadata + rendering eligibility without changing the input.

    No arbitrary raw ``height_semantics`` property is trusted. Positive floating
    heights require a version/value-matched evidence entry. Unresolved floating
    geometry, very low source heights and low terrain are retained as footprints.
    Estimates are renderable, explicitly labeled, and never called surveyed.
    """
    prop = feature.get('properties') or {}
    height = prop.get('height')
    minimum = prop.get('min_height', 0)
    ground = prop.get('base_height')
    method = prop.get('height_method', 'unknown')
    identity = feature.get('id')
    provenance = prop.get('provenance') or {}
    record_id = provenance.get('source_record_id') or identity
    flags = []
    eligible = True
    semantics = 'unresolved'
    basis = 'unresolved'
    upstream_id = None
    raw_height = height if method == 'source' else None
    if height is None:
        eligible = False
        flags.append('height_unknown')
    elif not finite(height) or height <= 0:
        eligible = False
        flags.append('height_invalid')
    elif height > 1000:
        eligible = False
        flags.append('height_above_1000m_review')
    if not finite(ground):
        eligible = False
        flags.append('ground_height_invalid')
    elif ground < -30:
        eligible = False
        flags.append('ellipsoidal_base_below_minus_30m_review')
    if not finite(minimum) or minimum < 0:
        eligible = False
        flags.append('min_height_invalid')
    elif minimum == 0:
        semantics = 'ground_to_top'
        basis = 'zero_bottom_offset_equivalence'
    else:
        entry = VERIFIED_OSM.get(identity)
        version = prop.get('dataset_version') or provenance.get('dataset_version')
        if entry and version == '2026-08-19.0' and height == entry[0] and minimum == entry[1] and method == 'source':
            semantics = 'ground_to_top'
            basis = 'exact_version_osm_tags'
            upstream_id = entry[2]
            flags.append('upstream_schema_semantics_conflict')
        else:
            eligible = False
            flags.append('height_semantics_unresolved')
        if finite(height) and minimum >= height:
            eligible = False
            flags.append('bottom_not_below_top_review')
    if method == 'source' and finite(height) and 0 < height < 1:
        eligible = False
        flags.append('source_height_below_1m_review')
    if method == 'floors':
        flags.append('floor_height_estimate')
    elif method == 'ghsl_cell_average_2018':
        flags.append('ghsl_2018_grid_mean_estimate')
    elif method not in ('source', 'unknown'):
        eligible = False
        flags.append('height_method_unrecognized')
    state = ('unknown' if height is None and flags == ['height_unknown'] else
             'review_required' if not eligible else
             'estimated' if method in ('floors', 'ghsl_cell_average_2018') else
             'verified_semantics' if basis == 'exact_version_osm_tags' else 'source_attribute')
    return {
        'quality_policy': VERSION,
        'source_record_id': str(record_id) if record_id is not None else '',
        'upstream_record_id': upstream_id,
        'raw_height': raw_height if finite(raw_height) else None,
        'input_height': height if finite(height) else None,
        'raw_min_height': minimum if finite(minimum) else None,
        'render_height': float(height) if eligible else None,
        'render_min_height': float(minimum) if eligible else None,
        'height_semantics': semantics,
        'height_semantics_basis': basis,
        'quality_flags': flags,
        'quality_state': state,
        'ground_vertical_datum': 'EPSG:4979',
        'ground_accuracy_verified': False,
        'render_eligible': eligible,
    }


def audit(root, output):
    """One snapshot, streaming source hashes, bounded samples, no source mutation."""
    from .building_identity_audit import JsonCursor, capture_assets, input_path
    from .core import atomic_json, now
    snapshot = capture_assets(root / '.local/catalog.sqlite')
    assets = sorted((a for a in snapshot['assets'] if a['id'].startswith('normalized-buildings-')), key=lambda a: a['id'])
    counts, flags, states, methods = Counter(), Counter(), Counter(), Counter()
    samples, inputs = {}, []
    for index, asset in enumerate(assets):
        path = input_path(root, asset, private=True)
        rows = 0
        with path.open('rb') as stream:
            cursor = JsonCursor(stream)
            for feature in cursor.features():
                result = evaluate_feature(feature)
                counts['rows'] += 1
                counts['render_eligible' if result['render_eligible'] else 'footprint_only'] += 1
                states[result['quality_state']] += 1
                methods[(feature.get('properties') or {}).get('height_method', 'missing')] += 1
                flags.update(result['quality_flags'])
                rows += 1
                for flag in result['quality_flags']:
                    bucket = samples.setdefault(flag, [])
                    if len(bucket) < 5:
                        bucket.append({'id': feature.get('id'), 'parent': asset['id'], **result})
        if cursor.digest.hexdigest() != asset['sha256'] or rows != asset['count']:
            raise ValueError('Normalized asset hash/count mismatch; quality report not published')
        inputs.append({'asset_id': asset['id'], 'sha256': asset['sha256'], 'rows': rows})
        if (index + 1) % 100 == 0:
            print(json.dumps({'stage': 'height-quality', 'assets_done': index + 1, 'rows': counts['rows']}), flush=True)
    report = {'policy': VERSION, 'generated_at': now(), 'snapshot_sha256': snapshot['sha256'],
              'notice': '확보한 원천의 렌더링 검토 판정이며 대한민국 실제 건물의 완전성·실측 정확도 증명이 아닙니다. 검토 임계값은 오류 확정이 아닙니다.',
              'data_quality_complete': False, 'counts': dict(counts), 'quality_flags': dict(flags),
              'quality_states': dict(states), 'height_methods': dict(methods), 'samples': samples, 'inputs': inputs}
    atomic_json(output, report)
    print(json.dumps({k: report[k] for k in ('policy', 'counts', 'quality_flags', 'quality_states')}, ensure_ascii=False))
    return report


def raster_point(dataset, lon, lat):
    """Bilinear COG samples at GDAL pixel centers; reject masked/border input."""
    import numpy as np
    from rasterio.windows import Window
    col, row = (~dataset.transform) * (lon, lat)
    col -= .5
    row -= .5
    left, top = math.floor(col), math.floor(row)
    if left < 0 or top < 0 or left + 1 >= dataset.width or top + 1 >= dataset.height:
        raise ValueError('Comparison sample requires the adjacent source tile')
    values = dataset.read(1, window=Window(left, top, 2, 2), masked=True)
    if np.any(np.ma.getmaskarray(values)) or not np.all(np.isfinite(values)):
        raise ValueError('No-data is not a ground or sea-level observation')
    x, y = col - left, row - top
    weights = np.array([[(1-x)*(1-y), x*(1-y)], [(1-x)*y, x*y]])
    return {'orthometric_height_m': float(np.sum(values * weights)),
            'pixels_m': values.tolist(), 'weights': weights.tolist(),
            'pixel_corner': [left, top], 'sampling': 'bilinear_pixel_centers'}


def ground_review(root, output):
    """Independently compare three known cases; never change ground/models."""
    import rasterio
    from pyproj import Transformer, datadir
    from .core import download, digest, atomic_json, now
    inputs = root / '.local/audit/building-height-extremes.json'
    samples = json.loads(inputs.read_text(encoding='utf-8'))['low_base_samples'][:3]
    directory = root / '.local/raw/ground-quality'
    grid = download('https://cdn.proj.org/us_nga_egm08_25.tif', root / '.local/grids/us_nga_egm08_25.tif')
    datadir.append_data_dir(str(grid.parent))
    transformer = Transformer.from_crs('EPSG:4326+3855', 'EPSG:4979', always_xy=True,
                                       allow_ballpark=False, only_best=True)
    outputs = []
    for item in samples:
        lon, lat = item['lon'], item['lat']
        name = f'Copernicus_DSM_COG_10_N{math.floor(lat):02d}_00_E{math.floor(lon):03d}_00_DEM'
        url = f'https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif'
        path = download(url, directory / (name + '.tif'))
        with rasterio.open(path) as dataset:
            if dataset.crs.to_epsg() != 4326:
                raise ValueError('Unexpected Copernicus horizontal CRS')
            sample = raster_point(dataset, lon, lat)
            raster_metadata = {'crs': str(dataset.crs), 'nodata': dataset.nodata,
                               'area_or_point': dataset.tags().get('AREA_OR_POINT'),
                               'resolution_degrees': list(dataset.res)}
        ellipsoid = transformer.transform(lon, lat, sample['orthometric_height_m'], errcheck=True)[2]
        if not finite(ellipsoid):
            raise ValueError('EGM2008 -> WGS84 ellipsoid conversion failed')
        outputs.append({'id': item['id'], 'lon': lon, 'lat': lat,
            'mapzen_egm96_m': item['egm96_interpolated_m'],
            'mapzen_ellipsoid_m': item['base_height'],
            'copernicus_egm2008_m': sample['orthometric_height_m'],
            'copernicus_ellipsoid_m': ellipsoid,
            'copernicus_geoid_offset_m': ellipsoid - sample['orthometric_height_m'],
            'ellipsoid_difference_m': ellipsoid - item['base_height'],
            'comparison_result': 'review_required', 'ground_replacement_authorized_by_evidence': False,
            'source_url': url, 'source_sha256': digest(path), 'raster': raster_metadata, **sample})
    report = {'policy': VERSION, 'generated_at': now(), 'input_audit_sha256': digest(inputs),
        'notice': '독립 공개 DSM과 동일 타원체 기준 비교입니다. Copernicus DSM은 건물·수목을 포함하므로 실측 지면이나 대체 지표고로 자동 채택하지 않습니다. 원본·모델을 수정하지 않았습니다.',
        'source_reference': 'https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM',
        'source_vertical_crs': 'EPSG:3855', 'comparison_vertical_crs': 'EPSG:4979',
        'egm2008_grid_sha256': digest(grid), 'vertical_operation': transformer.description,
        'samples': outputs, 'data_quality_complete': False}
    atomic_json(output, report)
    print(json.dumps(report, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--ground-review', action='store_true', help='Download public comparison DSM/geoid; never replace terrain.')
    args = parser.parse_args()
    if args.ground_review:
        ground_review(args.root, args.output or args.root / '.local/audit/ground-quality-comparison.json')
    else:
        audit(args.root, args.output or args.root / '.local/audit/height-quality.json')


if __name__ == '__main__':
    main()
