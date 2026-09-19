"""Bounded, read-only source audit. Run once after national conversion finishes.

python -m pipeline.building_identity_audit
Only the audit report and a new private audit ledger are written. Input files,
the publisher catalog, and normalized building records are never changed.
"""
from __future__ import annotations

import argparse
import codecs
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from urllib.parse import unquote, urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
NOTICE = ('확보한 정규화 건물과 공개 자산 사이의 ID·개수·높이 속성 감사입니다. '
          '대한민국 모든 실제 건물의 완전성이나 실측 정확도를 증명하지 않습니다.')


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


class JsonCursor:
    """Incremental UTF-8 JSON decoder bounded by one feature (default 16 MiB)."""
    def __init__(self, stream, chunk_size=65536, max_value_bytes=16 * 1024 * 1024):
        self.stream = stream
        self.chunk_size = chunk_size
        self.max_value_bytes = max_value_bytes
        self.buffer = ''
        self.position = 0
        self.eof = False
        self.decoder = codecs.getincrementaldecoder('utf-8')()
        self.json_decoder = json.JSONDecoder()
        self.digest = hashlib.sha256()

    def fill(self):
        if self.eof:
            return False
        self.buffer = self.buffer[self.position:]
        self.position = 0
        block = self.stream.read(self.chunk_size)
        self.digest.update(block)
        self.eof = not block
        self.buffer += self.decoder.decode(block, final=self.eof)
        if len(self.buffer.encode('utf-8')) > self.max_value_bytes:
            raise ValueError('JSON value exceeds bounded audit size')
        return bool(block)

    def peek(self):
        while True:
            while self.position < len(self.buffer) and self.buffer[self.position].isspace():
                self.position += 1
            if self.position < len(self.buffer):
                return self.buffer[self.position]
            if not self.fill():
                return ''

    def take(self, char):
        if self.peek() != char:
            raise ValueError('Unexpected JSON delimiter')
        self.position += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.json_decoder.raw_decode(self.buffer, self.position)
                # Numeric scalars can end at a chunk boundary before their next digit.
                if end == len(self.buffer) and not self.eof:
                    self.fill()
                    continue
                self.position = end
                return value
            except json.JSONDecodeError:
                if not self.fill():
                    raise ValueError('Invalid or truncated GeoJSON') from None

    def features(self):
        self.take('{')
        seen = set()
        collection_type = None
        while self.peek() != '}':
            key = self.value()
            if not isinstance(key, str) or key in seen:
                raise ValueError('Invalid or duplicate FeatureCollection key')
            seen.add(key)
            self.take(':')
            if key == 'features':
                self.take('[')
                if self.peek() != ']':
                    while True:
                        yield self.value()
                        if self.peek() == ']':
                            break
                        self.take(',')
                        if self.peek() == ']':
                            raise ValueError('Trailing feature comma')
                self.take(']')
            else:
                value = self.value()
                if key == 'type':
                    collection_type = value
            if self.peek() == '}':
                break
            self.take(',')
            if self.peek() == '}':
                raise ValueError('Trailing collection comma')
        self.take('}')
        if self.peek() or collection_type != 'FeatureCollection' or 'features' not in seen:
            raise ValueError('Expected exactly one GeoJSON FeatureCollection')


