"""Prepare local official building records for review; never publish or merge.

AL_D010 snapshots and CH_D010 changes have different A23 meanings. A change
code mapping must be explicitly supplied from the acquired data's contract.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re

from pyproj import CRS, Transformer
import shapely
from shapely.geometry import shape, mapping
from shapely.ops import transform

from .core import LOCAL, digest

VERSION = 'official-buildings-review-2'
COMMON_FIELDS = {
    'A0': '원천도형ID', 'A1': 'GIS건물통합식별번호', 'A2': '고유번호',
    'A3': '법정동코드', 'A4': '법정동명', 'A5': '지번', 'A6': '특수지코드',
    'A7': '특수지구분명', 'A8': '건축물용도코드', 'A9': '건축물용도명',
    'A10': '건축물구조코드', 'A11': '건축물구조명', 'A12': '건축물면적(㎡)',
    'A13': '사용승인일자', 'A14': '연면적', 'A15': '대지면적(㎡)',
    'A16': '높이(m)', 'A17': '건폐율(%)', 'A18': '용적율(%)',
    'A19': '건축물ID', 'A20': '위반건축물여부', 'A21': '참조체계연계키',
    'A22': '데이터기준일자', 'A24': '건물명', 'A25': '건물동명',
    'A26': '지상층_수', 'A27': '지하층_수', 'A28': '데이터생성변경일자',
}
SCHEMAS = {
    'AL_D010': {**COMMON_FIELDS, 'A23': '원천시도시군구코드'},
    'CH_D010': {**COMMON_FIELDS, 'A23': '변동순번', 'A29': '입력구분', 'A30': '원천시도시군구코드'},
}
EVENTS = {'insert', 'update', 'delete'}
SOURCE_SPEC = 'https://www.vworld.kr/contents/국가중점데이터_컬럼정의서(26.01.02)_배포용.xlsx'
NUMBER = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$')
TO_METRIC = Transformer.from_crs(4326, 5186, always_xy=True).transform


def text_value(value):
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and not NUMBER.fullmatch(value.strip()):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def date_value(value):
    raw = text_value(value)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def validate_event_map(table, event_map):
    if table not in SCHEMAS:
        raise ValueError('table must be AL_D010 or CH_D010')
    if table == 'CH_D010' and (not event_map or not isinstance(event_map, dict)):
        raise ValueError('CH_D010 requires an explicit source code -> insert/update/delete event map.')
    if event_map and any(not isinstance(k, str) or not k.strip() or v not in EVENTS for k, v in event_map.items()):
        raise ValueError('Unsupported change-event mapping.')


def normalize_record(properties, geometry, *, table, source_crs, event_map=None):
    """Retain official values, independently classify record/event completeness."""
    validate_event_map(table, event_map)
    crs = CRS.from_user_input(source_crs)
    issues = []
    expected = SCHEMAS[table]
    missing_fields = sorted(set(expected) - properties.keys(), key=lambda key: int(key[1:]))
    required = [f'A{i}' for i in range(23)] + ['A23' if table == 'AL_D010' else 'A30']
    empty_required = [field for field in required if text_value(properties.get(field)) is None]
    source_id = text_value(properties.get('A1'))
    if source_id is None:
        issues.append('missing_building_id')
    elif not isinstance(properties.get('A1'), str):
        issues.append('building_id_must_be_string_to_preserve_precision')
    jurisdiction = text_value(properties.get('A23' if table == 'AL_D010' else 'A30'))
    if jurisdiction is None or not re.fullmatch(r'\d{5}', jurisdiction):
        issues.append('invalid_jurisdiction_code')
    height = number(properties.get('A16'))
    raw_height = height
    if height is None or not 0 < height < 900:
        height = None
        issues.append('missing_or_invalid_height')
    floors = {}
    for name, field in [('above_ground', 'A26'), ('below_ground', 'A27')]:
        value = number(properties.get(field))
        floors[name] = int(value) if value is not None and value >= 0 and value.is_integer() else None
        if value is not None and floors[name] is None:
            issues.append(f'invalid_{name}_floors')
    height_flags = ['administrative_height_measurement_convention_unverified']
    if height is not None and height < 1:
        issues.append('source_height_below_1m_review')
        height_flags.append('source_height_below_1m_review')
        if floors['above_ground']:
            height_flags.append('source_height_floor_count_conflict_review')
    as_of = date_value(properties.get('A22'))
    if as_of is None:
        issues.append('missing_or_invalid_reference_date')
    change_sequence = text_value(properties.get('A23')) if table == 'CH_D010' else None
    source_event = text_value(properties.get('A29')) if table == 'CH_D010' else None
    event = 'snapshot' if table == 'AL_D010' else (event_map or {}).get(source_event)
    if table == 'CH_D010':
        if event is None:
            issues.append('unmapped_change_code')
        if change_sequence is None:
            issues.append('missing_change_sequence')
    normalized_geom = None
    if geometry is None:
        issues.append('missing_geometry')
    else:
        try:
            geom = shapely.from_wkt(geometry) if isinstance(geometry, str) else shape(geometry)
            if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon', 'MultiPolygon'):
                raise ValueError('Invalid footprint')
            geom = transform(Transformer.from_crs(crs, 4326, always_xy=True).transform, geom)
            west, south, east, north = geom.bounds
            if not all(math.isfinite(v) for v in geom.bounds) or not (124 <= west <= east <= 133 and 32 <= south <= north <= 40):
                raise ValueError('Outside Korea validation envelope')
            if not geom.is_valid:
                raise ValueError('Transformed geometry invalid')
            # Coordinates in degrees mislabelled as a Korean metric CRS can
            # still transform into Korea, but yield microscopic footprints.
            metric_area = transform(TO_METRIC, geom).area
            if not math.isfinite(metric_area) or metric_area < 1:
                raise ValueError('Footprint below the one-square-metre review threshold')
            normalized_geom = mapping(geom)
        except (TypeError, ValueError, shapely.GEOSException):
            issues.append('invalid_geometry_or_crs')
    if missing_fields:
        issues.append('missing_schema_fields')
    if empty_required:
        issues.append('empty_required_fields')
    event_valid = bool(source_id and jurisdiction and len(jurisdiction) == 5 and jurisdiction.isdigit() and as_of and event)
    if table == 'CH_D010':
        event_valid = event_valid and change_sequence is not None
    if event != 'delete':
        event_valid = event_valid and normalized_geom is not None and height is not None
    event_valid = event_valid and isinstance(properties.get('A1'), str)
    record = {
        'source_id': 'molit-gis-building', 'table': table, 'source_record_id': source_id,
        'source_shape_id': text_value(properties.get('A0')), 'building_register_id': text_value(properties.get('A19')),
        'jurisdiction_code': jurisdiction, 'source_event_code': source_event,
        'event': event, 'change_sequence': change_sequence,
        'as_of': as_of, 'source_updated_at': date_value(properties.get('A28')),
        'height_m': height, 'height_method': 'official_register_attribute' if height is not None else 'unknown',
        'raw_height_m': raw_height, 'render_height_m': None,
        'height_semantics': 'administrative_height_definition_unverified',
        'height_quality_flags': height_flags, 'height_accuracy_verified': False,
        'height_evidence': 'official_record' if height is not None else 'unverified',
        'height_datum': 'relative building height; detailed measurement convention not specified in field dictionary',
        'floors': floors, 'geometry': normalized_geom, 'geometry_crs': 'EPSG:4326',
        'input_crs': crs.to_string(), 'source_properties': dict(properties),
        'quality': {'complete': not issues, 'event_valid': event_valid,
                    'schema_complete': not missing_fields, 'missing_fields': missing_fields,
                    'empty_required_fields': empty_required, 'issues': issues},
        'publication_state': 'review_only',
    }
    return record


def iter_input(path, encoding='utf-8-sig'):
    suffix = path.suffix.lower()
    if suffix == '.csv':
        with path.open(encoding=encoding, newline='') as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError('CSV column names are missing or duplicated.')
            for row in reader:
                if None in row:
                    raise ValueError('CSV row has more values than columns.')
                yield {k: v for k, v in row.items() if k != 'geometry_wkt'}, row.get('geometry_wkt')
    elif suffix in ('.geojson', '.json'):
        def reject_nonfinite(value):
            raise ValueError(f'Non-finite JSON value is not allowed: {value}')
        document = json.loads(path.read_text(encoding=encoding), parse_constant=reject_nonfinite)
        if document.get('type') != 'FeatureCollection' or not isinstance(document.get('features'), list):
            raise ValueError('GeoJSON FeatureCollection is required.')
        for feature in document['features']:
            if feature.get('type') != 'Feature' or not isinstance(feature.get('properties'), dict):
                raise ValueError('Every feature needs an object properties field.')
            yield feature['properties'], feature.get('geometry')
    else:
        raise ValueError('Only CSV with geometry_wkt or GeoJSON is supported. Export SHP/GPKG in QGIS first; original files are not changed.')


def _write_immutable(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError(f'Immutable journal conflict: {path.name}')
        return
    # Exclusive creation prevents a concurrent invocation overwriting a journal.
    with path.open('xb') as stream:
        stream.write(content)


def prepare(path, *, table, source_crs, event_map=None, output_root=None, encoding='utf-8-sig'):
    path = Path(path).resolve(strict=True)
    validate_event_map(table, event_map)
    crs = CRS.from_user_input(source_crs).to_string()
    raw_hash = digest(path)
    contract = {'source_hash': raw_hash, 'table': table, 'source_crs': crs,
                'event_map': event_map or {}, 'encoding': encoding, 'transform_version': VERSION}
    batch_id = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    folder = Path(output_root) if output_root is not None else LOCAL / 'silver' / 'official-buildings' / 'journal'
    records = []
    counts = Counter()
    identities = Counter()
    for properties, geometry in iter_input(path, encoding):
        if len(records) >= 200_000:
            raise ValueError('Review batch exceeds 200,000 records; split the source into bounded files before importing.')
        record = normalize_record(properties, geometry, table=table, source_crs=crs, event_map=event_map)
        record['provenance'] = {'input_sha256': raw_hash, 'source_file': path.name,
                                'source_spec': SOURCE_SPEC, 'transform_version': VERSION,
                                'source_row': len(records) + 1}
        identity = (record['jurisdiction_code'], record['source_record_id'], record['change_sequence'])
        identities[identity] += 1
        records.append(record)
    # Duplicates are retained as evidence and excluded from complete/matching candidates.
    for record in records:
        identity = (record['jurisdiction_code'], record['source_record_id'], record['change_sequence'])
        if identities[identity] > 1:
            record['quality']['issues'].append('duplicate_source_identity')
            record['quality']['complete'] = False
            record['quality']['event_valid'] = False
        counts['records'] += 1
        counts['complete'] += int(record['quality']['complete'])
        counts['valid_events'] += int(record['quality']['event_valid'])
        counts[record['event'] or 'unmapped'] += 1
        for issue in record['quality']['issues']:
            counts[f'issue:{issue}'] += 1
    # Detect an input changing under us; never claim a hash for other bytes.
    if digest(path) != raw_hash:
        raise RuntimeError('Source changed during import; no journal was published.')
    journal = folder / f'{batch_id}.jsonl'
    manifest = folder / f'{batch_id}.manifest.json'
    content = ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + '\n' for row in records).encode('utf-8')
    report = {**contract, 'batch_id': batch_id, 'source_file': path.name,
              'journal_sha256': hashlib.sha256(content).hexdigest(), 'counts': dict(counts),
              'publication_state': 'review_only', 'applied_to_existing_data': False}
    _write_immutable(journal, content)
    _write_immutable(manifest, json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8'))
    return report, journal


def propose_matches(records, targets, *, min_iou=.8, max_centroid_distance_m=10.0):
    """Suggest unique high-overlap candidates, without changing either dataset.

    targets are GeoJSON Features in WGS84, each with a unique id. Incomplete
    records and change events are intentionally excluded from auto-match plans.
    """
    if not 0 < min_iou <= 1 or not 0 < max_centroid_distance_m <= 100:
        raise ValueError('Invalid matching thresholds.')
    target_counts = Counter(feature.get('id') for feature in targets)
    record_counts = Counter((r.get('jurisdiction_code'), r.get('source_record_id')) for r in records)
    valid_targets = []
    metric_targets = []
    for feature in targets:
        if not feature.get('id') or target_counts[feature['id']] != 1:
            continue
        try:
            geom = shape(feature['geometry'])
            if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon', 'MultiPolygon'):
                continue
            metric = transform(TO_METRIC, geom)
            if not math.isfinite(metric.area) or metric.area <= 0:
                continue
            valid_targets.append(feature)
            metric_targets.append(metric)
        except (KeyError, TypeError, ValueError, shapely.GEOSException):
            continue
    tree = shapely.STRtree(metric_targets)
    candidates = []
    exclusions = []
    for record in records:
        identity = (record.get('jurisdiction_code'), record.get('source_record_id'))
        height = number(record.get('height_m'))
        if (not record.get('quality', {}).get('complete') or record.get('event') != 'snapshot'
                or record_counts[identity] != 1 or height is None or not 0 < height < 900):
            exclusions.append({'source_record_id': identity[1], 'reason': 'incomplete_non_snapshot_or_duplicate'})
            continue
        try:
            official = transform(TO_METRIC, shape(record['geometry']))
        except (KeyError, TypeError, ValueError, shapely.GEOSException):
            exclusions.append({'source_record_id': identity[1], 'reason': 'invalid_geometry'})
            continue
        for index in tree.query(official, predicate='intersects'):
            target = metric_targets[index]
            intersection = official.intersection(target).area
            union = official.area + target.area - intersection
            iou = min(1.0, intersection / union) if union > 0 else 0
            distance = official.centroid.distance(target.centroid)
            area_ratio = official.area / target.area
            if iou >= min_iou and distance <= max_centroid_distance_m and .8 <= area_ratio <= 1.25:
                candidates.append({'source_record_id': identity[1], 'jurisdiction_code': identity[0],
                                   'target_id': valid_targets[index]['id'], 'iou': iou,
                                   'centroid_distance_m': distance, 'area_ratio': area_ratio,
                                   'height_m': record['height_m'], 'confidence': 'high_spatial_agreement',
                                   'status': 'review_candidate'})
    source_degrees = Counter((x['jurisdiction_code'], x['source_record_id']) for x in candidates)
    target_degrees = Counter(x['target_id'] for x in candidates)
    unique, ambiguous = [], []
    for candidate in candidates:
        if source_degrees[(candidate['jurisdiction_code'], candidate['source_record_id'])] == 1 and target_degrees[candidate['target_id']] == 1:
            unique.append(candidate)
        else:
            ambiguous.append({**candidate, 'status': 'ambiguous_do_not_apply'})
    return {'matches': unique, 'ambiguous': ambiguous, 'excluded': exclusions,
            'duplicate_target_ids': sum(n > 1 for n in target_counts.values()),
            'applied': False, 'requires_review': True,
            'thresholds': {'minimum_iou': min_iou, 'maximum_centroid_distance_m': max_centroid_distance_m,
                           'area_ratio': [.8, 1.25]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--table', required=True, choices=list(SCHEMAS))
    parser.add_argument('--source-crs', required=True, help='Read from the original PRJ/metadata; e.g. EPSG:5186')
    parser.add_argument('--encoding', default='utf-8-sig')
    parser.add_argument('--event-map', type=Path, help='Local JSON mapping verified source event codes to insert/update/delete')
    parser.add_argument('--output-root', type=Path)
    args = parser.parse_args()
    events = json.loads(args.event_map.read_text(encoding='utf-8')) if args.event_map else None
    report, path = prepare(args.input, table=args.table, source_crs=args.source_crs,
                           encoding=args.encoding, event_map=events, output_root=args.output_root)
    print(json.dumps({'journal': str(path), **report}, ensure_ascii=False))


if __name__ == '__main__':
    main()
