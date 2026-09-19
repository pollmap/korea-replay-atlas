"""Acquire bounded Overture building parts and audit them without publication.

Run: python -m pipeline.building_parts --sample all
The outputs are review candidates, not terrain-anchored or published models.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import shutil

import overturemaps.core
import pyarrow.parquet as pq
from pyproj import Transformer
import shapely
from shapely.geometry import box, mapping
from shapely.ops import transform, unary_union

from .core import LOCAL, RELEASE, atomic_json, digest, now
from .height_quality import evaluate_feature

SAMPLES = {
    'seoul-parts-sample': (126.965, 37.550, 126.995, 37.580),
    'daejeon-parts-sample': (127.405, 36.310, 127.455, 36.355),
}
VERSION = 'overture-building-parts-review-3'
METRIC = Transformer.from_crs(4326, 5186, always_xy=True).transform


def _positive_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _geometry(row):
    try:
        geometry = shapely.from_wkb(row.get('geometry'))
        if geometry is None or geometry.is_empty or not geometry.is_valid:
            return None
        if geometry.geom_type not in ('Polygon', 'MultiPolygon'):
            return None
        return geometry
    except (TypeError, ValueError, shapely.GEOSException):
        return None


def acquire(sample: str, kind: str, *, root=LOCAL, release=RELEASE):
    """Store original rows once; fail on changed/corrupt cached content."""
    if sample not in SAMPLES or kind not in ('building', 'building_part'):
        raise ValueError('Use a named, bounded sample and supported feature type.')
    bbox = SAMPLES[sample]
    target = Path(root) / 'raw' / 'overture' / release / f'{sample}-{kind}.parquet'
    metadata = target.with_suffix('.meta.json')
    if target.exists():
        if not metadata.exists():
            raise RuntimeError(f'Existing source has no provenance: {target.name}')
        meta = json.loads(metadata.read_text(encoding='utf-8'))
        if meta.get('sha256') != digest(target) or meta.get('bbox') != list(bbox):
            raise RuntimeError(f'Existing source failed integrity check: {target.name}')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(target.parent).free < 30 * 1024**3:
        raise RuntimeError('Disk reserve below 30 GiB.')
    reader = overturemaps.core.record_batch_reader(
        kind, bbox=bbox, release=release, stac=True,
        connect_timeout=30, request_timeout=120,
    )
    if reader is None:
        raise RuntimeError('Overture source did not return a reader.')
    partial = target.with_suffix(f'.{os.getpid()}.part')
    count = 0
    with pq.ParquetWriter(partial, reader.schema, compression='zstd') as writer:
        for batch in reader:
            count += batch.num_rows
            if count > 100_000:
                raise RuntimeError('Bounded sample exceeded 100,000 records; source retained for investigation.')
            writer.write_batch(batch)
    partial.replace(target)
    atomic_json(metadata, {
        'source_id': 'overture', 'dataset_version': release, 'feature_type': kind,
        'bbox': list(bbox), 'count': count, 'sha256': digest(target),
        'bytes': target.stat().st_size, 'retrieved_at': now(),
        'source_url': f'https://overturemapswestus2.blob.core.windows.net/release/{release}/theme=buildings/type={kind}/',
    })
    print(json.dumps({'stage': 'building_parts_acquire', 'sample': sample, 'kind': kind,
                      'rows': count, 'bytes': target.stat().st_size}), flush=True)
    return target


def audit_rows(parents, parts, bbox, *, release=RELEASE):
    """Return source-faithful GeoJSON candidates plus linkage/geometry audit.

    No floor-derived heights, filled colours, fabricated roofs, or terrain are
    added. Stacked/overlapping parts are allowed by the upstream schema.
    """
    parent_counts = Counter(row.get('id') for row in parents)
    part_counts = Counter(row.get('id') for row in parts)
    parent_index = {row['id']: row for row in parents if row.get('id') and parent_counts[row['id']] == 1}
    parent_geometry = {key: _geometry(row) for key, row in parent_index.items()}
    requested = box(*bbox)
    seen_parents = Counter()
    candidate_parents = Counter()
    geometries_by_parent = {}
    part_results = []
    candidates = []
    counts = Counter()
    for row in parts:
        issues = []
        part_id, parent_id = row.get('id'), row.get('building_id')
        if not part_id or part_counts[part_id] > 1:
            issues.append('missing_or_duplicate_part_id')
        if not parent_id:
            issues.append('missing_parent_id')
        elif parent_counts[parent_id] > 1:
            issues.append('ambiguous_parent_id')
        elif parent_id not in parent_index:
            issues.append('parent_not_in_sample')
        else:
            seen_parents[parent_id] += 1
            if not parent_index[parent_id].get('has_parts'):
                counts['parent_has_parts_flag_missing_or_false'] += 1
        geom = _geometry(row)
        if geom is None:
            issues.append('invalid_part_geometry')
        elif not requested.intersects(geom):
            issues.append('outside_requested_bbox')
        parent_geom = parent_geometry.get(parent_id)
        containment = None
        if parent_id in parent_index and parent_geom is None:
            issues.append('invalid_parent_geometry')
        if geom is not None and parent_geom is not None:
            metric_part, metric_parent = transform(METRIC, geom), transform(METRIC, parent_geom)
            containment = min(1.0, max(0.0, metric_part.intersection(metric_parent).area / metric_part.area))
            if containment < .95:
                issues.append('part_outside_parent_footprint')
            geometries_by_parent.setdefault(parent_id, []).append(metric_part)
        height = row.get('height')
        minimum = row.get('min_height')
        if not _positive_number(height):
            issues.append('missing_or_invalid_source_height')
        elif height >= 900:
            issues.append('source_height_out_of_review_bounds')
        if minimum is not None and (isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not math.isfinite(minimum) or minimum < 0):
            issues.append('invalid_source_min_height')
        if minimum is None and _positive_number(row.get('min_floor')):
            issues.append('floating_part_without_source_min_height')
        if row.get('is_underground') is True:
            issues.append('underground_part_requires_separate_placement')
        if row.get('roof_shape') and row['roof_shape'] != 'flat':
            counts['nonflat_roof_requires_shape_model'] += 1
        # Zero is only a local reference for checking source height semantics.
        # It is never stored as ground elevation or published as a positioned part.
        vertical = evaluate_feature({'id': part_id, 'properties': {
            'height': height, 'min_height': 0 if minimum is None else minimum,
            'height_method': 'source', 'dataset_version': release, 'base_height': 0}})
        simple_extrusion = (not issues and vertical['render_eligible']
                            and row.get('roof_shape') in (None, 'flat'))
        if not vertical['render_eligible']:
            counts['height_quality_requires_review'] += 1
        if simple_extrusion:
            counts['extrusion_candidates_pending_ground'] += 1
        for issue in issues:
            counts[issue] += 1
        result = {
            'part_id': part_id, 'building_id': parent_id,
            'source_height_m': height if _positive_number(height) else None,
            'source_min_height_m': minimum if isinstance(minimum, (int, float)) and not isinstance(minimum, bool) and math.isfinite(minimum) else None,
            'containment_ratio': containment, 'issues': issues,
            'candidate': not issues,
            'height_semantics': vertical['height_semantics'],
            'height_semantics_basis': vertical['height_semantics_basis'],
            'height_quality_flags': vertical['quality_flags'],
            'extrusion_candidate_pending_ground': simple_extrusion,
            'render_eligible': False,
        }
        part_results.append(result)
        if not issues:
            candidate_parents[parent_id] += 1
            properties = {key: row.get(key) for key in (
                'building_id', 'height', 'min_height', 'num_floors', 'min_floor',
                'roof_shape', 'roof_height', 'roof_direction', 'roof_orientation',
                'roof_material', 'roof_color', 'facade_material', 'facade_color',
                'is_underground', 'level', 'sources', 'names',
            )}
            properties.update({'source_id': 'overture', 'evidence_type': 'source_attribute',
                               'review_only': True, 'height_method': 'source',
                               'dataset_version': release,
                               'raw_height': height, 'render_height': None,
                               'height_semantics': vertical['height_semantics'],
                               'height_semantics_basis': vertical['height_semantics_basis'],
                               'height_quality_flags': vertical['quality_flags'],
                               'render_eligible': False,
                               'extrusion_candidate_pending_ground': simple_extrusion,
                               'description': '원천 부분 건물 속성. 지형 배치·지붕 형상은 아직 구현하지 않은 검토 후보입니다.'})
            candidates.append({'type': 'Feature', 'id': part_id, 'geometry': mapping(geom), 'properties': properties})
    parent_results = []
    for key, row in parent_index.items():
        geom = parent_geometry[key]
        if not row.get('has_parts') and key not in seen_parents:
            continue
        touches_boundary = geom is not None and not requested.contains(geom)
        count = seen_parents[key]
        coverage = None
        if geom is not None and geometries_by_parent.get(key):
            metric_parent = transform(METRIC, geom)
            coverage = min(1.0, max(0.0, unary_union(geometries_by_parent[key]).intersection(metric_parent).area / metric_parent.area))
        parent_results.append({'building_id': key, 'has_parts': row.get('has_parts'),
                               'observed_parts': count, 'touches_sample_boundary': touches_boundary,
                               'candidate_parts': candidate_parents[key],
                               'all_observed_parts_are_candidates': count > 0 and candidate_parents[key] == count,
                               'parts_footprint_coverage': coverage,
                               'missing_parts_in_sample': bool(row.get('has_parts')) and count == 0})
    counts.update({'parent_rows': len(parents), 'part_rows': len(parts), 'candidate_parts': len(candidates),
                   'parents_with_parts_in_sample': len(seen_parents),
                   'parents_flagged_has_parts': sum(bool(row.get('has_parts')) for row in parents),
                   'parents_flagged_but_no_parts': sum(r['missing_parts_in_sample'] for r in parent_results),
                   'duplicate_parent_ids': sum(n > 1 for n in parent_counts.values()),
                   'duplicate_part_ids': sum(n > 1 for n in part_counts.values())})
    report = {'counts': dict(counts), 'parts': part_results, 'parents': parent_results,
              'limitations': [
                  'Bounding-box extraction may omit a parent or sibling outside the selected area; absence in this sample is not global absence.',
                  'has_parts does not state an expected number of parts. Footprint coverage is not proof of completeness.',
                  'Source heights are attributes, not independently measured survey values.',
                  'No ground altitude or final vertical placement was calculated; preserve height/min_height before rendering.',
                  'Part footprint overlap may be intentional for vertically stacked parts.',
                  'Candidate means retained for review, not renderable. Positive min_height requires source-version height semantics; no ground placement has been approved.',
              ]}
    return {'type': 'FeatureCollection', 'features': candidates}, report


def review(sample: str, *, root=LOCAL, release=RELEASE):
    inputs = {kind: acquire(sample, kind, root=root, release=release) for kind in ('building', 'building_part')}
    parents, parts = (pq.read_table(inputs[kind]).to_pylist() for kind in ('building', 'building_part'))
    candidates, report = audit_rows(parents, parts, SAMPLES[sample], release=release)
    provenance = {kind: json.loads(path.with_suffix('.meta.json').read_text(encoding='utf-8')) for kind, path in inputs.items()}
    key = hashlib.sha256(json.dumps({'inputs': [provenance[k]['sha256'] for k in inputs], 'transform': VERSION}, sort_keys=True).encode()).hexdigest()[:16]
    output = Path(root) / 'silver' / 'reviewparts' / release / f'{sample}-{key}'
    for feature in candidates['features']:
        feature['properties']['provenance'] = {
            **provenance['building_part'], 'source_record_id': feature['id'],
            'transform_version': VERSION, 'unit': 'm', 'observed_at': None,
            'evidence_type': 'source_attribute',
        }
    report.update({'sample': sample, 'bbox': SAMPLES[sample], 'dataset_version': release,
                   'transform_version': VERSION, 'inputs': provenance, 'publication_state': 'review_only'})
    atomic_json(output.with_suffix('.geojson'), candidates)
    atomic_json(output.with_suffix('.audit.json'), report)
    print(json.dumps({'sample': sample, 'counts': report['counts'], 'output': str(output)}, ensure_ascii=False), flush=True)
    return output.with_suffix('.audit.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample', choices=[*SAMPLES, 'all'], default='all')
    args = parser.parse_args()
    for sample in SAMPLES if args.sample == 'all' else [args.sample]:
        review(sample)


if __name__ == '__main__':
    main()