def capture_assets(db_path):
    with sqlite3.connect(Path(db_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=60) as db:
        db.execute('BEGIN')
        assets = [json.loads(row[0]) for row in db.execute('SELECT payload FROM assets ORDER BY id')]
        db.rollback()
    selected = [a for a in assets if a['id'].startswith('normalized-buildings-') or a.get('layer') == 'buildings']
    return {'captured_at': now(), 'sha256': hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest(),
            'assets': selected}


def input_path(root, asset, private=False):
    base = (Path(root) / ('.local/silver/buildings' if private else 'public/data')).resolve()
    if private:
        path = Path(asset['path']).resolve()
    else:
        parsed = urlsplit(asset['url'])
        if parsed.scheme or parsed.netloc or not parsed.path.startswith('/data/'):
            raise ValueError('Expected local public asset URL')
        path = (base / unquote(parsed.path.removeprefix('/data/'))).resolve()
    if not path.is_relative_to(base):
        raise ValueError('Input path is outside the expected artifact directory')
    return path


def finite_number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def inspect_feature(feature):
    if not isinstance(feature, dict) or feature.get('type') != 'Feature':
        raise ValueError('Non-Feature entry in normalized collection')
    prop = feature.get('properties')
    if not isinstance(prop, dict):
        raise ValueError('Feature properties must be an object')
    provenance = prop.get('provenance') if isinstance(prop.get('provenance'), dict) else {}
    feature_id, source_id = feature.get('id'), provenance.get('source_record_id')
    flags = []
    for name, value in [('feature_id', feature_id), ('source_record_id', source_id)]:
        if not isinstance(value, str) or not value:
            flags.append(name + '_missing_or_invalid')
    if isinstance(feature_id, str) and isinstance(source_id, str) and feature_id != source_id:
        flags.append('feature_provenance_id_mismatch')
    height, base, minimum = prop.get('height'), prop.get('base_height'), prop.get('min_height')
    for key, value in [('height', height), ('base_height', base), ('min_height', minimum)]:
        if key == 'height' and value is None:
            continue  # A recorded unknown height is allowed; it cannot become a 3D model.
        if not finite_number(value):
            flags.append(key + '_non_finite_or_missing')
    if finite_number(height):
        if height <= 0:
            flags.append('height_non_positive')
        if height > 1000:
            flags.append('review_height_above_1000m')
    if finite_number(minimum):
        if minimum < 0:
            flags.append('min_height_negative')
        if finite_number(height) and minimum > height:
            flags.append('min_height_above_height')
    method = prop.get('height_method', 'missing')
    if method not in ('source', 'floors', 'ghsl_cell_average_2018', 'unknown'):
        flags.append('height_method_unrecognized')
    return {'feature_id': feature_id if isinstance(feature_id, str) and feature_id else None,
            'source_record_id': source_id if isinstance(source_id, str) and source_id else None,
            'method': method if isinstance(method, str) else 'invalid',
            'height_source': prop.get('height_source') if isinstance(prop.get('height_source'), str) else 'missing',
            'height': height, 'base_height': base, 'min_height': minimum, 'flags': flags,
            'model_candidate': finite_number(height) and height > 0,
            'unknown_candidate': height is None or (finite_number(height) and height == 0),
            'quarantined': any(flag != 'review_height_above_1000m' for flag in flags)}


def statistics():
    return {key: {'finite_count': 0, 'missing_count': 0, 'invalid_count': 0, 'min': None, 'max': None}
            for key in ('height', 'base_height', 'min_height')}


def add_statistics(stats, item):
    for key, result in stats.items():
        value = item[key]
        if finite_number(value):
            result['finite_count'] += 1
            result['min'] = value if result['min'] is None else min(result['min'], value)
            result['max'] = value if result['max'] is None else max(result['max'], value)
        elif value is None:
            result['missing_count'] += 1
        else:
            result['invalid_count'] += 1


def public_check(root, asset, expected_source_sha=None):
    if asset is None:
        return {'exists': False, 'reported_count': 0, 'valid': True}
    result = {'exists': True, 'id': asset['id'], 'reported_count': asset.get('count'), 'valid': False}
    if not isinstance(asset.get('count'), int) or isinstance(asset.get('count'), bool) or asset['count'] < 0:
        result.update(reported_count=0, error='invalid_public_count')
        return result
    try:
        path = input_path(root, asset)
        if sha256(path) != asset['sha256']:
            raise ValueError('Public asset SHA-256 mismatch')
        if asset['format'] == '3d-tiles':
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Tileset manifest exceeds bounded audit size')
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != asset['sha256']:
                raise ValueError('Public manifest changed during audit')
            payload = json.loads(data)
            result['normalized_source_matches'] = payload.get('extras', {}).get('source_sha256') == expected_source_sha
            result['valid'] = result['normalized_source_matches']
        elif asset['format'] == 'geojson':
            with path.open('rb') as stream:
                cursor = JsonCursor(stream)
                result['actual_feature_rows'] = sum(1 for _ in cursor.features())
            result['valid'] = (cursor.digest.hexdigest() == asset['sha256']
                               and result['actual_feature_rows'] == asset.get('count'))
        else:
            raise ValueError('Unexpected public building format')
    except (OSError, ValueError, KeyError, TypeError):
        result['error'] = 'missing_invalid_or_changed_public_artifact'
    return result


def scan_normalized(root, asset, ledger, public, sample_limit=20):
    parent = asset['id'].removeprefix('normalized-buildings-')
    result = {'parent': parent, 'normalized_asset_id': asset['id'], 'input_sha256': asset.get('sha256'),
              'state': 'quarantined', 'reported_normalized_rows': asset.get('count')}
    counts = Counter(); methods = Counter(); flags = Counter(); stats = statistics(); by_source = {}
    samples = []; buffer = []
    ledger.execute('SAVEPOINT one_asset')
    try:
        if not asset.get('_private'):
            raise ValueError('Normalized source must be a private catalog artifact')
        path = input_path(root, asset, private=True)
        if sha256(path) != asset['sha256']:
            raise ValueError('Normalized source SHA-256 mismatch')
        with path.open('rb') as stream:
            cursor = JsonCursor(stream)
            for ordinal, feature in enumerate(cursor.features()):
                item = inspect_feature(feature)
                counts['normalized_rows'] += 1
                counts['model_candidates'] += item['model_candidate']
                counts['unknown_candidates'] += item['unknown_candidate']
                counts['quarantined_rows'] += item['quarantined']
                methods[item['method']] += 1
                flags.update(item['flags'])
                add_statistics(stats, item)
                key = item['method'] + '/' + item['height_source']
                source = by_source.setdefault(key, {'rows': 0, 'statistics': statistics(), 'flags': Counter(), 'samples': [], 'high_height_review_samples': []})
                source['rows'] += 1; source['flags'].update(item['flags']); add_statistics(source['statistics'], item)
                sample = {'feature_id': item['feature_id'], 'source_record_id': item['source_record_id'],
                          'flags': item['flags'], **{k: item[k] if finite_number(item[k]) else None
                                                   for k in ('height', 'base_height', 'min_height')}}
                if item['flags'] and len(samples) < sample_limit:
                    samples.append(sample)
                if item['flags'] and len(source['samples']) < sample_limit:
                    source['samples'].append(sample)
                if 'review_height_above_1000m' in item['flags'] and len(source['high_height_review_samples']) < sample_limit:
                    source['high_height_review_samples'].append(sample)
                buffer.append((parent, ordinal, item['feature_id'], item['source_record_id'], item['method'],
                               json.dumps(item['flags']), int(item['quarantined'])))
                if len(buffer) >= 1000:
                    ledger.executemany('INSERT INTO records VALUES(?,?,?,?,?,?,?)', buffer); buffer.clear()
            if cursor.digest.hexdigest() != asset['sha256']:
                raise ValueError('Normalized source changed during audit')
        if buffer:
            ledger.executemany('INSERT INTO records VALUES(?,?,?,?,?,?,?)', buffer)
        ledger.execute('RELEASE one_asset')
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        ledger.execute('ROLLBACK TO one_asset'); ledger.execute('RELEASE one_asset')
        # Never include the private path, raw feature properties, or exception messages.
        result['error_type'] = type(error).__name__
        result['reason'] = 'normalized_source_missing_hash_mismatch_or_invalid_geojson'
        return result
    model = public_check(root, public.get('buildings-' + parent), asset['sha256'])
    unknown = public_check(root, public.get('unknown-buildings-' + parent))
    modeled_count, unknown_count = model['reported_count'], unknown['reported_count']
    reconciled = (model['valid'] and unknown['valid'] and counts['normalized_rows'] == asset.get('count')
                  and counts['normalized_rows'] == modeled_count + unknown_count
                  and counts['model_candidates'] == modeled_count and counts['unknown_candidates'] == unknown_count)
    result.update(state='scanned', counts=dict(counts), height_methods=dict(methods), flags=dict(flags),
                  height_statistics=stats, by_height_source=by_source, issue_samples=samples,
                  public_models=model, public_unknown=unknown, counts_reconcile=reconciled)
    return result


def duplicate_summary(ledger, column, sample_limit):
    if column not in ('feature_id', 'source_record_id'):
        raise ValueError('Unsupported identity column')
    query = f'SELECT {column} identity,COUNT(*) n,COUNT(DISTINCT parent) parents FROM records WHERE {column} IS NOT NULL GROUP BY {column} HAVING COUNT(*)>1'
    count, excess, cross_parent = ledger.execute(
        f'SELECT COUNT(*),COALESCE(SUM(n-1),0),COALESCE(SUM(parents>1),0) FROM ({query})').fetchone()
    samples = []
    for identity, occurrences, parents in ledger.execute(query + ' ORDER BY identity LIMIT ?', (sample_limit,)):
        examples = [r[0] for r in ledger.execute(f'SELECT DISTINCT parent FROM records WHERE {column}=? ORDER BY parent LIMIT 5', (identity,))]
        samples.append({'id': identity, 'occurrences': occurrences, 'parent_count': parents, 'parent_samples': examples})
    return {'duplicate_ids': count, 'excess_rows': excess, 'cross_parent_duplicate_ids': cross_parent,
            'samples': samples, 'sample_limit': sample_limit}


def audit(root, snapshot, ledger_path, sample_limit=20):
    if not 1 <= sample_limit <= 20:
        raise ValueError('sample_limit must be between 1 and 20')
    ledger_path = Path(ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open('xb'):
        pass  # Refuse to overwrite any existing ledger.
    normalized = sorted((a for a in snapshot['assets'] if a['id'].startswith('normalized-buildings-')), key=lambda a: a['id'])
    public = {a['id']: a for a in snapshot['assets'] if not a.get('_private') and a.get('layer') == 'buildings'}
    with sqlite3.connect(ledger_path) as ledger:
        ledger.execute('PRAGMA cache_size=-32768')
        ledger.execute('PRAGMA temp_store=FILE')
        ledger.execute('CREATE TABLE records(parent TEXT,ordinal INTEGER,feature_id TEXT,source_record_id TEXT,height_method TEXT,flags TEXT,quarantined INTEGER,PRIMARY KEY(parent,ordinal))')
        results = []
        for index, asset in enumerate(normalized):
            results.append(scan_normalized(root, asset, ledger, public, sample_limit))
            ledger.commit()
            print(json.dumps({'stage': 'building-identity-audit', 'assets_scanned': index + 1,
                              'assets_total': len(normalized), 'state': results[-1]['state']}), flush=True)
        ledger.execute('CREATE INDEX feature_ids ON records(feature_id,parent)')
        ledger.execute('CREATE INDEX source_ids ON records(source_record_id,parent)')
        duplicates = {key: duplicate_summary(ledger, key, sample_limit) for key in ('feature_id', 'source_record_id')}
        row_count, quarantined = ledger.execute('SELECT COUNT(*),COALESCE(SUM(quarantined),0) FROM records').fetchone()
        unique = {key: ledger.execute(f'SELECT COUNT(DISTINCT {key}) FROM records').fetchone()[0]
                  for key in ('feature_id', 'source_record_id')}
    scanned = [r for r in results if r['state'] == 'scanned']
    normalized_parents = {r['parent'] for r in results}
    orphan_public = [a['id'] for a in public.values() if a['id'].removeprefix('unknown-').removeprefix('buildings-') not in normalized_parents]
    methods = Counter(); flags = Counter(); height_sources = {}
    for item in scanned:
        methods.update(item['height_methods']); flags.update(item['flags'])
        for name, values in item['by_height_source'].items():
            merged = height_sources.setdefault(name, {'rows': 0, 'statistics': statistics(), 'flags': Counter(), 'samples': [], 'high_height_review_samples': []})
            merged['rows'] += values['rows']; merged['flags'].update(values['flags'])
            for key, stats in values['statistics'].items():
                dest = merged['statistics'][key]
                for count_key in ('finite_count', 'missing_count', 'invalid_count'):
                    dest[count_key] += stats[count_key]
                for bound, operation in [('min', min), ('max', max)]:
                    if stats[bound] is not None:
                        dest[bound] = stats[bound] if dest[bound] is None else operation(dest[bound], stats[bound])
            merged['samples'].extend(values['samples'][:max(0, sample_limit - len(merged['samples']))])
            merged['high_height_review_samples'].extend(values['high_height_review_samples'][:max(0, sample_limit - len(merged['high_height_review_samples']))])
        # Keep detailed parent counts, but sample lists are bounded globally per source.
        item.pop('by_height_source'); item.pop('issue_samples')
    return {'schema_version': 1, 'finished_at': now(), 'notice': NOTICE,
            'snapshot': {k: snapshot[k] for k in ('captured_at', 'sha256')},
            'scope': {'normalized_assets': len(normalized), 'public_building_assets': len(public),
                      'normalized_rows_scanned': row_count, 'unique_feature_ids': unique['feature_id'],
                      'unique_source_record_ids': unique['source_record_id'], 'quarantined_feature_rows': quarantined,
                      'quarantined_assets': len(results) - len(scanned), 'scan_complete': len(scanned) == len(normalized) and bool(normalized)},
            'identity_duplicates': duplicates, 'height_methods': dict(methods), 'height_flags': dict(flags),
            'height_by_source': height_sources,
            'height_review_notice': '1000m 초과는 검토 기준일 뿐, 실측 오류 또는 허위라고 단정하지 않습니다. base_height에는 해발 지형 고도가 포함될 수 있습니다.',
            'parents': results, 'orphan_public_assets': orphan_public,
            'all_parent_counts_reconcile': bool(scanned) and len(scanned) == len(normalized) and all(r['counts_reconcile'] for r in scanned) and not orphan_public,
            'ledger_file': ledger_path.name,
            'glb_feature_tables_audited': False, 'browser_rendering_verified': False,
            'real_world_completeness_verified': False,
            'model_count_basis': '공개 catalog count와 검증된 tileset 원천 해시를 대조합니다. GLB 내부 feature table의 전수 검사는 수행하지 않습니다. unknown GeoJSON은 실제 행 수도 확인합니다.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--sample-limit', type=int, choices=range(1, 21), default=20)
    args = parser.parse_args()
    snapshot = capture_assets(args.root / '.local/catalog.sqlite')
    ledger = args.root / '.local/audit/building-identities' / f'{snapshot["sha256"][:16]}-{uuid.uuid4().hex[:8]}.sqlite'
    report = audit(args.root, snapshot, ledger, args.sample_limit)
    output = args.root / '.local/audit/building-identities.json'
    content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    temporary = output.with_suffix('.' + uuid.uuid4().hex[:8] + '.tmp')
    temporary.write_text(content, encoding='utf-8'); temporary.replace(output)
    print(json.dumps({'stage': 'building-identity-audit-complete', 'report': str(output), 'scope': report['scope'],
                      'all_parent_counts_reconcile': report['all_parent_counts_reconcile']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
