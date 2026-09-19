"""Bounded Overture part acquisition and a private, source-preserving proposal.

This module never publishes, rewrites parent footprints, or regenerates tiles.
Range checkpoints retain the original remote bytes; interrupted runs can resume.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import ctypes
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import time
from urllib.parse import urlsplit

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests
import shapely
from shapely.geometry import box, mapping, shape
from shapely.ops import transform, unary_union

from .building_identity_audit import JsonCursor
from .building_parts import METRIC, _geometry, audit_rows
from .core import LOCAL, RELEASE, ROOT, REGIONS, digest, now
from .height_quality import evaluate_feature
from .retile import glb_strings

VERSION = 'national-building-parts-candidate-1'
BBOX = REGIONS['korea']
MIB = 1024**2
NETWORK_LIMIT = 64 * MIB
MEMORY_LIMIT = 512 * MIB
STORAGE_LIMIT = 256 * MIB
ROW_LIMIT = 100_000
DISK_RESERVE = 30 * 1024**3
STAC_URL = f'https://stac.overturemaps.org/{RELEASE}/collections.parquet'
PUBLIC_CATALOG_URL = os.environ.get('KOREA_REPLAY_CATALOG_URL', 'https://korea-replay.pages.dev/data/catalog.json')
PUBLIC_RELEASE = os.environ.get('KOREA_REPLAY_RELEASE', 'pub-b71d244ced0bff39')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def memory_usage():
    """Current and process peak RSS, without adding a runtime dependency."""
    if os.name == 'nt':
        class Counters(ctypes.Structure):
            _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in ('peak', 'rss', 'peak_pool', 'pool', 'peak_nonpool',
                                                     'nonpool', 'pagefile', 'peak_pagefile')]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise RuntimeError('Cannot enforce process memory budget')
        return counters.rss, counters.peak
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if __import__('sys').platform != 'darwin':
        peak *= 1024
    return peak, peak


class Budget:
    def __init__(self, folder, *, network_limit=NETWORK_LIMIT, storage_limit=STORAGE_LIMIT,
                 memory_limit=MEMORY_LIMIT, disk_reserve=DISK_RESERVE):
        self.folder = Path(folder).resolve()
        self.network_limit, self.storage_limit = network_limit, storage_limit
        self.memory_limit, self.disk_reserve = memory_limit, disk_reserve
        self.network_bytes = 0
        self.peak_memory = 0
        self.folder.mkdir(parents=True, exist_ok=True)
        self.ledger = self.folder / 'requests.jsonl'
        if self.ledger.exists():
            for line in self.ledger.read_text(encoding='utf-8').splitlines():
                self.network_bytes += json.loads(line)['reserved_bytes']
        self.check()

    def check(self, *, allocation=0, write_bytes=0):
        rss, peak = memory_usage()
        self.peak_memory = max(self.peak_memory, peak)
        if max(rss + allocation, peak) > self.memory_limit:
            raise RuntimeError('512 MiB process memory budget exceeded')
        size = sum(p.stat().st_size for p in self.folder.rglob('*') if p.is_file())
        if size + write_bytes > self.storage_limit:
            raise RuntimeError('256 MiB new artifact budget exceeded')
        if shutil.disk_usage(self.folder).free - write_bytes < self.disk_reserve:
            raise RuntimeError('30 GiB free disk reserve would be crossed')
        return size

    def reserve_request(self, url, cap, headers):
        # Reserve before sending, including failed attempts; restart cannot reset quota.
        if self.network_bytes + cap > self.network_limit:
            raise RuntimeError('64 MiB cumulative network budget exceeded')
        event = encoded({'at': now(), 'url': url, 'range': headers.get('Range'), 'reserved_bytes': cap}) + b'\n'
        self.check(write_bytes=len(event))
        with self.ledger.open('ab') as stream:
            stream.write(event)
        self.network_bytes += cap

    def write_once(self, relative, data):
        path = (self.folder / relative).resolve()
        if not path.is_relative_to(self.folder):
            raise ValueError('Artifact path escaped the candidate folder')
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError('Refusing to overwrite an existing candidate artifact: ' + relative)
            return path
        self.check(write_bytes=len(data))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
        return path

    def json(self, relative, value):
        return self.write_once(relative, encoded(value))


class PublicReader:
    def __init__(self, budget, session=None):
        self.budget = budget
        self.session = session or requests.Session()
        self.actual_bytes = 0

    def get(self, url, cap, *, span=None, total=None, etag=None):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Only credential-free official HTTPS URLs are accepted')
        if (parsed.hostname not in ('stac.overturemaps.org',
                                    'overturemaps-us-west-2.s3.us-west-2.amazonaws.com')
                and url != PUBLIC_CATALOG_URL):
            raise ValueError('Unexpected public data host')
        headers = {'Accept-Encoding': 'identity'}
        if etag:
            headers['If-Match'] = etag
        if span:
            start, end = span
            if start < 0 or end < start or end - start + 1 != cap:
                raise ValueError('Invalid exact byte range')
            headers['Range'] = f'bytes={start}-{end}'
        self.budget.reserve_request(url, cap, headers)
        with self.session.get(url, headers=headers, stream=True, allow_redirects=False, timeout=(10, 30)) as response:
            expected_status = 206 if span else 200
            if response.status_code != expected_status:
                raise RuntimeError(f'Unexpected public response {response.status_code}; body not accepted')
            if response.headers.get('Content-Encoding', 'identity') not in ('', 'identity'):
                raise ValueError('Compressed HTTP transfer invalidates byte range accounting')
            if span and response.headers.get('Content-Range') != f'bytes {start}-{end}/{total}':
                raise ValueError('Content-Range does not match requested source interval')
            if etag and response.headers.get('ETag') != etag:
                raise ValueError('Source object changed after the pinned HEAD response')
            if int(response.headers.get('Content-Length', 0)) > cap:
                raise ValueError('Response exceeds requested byte cap')
            result = bytearray(); deadline = time.monotonic() + 90
            for chunk in response.iter_content(65536):
                self.actual_bytes += len(chunk)
                if len(result) + len(chunk) > cap or time.monotonic() > deadline:
                    raise RuntimeError('Public response size/time cap exceeded')
                result.extend(chunk)
                self.budget.check(allocation=2 * len(chunk))
            if span and len(result) != cap:
                raise ValueError('Truncated source byte range')
            return bytes(result)

    def head_size(self, url):
        # The fixed URL was obtained from and validated against the release STAC.
        self.budget.reserve_request(url, 0, {})
        with self.session.head(url, allow_redirects=False, timeout=(10, 30)) as response:
            if response.status_code != 200 or not response.headers.get('Content-Length', '').isdigit():
                raise RuntimeError('Public object size is unavailable')
            return int(response.headers['Content-Length']), response.headers.get('ETag')


class SparseFile(io.RawIOBase):
    """Read only explicitly acquired source spans; never trigger hidden I/O."""
    def __init__(self, size, segments):
        super().__init__(); self.size = size; self.segments = sorted(segments); self.position = 0

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.position

    def seek(self, offset, whence=0):
        value = offset + (self.position if whence == 1 else self.size if whence == 2 else 0)
        if whence not in (0, 1, 2) or value < 0:
            raise ValueError('Invalid source seek')
        self.position = value
        return value

    def read(self, count=-1):
        count = min(self.size - self.position, count if count >= 0 else self.size)
        if count <= 0: return b''
        for start, data in self.segments:
            if start <= self.position and self.position + count <= start + len(data):
                offset = self.position - start; self.position += count
                return data[offset:offset + count]
        raise ValueError('Unacquired source span requested; full-file fallback prohibited')


def overlaps(left, right):
    return left[0] < right[2] and left[2] > right[0] and left[1] < right[3] and left[3] > right[1]


def row_group_plan(metadata, bbox):
    result = []
    for index in range(metadata.num_row_groups):
        group = metadata.row_group(index)
        cols = [group.column(j) for j in range(group.num_columns)]
        stats = {c.path_in_schema: c.statistics for c in cols}
        bounds = []
        for key, mode in [('xmin', 'min'), ('ymin', 'min'), ('xmax', 'max'), ('ymax', 'max')]:
            stat = stats.get('bbox.' + key)
            if stat is None or not stat.has_min_max:
                raise ValueError('Missing bounding-box statistics; broad source fallback prohibited')
            bounds.append(getattr(stat, mode))
        if not overlaps(bounds, bbox): continue
        starts = [c.dictionary_page_offset if c.has_dictionary_page else c.data_page_offset for c in cols]
        start = min(starts); end = max(a + c.total_compressed_size for a, c in zip(starts, cols))
        if start < 4 or end <= start: raise ValueError('Invalid Parquet column extent')
        result.append({'index': index, 'rows': group.num_rows, 'start': start, 'end': end - 1,
                       'bytes': end - start, 'uncompressed_bytes': group.total_byte_size, 'bbox': bounds})
    if sum(r['rows'] for r in result) > ROW_LIMIT:
        raise ValueError('Candidate row-group upper bound exceeds 100,000 rows')
    return result


def load_checkpoint(budget, relative, sha=None):
    path = budget.folder / relative
    if not path.exists(): return None
    if sha and digest(path) != sha: raise ValueError('Stored source checkpoint hash mismatch')
    return path.read_bytes()


def acquire(budget, reader):
    plan_path = budget.folder / 'source-plan.json'
    if plan_path.exists():
        plan = json.loads(plan_path.read_bytes())
        if plan['version'] != VERSION or plan['dataset_version'] != RELEASE or plan['bbox'] != list(BBOX):
            raise ValueError('Checkpoint policy/release/bbox differs')
        footer = load_checkpoint(budget, 'raw/footer.bin', plan['footer_sha256'])
        tail = load_checkpoint(budget, 'raw/tail.bin', plan['tail_sha256'])
    else:
        stac = reader.get(STAC_URL, 2 * MIB)
        table = pq.read_table(io.BytesIO(stac))
        selected = table.filter((pc.field('collection') == 'building_part') & (pc.field('type') == 'Feature')
                                & (pc.field('bbox', 'xmin') < BBOX[2]) & (pc.field('bbox', 'xmax') > BBOX[0])
                                & (pc.field('bbox', 'ymin') < BBOX[3]) & (pc.field('bbox', 'ymax') > BBOX[1]))
        if selected.num_rows != 1:
            raise ValueError('This pinned release requires exactly one source object; review changed layout')
        source = selected.select(['assets']).to_pylist()[0]['assets']['aws']['href']
        prefix = f'https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/release/{RELEASE}/theme=buildings/type=building_part/'
        if not source.startswith(prefix) or not source.endswith('.parquet'):
            raise ValueError('Unexpected release source URL')
        size, etag = reader.head_size(source)
        tail = reader.get(source, 8, span=(size - 8, size - 1), total=size, etag=etag)
        footer_size, magic = struct.unpack('<I4s', tail)
        if magic != b'PAR1' or not 0 < footer_size <= 8 * MIB or size <= footer_size + 12:
            raise ValueError('Invalid bounded Parquet footer')
        footer = reader.get(source, footer_size, span=(size - footer_size - 8, size - 9), total=size, etag=etag)
        metadata = pq.ParquetFile(io.BytesIO(b'PAR1' + footer + tail)).metadata
        groups = row_group_plan(metadata, BBOX)
        if sum(g['bytes'] for g in groups) + budget.network_bytes > budget.network_limit:
            raise ValueError('Source row groups exceed the remaining transfer budget')
        plan = {'version': VERSION, 'dataset_version': RELEASE, 'bbox': list(BBOX), 'source_url': source,
                'source_bytes': size, 'etag': etag, 'global_rows': metadata.num_rows,
                'row_groups': groups, 'stac_sha256': hashlib.sha256(stac).hexdigest(),
                'footer_sha256': hashlib.sha256(footer).hexdigest(), 'tail_sha256': hashlib.sha256(tail).hexdigest(),
                'retrieved_at': now()}
        budget.write_once('raw/collections.parquet', stac)
        budget.write_once('raw/footer.bin', footer); budget.write_once('raw/tail.bin', tail)
        budget.json('source-plan.json', plan)
    metadata = pq.ParquetFile(io.BytesIO(b'PAR1' + footer + tail)).metadata
    parts = []; count = 0
    for group in plan['row_groups']:
        index = group['index']; receipt_path = budget.folder / f'raw/group-{index}.json'
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_bytes())
            payload = load_checkpoint(budget, f'raw/group-{index}.bin', receipt['source_sha256'])
        else:
            budget.check(allocation=group['bytes'] + 8 * group['uncompressed_bytes'])
            payload = reader.get(plan['source_url'], group['bytes'], span=(group['start'], group['end']),
                                 total=plan['source_bytes'], etag=plan.get('etag'))
            budget.write_once(f'raw/group-{index}.bin', payload)
            receipt = {'index': index, 'rows_upper_bound': group['rows'], 'source_sha256': hashlib.sha256(payload).hexdigest(),
                       'bytes': len(payload), 'start': group['start'], 'end': group['end']}
            budget.json(f'raw/group-{index}.json', receipt)
        sparse = SparseFile(plan['source_bytes'], [(0, b'PAR1'), (group['start'], payload),
                            (plan['source_bytes'] - len(footer) - 8, footer + tail)])
        parquet = pq.ParquetFile(pa.PythonFile(sparse), metadata=metadata, pre_buffer=False)
        selected_batches = []
        for batch in parquet.iter_batches(batch_size=1024, row_groups=[index], use_threads=False):
            budget.check(allocation=4 * batch.nbytes)
            table = pa.Table.from_batches([batch])
            selected = table.filter((pc.field('bbox', 'xmin') < BBOX[2]) & (pc.field('bbox', 'xmax') > BBOX[0])
                                    & (pc.field('bbox', 'ymin') < BBOX[3]) & (pc.field('bbox', 'ymax') > BBOX[1]))
            if selected.num_rows:
                parts.extend(selected.to_pylist()); selected_batches.append(selected)
                count += selected.num_rows
                if count > ROW_LIMIT: raise RuntimeError('Filtered records exceed row cap')
        parquet.close()
        filtered = pa.concat_tables(selected_batches) if selected_batches else pa.Table.from_batches([], schema=metadata.schema.to_arrow_schema())
        buf = io.BytesIO(); pq.write_table(filtered, buf, compression='zstd')
        budget.write_once(f'raw/korea-group-{index}.parquet', buf.getvalue())
        print(json.dumps({'stage': 'national-parts-source', 'row_group': index, 'korea_rows': filtered.num_rows,
                          'total_rows': count, 'network_reserved_bytes': budget.network_bytes}), flush=True)
        budget.check()
    return parts, plan


def source_assets(root=ROOT):
    with sqlite3.connect((root / '.local/catalog.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as db:
        return [json.loads(row[0]) for row in db.execute("SELECT payload FROM assets WHERE id LIKE 'normalized-buildings-%' ORDER BY id")]


def resolve_parents(parts, budget, root=ROOT):
    """Read intersecting source cells sequentially; retain requested IDs only."""
    wanted = {r.get('building_id') for r in parts if isinstance(r.get('building_id'), str)}
    bounds = [(r['bbox']['xmin'], r['bbox']['ymin'], r['bbox']['xmax'], r['bbox']['ymax']) for r in parts]
    assets = [a for a in source_assets(root) if any(overlaps(a['bbox'], b) for b in bounds)]
    normalized = defaultdict(list); raw = defaultdict(list); receipts = []
    for i, asset in enumerate(assets):
        path = Path(asset['path']).resolve()
        if not path.is_relative_to((root / '.local/silver/buildings' / RELEASE).resolve()):
            raise ValueError('Unexpected normalized source path/release')
        count = 0; matched = []
        with path.open('rb') as stream:
            cursor = JsonCursor(stream)
            for feature in cursor.features():
                count += 1
                if feature.get('id') in wanted:
                    normalized[feature['id']].append({'feature': feature, 'asset_id': asset['id'], 'source_sha256': asset['sha256']})
                    matched.append(feature['id'])
                if count % 1024 == 0: budget.check()
            actual_sha = cursor.digest.hexdigest()
        if actual_sha != asset['sha256'] or count != asset['count']:
            raise ValueError('Normalized source changed after its catalog snapshot')
        region = asset['id'].removeprefix('normalized-buildings-')
        raw_path = root / '.local/raw/overture' / RELEASE / (region + '-building.parquet')
        raw_meta = raw_path.with_suffix('.meta.json')
        raw_sha = None
        if raw_path.is_file() and raw_meta.is_file():
            expected = json.loads(raw_meta.read_bytes())
            raw_sha = digest(raw_path)
            if raw_sha != expected.get('sha256') or expected.get('dataset_version', RELEASE) != RELEASE:
                raise ValueError('Raw parent source hash/release mismatch')
            for batch in pq.ParquetFile(raw_path).iter_batches(batch_size=1024, use_threads=False):
                ids = batch.column(batch.schema.get_field_index('id'))
                mask = pc.is_in(ids, value_set=pa.array(sorted(wanted), type=pa.string()))
                for row in pa.Table.from_batches([batch]).filter(mask).to_pylist():
                    if not any(item['geometry'] == row['geometry'] and item == row for item in raw[row['id']]):
                        raw[row['id']].append(row)
                budget.check()
        receipts.append({'asset_id': asset['id'], 'sha256': actual_sha, 'bytes': path.stat().st_size,
                         'rows': count, 'matched_ids': sorted(matched), 'raw_sha256': raw_sha})
        if matched or (i + 1) % 20 == 0:
            print(json.dumps({'stage': 'national-parts-parents', 'cells': i + 1, 'total_cells': len(assets),
                              'parents_found': len(normalized)}), flush=True)
    budget.json('parent-inputs.json', receipts)
    return normalized, raw


def group_decision(parent, parts, normalized, *, duplicate_ids=()):
    """No clipping/union output or partial parent replacement is permitted."""
    reasons = []
    if len(normalized) != 1: reasons.append('normalized_parent_missing_or_ambiguous')
    if parent is None: reasons.append('raw_parent_missing_or_ambiguous')
    if len(parts) > 512: reasons.append('parent_group_exceeds_geometry_work_budget')
    if any(p.get('id') in duplicate_ids for p in parts): reasons.append('duplicate_part_id')
    report = {'building_id': parts[0].get('building_id'), 'parts': len(parts), 'reasons': reasons,
              'published_parent_verified': False, 'candidate': False}
    if reasons: return report, []
    feature = normalized[0]['feature']; props = feature.get('properties') or {}
    if props.get('dataset_version') != RELEASE or (props.get('provenance') or {}).get('source_record_id') != parent['id']:
        reasons.append('normalized_source_identity_or_release_mismatch')
    if not parent.get('has_parts'): reasons.append('parent_has_parts_not_true')
    parent_geometry = _geometry(parent)
    if parent_geometry is None:
        reasons.append('invalid_parent_geometry')
        return report, []
    if not box(*BBOX).contains(parent_geometry): reasons.append('parent_touches_extraction_boundary')
    if not parent_geometry.equals_exact(shape(feature['geometry']), 0): reasons.append('normalized_parent_geometry_differs')
    quality = evaluate_feature(feature)
    if not quality['render_eligible']: reasons.append('parent_vertical_quality_unresolved')
    candidates, audit = audit_rows([parent], parts, BBOX)
    report['part_audit'] = audit['parts']
    if len(candidates['features']) != len(parts) or any(not p['extrusion_candidate_pending_ground'] for p in audit['parts']):
        reasons.append('not_all_parts_have_eligible_height_and_supported_roof')
    geometries = [_geometry(p) for p in parts]
    if any(g is None for g in geometries):
        reasons.append('invalid_part_geometry'); return report, []
    # Preserve source topology first. Projecting differently segmented shared edges
    # before the Boolean operation creates artificial slivers on a curved CRS.
    pg = transform(METRIC, parent_geometry); union = unary_union(geometries)
    missing = transform(METRIC, parent_geometry.difference(union)).area
    overhang = transform(METRIC, union.difference(parent_geometry)).area
    report.update({'parent_area_m2': pg.area, 'missing_area_m2': missing, 'overhang_area_m2': overhang,
                   'area_crs': 'EPSG:5186', 'topology_crs': 'EPSG:4326', 'numerical_area_tolerance_m2': 1e-6})
    if missing > 1e-6: reasons.append('parts_do_not_cover_parent')
    if overhang > 1e-6: reasons.append('parts_extend_outside_parent')
    overlaps_3d = []
    if not reasons:
        for i, a in enumerate(geometries):
            for j in range(i + 1, len(geometries)):
                area = transform(METRIC, a.intersection(geometries[j])).area
                low = max(parts[i].get('min_height') or 0, parts[j].get('min_height') or 0)
                high = min(parts[i]['height'], parts[j]['height'])
                if area > 1e-6 and high > low:
                    overlaps_3d.append({'part_ids': [parts[i]['id'], parts[j]['id']], 'area_m2': area,
                                       'overlap_height_m': high - low})
        if overlaps_3d: reasons.append('part_volumes_overlap')
    report['overlapping_volumes'] = overlaps_3d
    if reasons: return report, []
    source_by_id = {p['id']: p for p in parts}
    result = []
    for candidate in candidates['features']:
        row = source_by_id[candidate['id']]
        input_props = {**candidate['properties'], 'base_height': props['base_height'], 'height_method': 'source'}
        vertical = evaluate_feature({'id': candidate['id'], 'properties': {
            **input_props, 'min_height': 0 if row.get('min_height') is None else row['min_height'],
        }})
        if not vertical['render_eligible']:
            reasons.append('part_vertical_quality_with_parent_base_unresolved'); return report, []
        original = {k: v for k, v in row.items() if k != 'geometry'}
        result.append({**candidate, 'properties': {
            **input_props, **vertical, 'raw_min_height': row.get('min_height'),
            'render_eligible': False, 'proposal_only': True,
            'candidate_render_eligible': True, 'source_record_id': candidate['id'],
            'parent_source_record_id': parent['id'], 'source_feature_type': 'building_part',
            'ground_height_policy': 'inherit_verified_published_parent_base',
            'ground_accuracy_verified': False, 'original_properties': original,
            'roof_model': 'flat_extrusion' if row.get('roof_shape') == 'flat' else 'unspecified_roof_flat_extrusion',
        }})
    report['source_geometry_sha256'] = hashlib.sha256(encoded(feature['geometry'])).hexdigest()
    report['part_source_ids'] = [f['id'] for f in result]
    report['normalized_parent_asset'] = normalized[0]['asset_id']
    report['normalized_parent_sha256'] = normalized[0]['source_sha256']
    return report, result


def public_proof(budget, reader, root=ROOT):
    """Pin small public/local references; never trust a remembered deployment ID."""
    cached = budget.folder / 'references/public-catalog.json'
    content = cached.read_bytes() if cached.exists() else reader.get(PUBLIC_CATALOG_URL, 2 * MIB)
    budget.write_once('references/public-catalog.json', content)
    public = json.loads(content)
    local_content = (root / 'public/data/catalog.json').read_bytes()
    local = json.loads(local_content)
    fields = ('id', 'url', 'sha256', 'version', 'dataset_version', 'count')
    def building(doc):
        found = [a for a in doc.get('assets', []) if a.get('id') == 'buildings-korea-retiled' and a.get('format') == '3d-tiles']
        return found[0] if len(found) == 1 else None
    remote_asset, local_asset = building(public), building(local)
    verified = bool(public.get('release_id') == PUBLIC_RELEASE and local.get('release_id') == PUBLIC_RELEASE
                    and remote_asset and local_asset and all(remote_asset.get(k) == local_asset.get(k) for k in fields))
    evidence = {'public_catalog_url': PUBLIC_CATALOG_URL, 'expected_release_id': PUBLIC_RELEASE,
                'public_release_id': public.get('release_id'), 'local_release_id': local.get('release_id'),
                'public_catalog_sha256': hashlib.sha256(content).hexdigest(),
                'local_catalog_sha256': hashlib.sha256(local_content).hexdigest(),
                'building_root': remote_asset, 'verified': False}
    tree = None
    if verified:
        url = local_asset['url']
        path = (root / 'public' / url.lstrip('/')).resolve()
        if not url.startswith('/data/') or not path.is_relative_to((root / 'public/data').resolve()):
            raise ValueError('Building root escaped public assets')
        payload = path.read_bytes()
        verified = hashlib.sha256(payload).hexdigest() == remote_asset['sha256']
        if verified:
            tree = json.loads(payload); evidence['local_root_sha256'] = remote_asset['sha256']
    evidence['verified'] = verified
    budget.json('references/published-parent-root.json', evidence)
    return evidence, tree


def verify_published_parents(reports, normalized, tree, evidence, budget, root=ROOT):
    candidates = {r['building_id']: r for r in reports if not r['reasons']}
    if not tree or not evidence['verified']:
        for report in candidates.values(): report['reasons'].append('published_root_reference_not_verified')
        return
    bounds = {key: shape(normalized[key][0]['feature']['geometry']).bounds for key in candidates}
    found = defaultdict(list); seen_paths = set()
    root_path = root / 'public' / evidence['building_root']['url'].lstrip('/')
    stack = [tree['root']]
    while stack:
        node = stack.pop(); stack.extend(node.get('children', []))
        if node.get('extras', {}).get('lod_role') != 'detail': continue
        region = node.get('boundingVolume', {}).get('region')
        uri = node.get('content', {}).get('uri')
        if not region or not uri or not uri.endswith('.glb'): continue
        bbox = tuple(math.degrees(x) for x in region[:4])
        wanted = {key for key, b in bounds.items() if overlaps(bbox, b)}
        if not wanted: continue
        path = (root_path.parent / uri).resolve()
        if not path.is_relative_to((root / 'public/data').resolve()):
            raise ValueError('Detail tile path escaped public assets')
        if path in seen_paths: continue
        seen_paths.add(path)
        expected = node.get('extras', {}).get('sha256')
        actual = digest(path)
        if not expected or actual != expected: raise ValueError('Selected published detail tile hash mismatch')
        identities = Counter(glb_strings(path, 'source_record_id'))
        for key in wanted:
            if identities[key]:
                found[key].append({'path': str(path.relative_to(root)).replace('\\', '/'), 'sha256': actual,
                                   'occurrences': identities[key], 'bytes': path.stat().st_size})
        budget.check()
    for key, report in candidates.items():
        report['published_tiles'] = found[key]
        report['published_parent_verified'] = sum(t['occurrences'] for t in found[key]) == 1
        if not report['published_parent_verified']: report['reasons'].append('published_parent_missing_or_duplicate')
        report['candidate'] = not report['reasons']


def prepare_candidate_mesh(features, replacement_plan):
    """Explicit private-mesh adapter: source nulls remain in raw/original fields.

    Preparing a mesh is not permission to overlay it on the existing parent.
    Publication must apply the full parent replacement plan atomically.
    """
    groups = replacement_plan.get('groups', [])
    if any(not g.get('candidate') or not g.get('published_parent_verified') for g in groups):
        raise ValueError('Only verified complete parent groups can prepare a mesh')
    expected = {g['building_id']: g['part_source_ids'] for g in groups}
    if len(expected) != len(groups) or set(replacement_plan.get('replace_parent_ids', [])) != set(expected):
        raise ValueError('Replacement parent IDs differ from verified groups')
    actual = defaultdict(list)
    for feature in features:
        props = feature.get('properties') or {}
        if not props.get('candidate_render_eligible') or props.get('source_record_id') != feature.get('id'):
            raise ValueError('Candidate source identity or render eligibility is invalid')
        actual[props.get('parent_source_record_id')].append(feature['id'])
    if set(actual) != set(expected) or any(Counter(actual[k]) != Counter(expected[k]) for k in expected):
        raise ValueError('Partial or duplicated part groups cannot prepare a mesh')
    return [{**f, 'properties': {**f['properties'], 'height': f['properties']['render_height'],
                                'min_height': f['properties']['render_min_height'], 'render_eligible': True,
                                'proposal_only': True, 'review_only': True}} for f in features]


def apply_sibling_gate(replacement_plan, global_counts, *, complete):
    """A bbox-complete footprint cannot substitute for complete source relations."""
    accepted, blocked = [], []
    for original in replacement_plan['groups']:
        group = json.loads(encoded(original))
        expected = global_counts.get(group['building_id'], 0)
        observed = len(group['part_source_ids'])
        group['global_sibling_count'] = expected
        group['observed_sibling_count'] = observed
        group['global_sibling_count_verified'] = bool(complete and expected == observed)
        if not group['global_sibling_count_verified']:
            group['candidate'] = False
            group['reasons'].append('global_sibling_count_mismatch' if complete else 'global_sibling_scan_incomplete')
            blocked.append(group)
        else:
            accepted.append(group)
    return {**replacement_plan, 'groups': accepted, 'blocked_groups': blocked,
            'replace_parent_ids': sorted(g['building_id'] for g in accepted),
            'global_sibling_scan_complete': complete,
            'render_feature_count_delta': sum(len(g['part_source_ids']) for g in accepted) - len(accepted)}


def verify_global_siblings(folder):
    """Additional approved scalar-only scan; never acquire global geometries.

    The 100k geometry/attribute record cap still applies to acquire(). This
    separate phase reads only the 4,339,150 building_id values of the pinned
    global file, retaining counts for requested parents in bounded batches.
    """
    budget = Budget(folder); reader = PublicReader(budget)
    plan = json.loads((budget.folder / 'source-plan.json').read_bytes())
    if plan['dataset_version'] != RELEASE or plan['global_rows'] != 4_339_150 or plan['bbox'] != list(BBOX):
        raise ValueError('Scalar scan only approved for this exact release and row count')
    footer = load_checkpoint(budget, 'raw/footer.bin', plan['footer_sha256'])
    tail = load_checkpoint(budget, 'raw/tail.bin', plan['tail_sha256'])
    metadata = pq.ParquetFile(io.BytesIO(b'PAR1' + footer + tail)).metadata
    if metadata.num_rows != plan['global_rows']:
        raise ValueError('Scalar source metadata row count changed')
    base_summary = json.loads((budget.folder / 'summary.json').read_bytes())
    source_reports = json.loads((budget.folder / 'review-groups.json').read_bytes())
    wanted = {r['building_id'] for r in source_reports if isinstance(r['building_id'], str)}
    wanted_array = pa.array(sorted(wanted), type=pa.string())
    slices = []
    full_groups = {g['index']: g for g in plan['row_groups']}
    for index in range(metadata.num_row_groups):
        group = metadata.row_group(index)
        column = next((group.column(j) for j in range(group.num_columns)
                       if group.column(j).path_in_schema == 'building_id'), None)
        if column is None: raise ValueError('Expected building_id column is absent')
        start = column.dictionary_page_offset if column.has_dictionary_page else column.data_page_offset
        slices.append({'index': index, 'start': start, 'bytes': column.total_compressed_size,
                       'end': start + column.total_compressed_size - 1, 'rows': group.num_rows})
    remaining = sum(s['bytes'] for s in slices if s['index'] not in full_groups
                    and not (budget.folder / f'siblings/column-{s["index"]}.json').exists())
    if remaining + budget.network_bytes > budget.network_limit:
        raise RuntimeError('Scalar-only verification exceeds remaining cumulative transfer budget')
    counts = Counter(); scanned = 0; new_verified_bytes = 0
    for item in slices:
        index = item['index']; stem = f'siblings/column-{index}'
        receipt_path = budget.folder / (stem + '.json')
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_bytes())
            if any(receipt.get(k) != item[k] for k in ('index', 'start', 'bytes', 'end', 'rows')):
                raise ValueError('Scalar checkpoint interval changed')
            payload = load_checkpoint(budget, stem + '.bin', receipt['sha256'])
        else:
            if index in full_groups:
                group = full_groups[index]
                saved = json.loads((budget.folder / f'raw/group-{index}.json').read_bytes())
                original = load_checkpoint(budget, f'raw/group-{index}.bin', saved['source_sha256'])
                offset = item['start'] - group['start']
                if offset < 0 or offset + item['bytes'] > len(original):
                    raise ValueError('Scalar source interval lies outside saved group')
                payload = original[offset:offset + item['bytes']]
                transfer_bytes = 0
            else:
                payload = reader.get(plan['source_url'], item['bytes'], span=(item['start'], item['end']),
                                     total=plan['source_bytes'], etag=plan.get('etag'))
                transfer_bytes = len(payload)
            receipt = {**item, 'sha256': hashlib.sha256(payload).hexdigest(), 'new_transfer_bytes': transfer_bytes,
                       'source_column': 'building_id', 'source_etag': plan.get('etag')}
            budget.write_once(stem + '.bin', payload); budget.json(stem + '.json', receipt)
        new_verified_bytes += receipt['new_transfer_bytes']
        sparse = SparseFile(plan['source_bytes'], [(item['start'], payload)])
        parquet = pq.ParquetFile(pa.PythonFile(sparse), metadata=metadata, pre_buffer=False)
        row_count = 0
        for batch in parquet.iter_batches(batch_size=2048, row_groups=[index], columns=['building_id'], use_threads=False):
            values = batch.column(0)
            counts.update(values.filter(pc.is_in(values, value_set=wanted_array)).to_pylist())
            row_count += batch.num_rows; budget.check(allocation=4 * batch.nbytes)
        parquet.close()
        if row_count != item['rows']: raise ValueError('Scalar row-group count mismatch')
        scanned += row_count
        if scanned > plan['global_rows']: raise ValueError('Scalar source row cap exceeded')
        if (index + 1) % 16 == 0 or index + 1 == len(slices):
            print(json.dumps({'stage': 'national-parts-global-siblings', 'groups': index + 1,
                              'total_groups': len(slices), 'scalar_rows': scanned,
                              'network_reserved_bytes': budget.network_bytes}), flush=True)
    original_plan = json.loads((budget.folder / 'replacement-plan.json').read_bytes())
    result = apply_sibling_gate(original_plan, counts, complete=scanned == plan['global_rows'])
    source_features = json.loads((budget.folder / 'candidate-parts.geojson').read_bytes())['features']
    accepted_ids = set(result['replace_parent_ids'])
    features = [{**f, 'properties': {**f['properties'], 'global_sibling_count_verified': True}}
                for f in source_features if f['properties']['parent_source_record_id'] in accepted_ids]
    prepare_candidate_mesh(features, result)
    audit = {'column': 'building_id', 'global_scalar_rows_read': scanned,
             'geometry_attribute_rows_upper_bound': sum(g['rows'] for g in plan['row_groups']),
             'bbox_geometry_attribute_rows': base_summary['source_parts'],
             'source_etag': plan.get('etag'), 'parent_counts': dict(sorted(counts.items())),
             'all_row_groups_read': len(slices), 'scan_complete': scanned == plan['global_rows'],
             'source_column_compressed_bytes': sum(s['bytes'] for s in slices),
             'new_verified_response_bytes': new_verified_bytes,
             'network_reserved_bytes_including_base': budget.network_bytes,
             'verified_response_bytes_including_base': base_summary['network_actual_bytes_this_run'] + new_verified_bytes,
             'peak_process_rss_bytes': budget.peak_memory,
             'candidate_groups_before': len(original_plan['groups']), 'candidate_groups_after': len(result['groups']),
             'blocked_groups': len(result['blocked_groups']), 'candidate_parts': len(features)}
    budget.json('verified/sibling-audit.json', audit)
    budget.json('verified/replacement-plan.json', result)
    budget.json('verified/candidate-parts.geojson', {'type': 'FeatureCollection', 'features': features})
    budget.json('verified/summary.json', {**audit, 'publication_state': 'private_candidate_only',
                                        'public_assets_changed': False,
                                        'limitation': 'Global source sibling counts are complete; source accuracy, physical roofs, and surveyed terrain are not established.'})
    print(json.dumps({k: v for k, v in audit.items() if k != 'parent_counts'}, ensure_ascii=False), flush=True)
    return audit


def apply_parent_height_gate(replacement_plan, features, parents):
    """Do not silently prefer a part height over a conflicting source parent."""
    by_parent = defaultdict(list)
    for feature in features: by_parent[feature['properties']['parent_source_record_id']].append(feature)
    accepted, blocked = [], []
    for original in replacement_plan['groups']:
        group = json.loads(encoded(original)); parent_id = group['building_id']
        reference = parents.get(parent_id)
        if reference is None:
            group['candidate'] = False; group['reasons'].append('parent_height_reference_missing')
            blocked.append(group); continue
        height = reference.get('height')
        usable = type(height) in (int, float) and math.isfinite(height) and height > 0
        highest = max(f['properties']['render_height'] for f in by_parent[parent_id])
        state = 'source_parent_absent' if height is None else 'source_parent_invalid' if not usable else (
            'source_values_equal' if math.isclose(height, highest, rel_tol=0, abs_tol=1e-6) else 'source_values_conflict')
        group.update({'parent_source_height_m': height if usable else None, 'max_part_source_height_m': highest,
                      'parent_height_consistency': state, 'parent_source_reference': reference,
                      'parent_height_numerical_tolerance_m': 1e-6})
        if state == 'source_values_conflict':
            group['candidate'] = False; group['reasons'].append('parent_and_part_source_height_conflict')
            blocked.append(group)
        else:
            accepted.append(group)
    plan = {**replacement_plan, 'groups': accepted, 'blocked_height_groups': blocked,
            'replace_parent_ids': sorted(g['building_id'] for g in accepted),
            'parent_height_review_complete': True, 'parent_height_policy': 'source-parent-top-consistency-1',
            'render_feature_count_delta': sum(len(g['part_source_ids']) for g in accepted) - len(accepted)}
    allowed = {g['building_id']: g for g in accepted}; result = []
    for feature in features:
        group = allowed.get(feature['properties']['parent_source_record_id'])
        if not group: continue
        reference = group['parent_source_reference']
        source_name = (feature['properties']['original_properties'].get('names') or {}).get('primary')
        parent_name = (reference.get('names') or {}).get('primary')
        result.append({**feature, 'properties': {**feature['properties'],
            'name': source_name or parent_name or '건물 부분',
            'display_name_source': 'part_source' if source_name else 'parent_source' if parent_name else 'generic',
            'parent_height_consistency': group['parent_height_consistency'],
            'parent_source_reference': reference}})
    prepare_candidate_mesh(result, plan)
    return plan, result


def verify_parent_heights(folder, root=ROOT):
    budget = Budget(folder)
    plan = json.loads((budget.folder / 'verified/replacement-plan.json').read_bytes())
    if not plan.get('global_sibling_scan_complete'):
        raise ValueError('Global sibling scan must finish before final height review')
    features = json.loads((budget.folder / 'verified/candidate-parts.geojson').read_bytes())['features']
    wanted = set(plan['replace_parent_ids']); parents = {}
    receipts = {r['asset_id']: r for r in json.loads((budget.folder / 'parent-inputs.json').read_bytes())}
    for asset_id in sorted({g['normalized_parent_asset'] for g in plan['groups']}):
        region = asset_id.removeprefix('normalized-buildings-')
        path = (root / '.local/raw/overture' / RELEASE / (region + '-building.parquet')).resolve()
        if not path.is_relative_to((root / '.local/raw/overture' / RELEASE).resolve()):
            raise ValueError('Raw parent reference escaped pinned release')
        if digest(path) != receipts[asset_id]['raw_sha256']:
            raise ValueError('Parent source changed after the verified link audit')
        for batch in pq.ParquetFile(path).iter_batches(batch_size=1024, columns=['id', 'height', 'names', 'sources'], use_threads=False):
            for row in batch.to_pylist():
                if row['id'] not in wanted: continue
                record = {**row, 'dataset_version': RELEASE, 'raw_parent_sha256': receipts[asset_id]['raw_sha256']}
                if row['id'] in parents and parents[row['id']] != record:
                    raise ValueError('Ambiguous original parent height records')
                parents[row['id']] = record
            budget.check()
    final, result = apply_parent_height_gate(plan, features, parents)
    summary = {'parent_height_policy': final['parent_height_policy'], 'groups_before': len(plan['groups']),
               'replacement_parent_candidates': len(final['groups']), 'replacement_part_candidates': len(result),
               'blocked_height_conflicts': len(final['blocked_height_groups']),
               'parent_height_states': dict(Counter(g['parent_height_consistency'] for g in final['groups'])),
               'public_assets_changed': False, 'publication_state': 'private_candidate_only',
               'logical_parent_count_delta': 0, 'render_feature_count_delta': final['render_feature_count_delta'],
               'peak_process_rss_bytes': budget.peak_memory,
               'limitation': 'Source-consistent part extrusions, not surveyed roofs or verified real-world height accuracy.'}
    budget.json('final/replacement-plan.json', final)
    budget.json('final/candidate-parts.geojson', {'type': 'FeatureCollection', 'features': result})
    budget.json('final/height-consistency.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def verify_candidate_meshes(folder):
    """Generate only small private review meshes for globally complete groups."""
    from .mesh import write_glb
    from .mesh_metadata_audit import audit_glb
    budget = Budget(folder)
    plan_path = budget.folder / 'final/replacement-plan.json'
    features_path = budget.folder / 'final/candidate-parts.geojson'
    plan = json.loads(plan_path.read_bytes())
    if not plan.get('global_sibling_scan_complete') or any(not g.get('global_sibling_count_verified') for g in plan['groups']):
        raise ValueError('Global sibling completeness is required for private mesh verification')
    if not plan.get('parent_height_review_complete'):
        raise ValueError('Parent source height consistency must be reviewed before meshes')
    features = json.loads(features_path.read_bytes())['features']
    prepared = prepare_candidate_mesh(features, plan)
    by_parent = defaultdict(list)
    for feature in prepared: by_parent[feature['properties']['parent_source_record_id']].append(feature)
    source = {}; wanted_ids = {f['id'] for f in features}
    for path in sorted((budget.folder / 'raw').glob('korea-group-*.parquet')):
        for batch in pq.ParquetFile(path).iter_batches(batch_size=1024, use_threads=False):
            for row in batch.to_pylist():
                if row.get('id') in wanted_ids: source[row['id']] = row
            budget.check()
    for feature in features:
        row = source.get(feature['id'])
        if row is None or encoded(feature['geometry']) != encoded(mapping(shapely.from_wkb(row['geometry']))):
            raise ValueError('Candidate changed source coordinates or identity')
        if encoded(feature['properties']['original_properties']) != encoded({k: v for k, v in row.items() if k != 'geometry'}):
            raise ValueError('Candidate dropped or changed source attributes')
    receipts = []
    for parent, group in sorted(by_parent.items()):
        name = 'final/meshes/' + hashlib.sha256(parent.encode()).hexdigest()[:24] + '.glb'
        path = budget.folder / name
        # Each parent is a small independent local frame; never combine the
        # nation into one float32 coordinate frame just for this review.
        budget.check(allocation=8 * MIB, write_bytes=4 * MIB)
        if path.exists(): raise ValueError('Review mesh already exists; preserve it and use its audit')
        path.parent.mkdir(parents=True, exist_ok=True)
        written = write_glb(group, path)
        if written is None or path.stat().st_size > 4 * MIB:
            raise ValueError('Private parent mesh exceeds the existing 4 MiB tile budget')
        audit = audit_glb(path)
        if glb_strings(path, 'source_record_id') != [f['id'] for f in group]:
            raise ValueError('GLB source identity round-trip differs')
        original_rows = [json.loads(x) for x in glb_strings(path, 'original_properties')]
        if encoded(original_rows) != encoded([f['properties']['original_properties'] for f in group]):
            raise ValueError('GLB lost source part attributes or parent relationship')
        error = written[0]['extras']['coordinate_rounding_max_m']
        if error > .005: raise ValueError('Local float32 conversion exceeded 5 mm additional error')
        receipts.append({'building_id': parent, 'path': name, 'sha256': audit['sha256'], 'bytes': path.stat().st_size,
                         'features': audit['features'], 'vertices': audit['vertices'], 'triangles': audit['triangles'],
                         'coordinate_rounding_max_m': error})
    report = {'plan_sha256': digest(plan_path), 'candidate_geojson_sha256': digest(features_path),
              'public_assets_changed': False, 'private_meshes': len(receipts), 'features': len(features),
              'raw_id_geometry_attributes_equal': True, 'glb_id_original_properties_equal': True,
              'bytes': sum(r['bytes'] for r in receipts), 'max_glb_bytes': max((r['bytes'] for r in receipts), default=0),
              'coordinate_rounding_max_m': max((r['coordinate_rounding_max_m'] for r in receipts), default=0),
              'peak_process_rss_bytes': budget.peak_memory, 'meshes': receipts,
              'limitations': ['Private diagnostic meshes have not been integrated into a public hierarchy.',
                              'Coordinate rounding is additional conversion error, not absolute location accuracy.']}
    budget.json('final/mesh-audit.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'meshes'}, ensure_ascii=False), flush=True)
    return report


def run(folder):
    budget = Budget(folder); reader = PublicReader(budget)
    parts, plan = acquire(budget, reader)
    normalized, raw = resolve_parents(parts, budget)
    groups = defaultdict(list)
    ids = Counter(p.get('id') for p in parts)
    duplicates = {key for key, count in ids.items() if not key or count > 1}
    for part in parts: groups[part.get('building_id')].append(part)
    reports = []; features_by_parent = {}
    for key, rows in sorted(groups.items(), key=lambda item: str(item[0])):
        parent = raw[key][0] if len(raw[key]) == 1 else None
        report, features = group_decision(parent, rows, normalized[key], duplicate_ids=duplicates)
        reports.append(report); features_by_parent[key] = features
        budget.check()
    evidence, tree = public_proof(budget, reader)
    verify_published_parents(reports, normalized, tree, evidence, budget)
    features = [f for r in reports if r['candidate'] for f in features_by_parent[r['building_id']]]
    replacement_ids = {r['building_id'] for r in reports if r['candidate']}
    counts = Counter(reason for r in reports for reason in r['reasons'])
    part_counts = Counter(flag for r in reports for p in r.get('part_audit', []) for flag in p['issues'])
    part_counts.update({'eligible_extrusion_pending_ground': sum(p['extrusion_candidate_pending_ground'] for r in reports for p in r.get('part_audit', []))})
    summary = {'version': VERSION, 'dataset_version': RELEASE, 'bbox': list(BBOX), 'publication_state': 'private_candidate_only',
               'source_row_group_upper_bound': sum(g['rows'] for g in plan['row_groups']),
               'source_parts': len(parts), 'linked_parent_ids': len(groups), 'duplicate_part_ids': len(duplicates),
               'normalized_parent_ids_found': sum(len(normalized[k]) == 1 for k in groups),
               'raw_parent_ids_found': sum(len(raw[k]) == 1 for k in groups),
               'replacement_parent_candidates': len(replacement_ids), 'replacement_part_candidates': len(features),
               'review_parent_groups': len(reports) - len(replacement_ids), 'group_reasons': dict(counts),
               'part_quality_counts': dict(part_counts), 'roof_shapes': dict(Counter(p.get('roof_shape') or 'unspecified' for p in parts)),
               'network_reserved_bytes': budget.network_bytes, 'network_actual_bytes_this_run': reader.actual_bytes,
               'peak_process_rss_bytes': budget.peak_memory, 'new_artifact_bytes_before_final_report': budget.check(),
               'limits': {'network_bytes': NETWORK_LIMIT, 'memory_bytes': MEMORY_LIMIT, 'new_storage_bytes': STORAGE_LIMIT,
                          'rows': ROW_LIMIT, 'free_disk_bytes': DISK_RESERVE},
               'public_parent_reference_verified': evidence['verified'],
               'limitations': [
                   'This is the pinned-release Korea bounding-box extraction, not a census of all real buildings.',
                   'All observed in-bounds siblings must pass. Incorrectly linked parts outside the extraction remain unproven.',
                   'Flat extrusion of an unspecified roof is a representation, not a measured roof model.',
                   'Terrain base is inherited from the same verified published parent; ground accuracy is not independently verified.',
                   'Candidates retain all source parts/IDs; no public parent was hidden, changed, or published.',
                   'Group predicates verify source consistency, not all real-world dimensions.']}
    budget.json('review-groups.json', reports)
    budget.json('replacement-plan.json', {'version': VERSION, 'replace_parent_ids': sorted(replacement_ids),
                                        'groups': [r for r in reports if r['candidate']],
                                        'logical_parent_count_delta': 0, 'render_feature_count_delta': len(features) - len(replacement_ids),
                                        'public_assets_changed': False})
    budget.json('candidate-parts.geojson', {'type': 'FeatureCollection', 'features': features})
    budget.json('summary.json', summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=LOCAL / 'building-parts-national' / RELEASE / VERSION)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--verify-siblings-only', action='store_true', help='Explicitly approved scalar-only global completeness check')
    mode.add_argument('--verify-heights-only', action='store_true', help='Quarantine conflicting parent/part source heights')
    mode.add_argument('--verify-mesh-only', action='store_true', help='Small private meshes after source completeness verification')
    args = parser.parse_args()
    path = args.output.resolve()
    if not path.is_relative_to((LOCAL / 'building-parts-national').resolve()):
        raise ValueError('Only a separate private national-parts candidate directory is allowed')
    if args.verify_siblings_only:
        verify_global_siblings(path)
    elif args.verify_heights_only:
        verify_parent_heights(path)
    elif args.verify_mesh_only:
        verify_candidate_meshes(path)
    else:
        run(path)


if __name__ == '__main__':
    main()
