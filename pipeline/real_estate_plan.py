"""Small, offline property job plans; no key, network, SQLite or collection mode.

The public registry contains administrative code facts for planning only. It is
deliberately a different kind from the complete legal-dong collection registry.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from .real_estate import RealEstateError, _reject_links, canonical_bytes, sha256, utc_instant

DEFAULT_REGISTRY = 'config/molit-legal-region-registry.json'
REGISTRY_KIND = 'molit-legal-region-plan-registry'
SOURCE_PAGE = 'https://www.code.go.kr/stdcodesrch/codeAllDownloadL.do'
SOURCE_DOWNLOAD = 'https://www.code.go.kr/etc/codeFullDown.do?codeseId=00002'
SOURCE_TERMS = 'https://www.data.go.kr/data/15077871/openapi.do'
HISTORY_SCOPE = 'current_codes_only_pending_effective_date_crosswalk'
MAX_REGISTRY_BYTES = 256 * 1024
MAX_PLAN_BYTES = 16 * 1024**2
PLAN_RESERVE_BYTES = 64 * 1024**2
KST = timezone(timedelta(hours=9))
RIGHTS = {
    'terms_url': SOURCE_TERMS,
    'license_statement': '이용허락범위 제한 없음',
    'redistributed_scope': 'administrative_code_facts_subset',
    'source_archive_license': 'not_separately_designated_on_download_page',
    'attribution': '행정안전부 행정표준코드관리시스템 법정동코드',
}


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def _exact_keys(value, keys):
    return isinstance(value, dict) and set(value) == set(keys)


def validate_plan_registry(value):
    """Closed fields prevent an unrelated dataset or secret from becoming a plan."""
    if (not _exact_keys(value, ('schema_version', 'kind', 'planning_only', 'retrieved_at',
            'historical_coverage', 'source', 'rights', 'audit', 'regions'))
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['kind'] != REGISTRY_KIND or value['planning_only'] is not True
            or value['historical_coverage'] != HISTORY_SCOPE):
        raise RealEstateError('invalid_plan_registry')
    utc_instant(value['retrieved_at'])
    source = value['source']
    if (not _exact_keys(source, ('id', 'page_url', 'download_url', 'sha256', 'bytes',
                                'registry_sha256'))
            or source['id'] != 'mois-legal-dong-codes'
            or source['page_url'] != SOURCE_PAGE or source['download_url'] != SOURCE_DOWNLOAD
            or not _hash(source['sha256']) or not _hash(source['registry_sha256'])
            or type(source['bytes']) is not int or not 0 < source['bytes'] <= 16 * 1024**2):
        raise RealEstateError('invalid_plan_source')
    rights = value['rights']
    if (not _exact_keys(rights, (*RIGHTS, 'checked_at'))
            or any(rights[key] != expected for key, expected in RIGHTS.items())):
        raise RealEstateError('invalid_plan_rights')
    utc_instant(rights['checked_at'])
    rows = value['regions']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise RealEstateError('invalid_plan_regions')
    codes = []
    for row in rows:
        if (not _exact_keys(row, ('lawd_code', 'legal_code', 'name', 'status'))
                or not isinstance(row['lawd_code'], str)
                or not re.fullmatch(r'[0-9]{5}', row['lawd_code'])
                or row['lawd_code'] == '00000' or row['status'] != 'active'
                or row['legal_code'] != row['lawd_code'] + '00000'
                or not isinstance(row['name'], str)
                or not re.fullmatch(r'[가-힣0-9 ·ㆍ()\-]{2,120}', row['name'])
                or len(row['name'].strip()) < 2):
            raise RealEstateError('invalid_plan_region')
        codes.append(row['lawd_code'])
    if len(set(codes)) != len(codes):
        raise RealEstateError('duplicate_plan_region')
    if codes != sorted(codes):
        raise RealEstateError('unsorted_plan_regions')
    audit = value['audit']
    if (not _exact_keys(audit, ('active_regions', 'regions_sha256', 'source_rows',
                               'source_active_codes', 'source_retired_codes'))
            or any(type(audit[key]) is not int or audit[key] < 0 for key in
                   ('active_regions', 'source_rows', 'source_active_codes', 'source_retired_codes'))
            or audit['active_regions'] != len(rows)
            or audit['source_active_codes'] < len(rows)
            or audit['source_rows'] != audit['source_active_codes'] + audit['source_retired_codes']
            or audit['regions_sha256'] != sha256(canonical_bytes(rows))):
        raise RealEstateError('invalid_plan_registry_audit')
    return value


def make_plan_registry(registry, *, registry_sha256, rights_checked_at):
    """Derive only whitelisted public facts from a separately verified full registry."""
    if (registry.get('kind') != 'molit-legal-region-registry'
            or registry.get('schema_version') != 1
            or registry.get('historical_coverage') != HISTORY_SCOPE
            or sha256(canonical_bytes(registry)) != registry_sha256):
        raise RealEstateError('invalid_full_registry_reference')
    result = {
        'schema_version': 1, 'kind': REGISTRY_KIND, 'planning_only': True,
        'retrieved_at': registry['retrieved_at'], 'historical_coverage': HISTORY_SCOPE,
        'source': {**{key: registry['source'][key] for key in
            ('id', 'page_url', 'download_url', 'sha256', 'bytes')},
            'registry_sha256': registry_sha256},
        'rights': {**RIGHTS, 'checked_at': rights_checked_at},
        'regions': [{key: row[key] for key in ('lawd_code', 'legal_code', 'name', 'status')}
                    for row in registry['regions']],
        'audit': {'active_regions': registry['audit']['active_regions'],
                  'source_rows': registry['audit']['source_rows'],
                  'source_active_codes': registry['audit']['active_codes'],
                  'source_retired_codes': registry['audit']['retired_codes'],
                  'regions_sha256': sha256(canonical_bytes(registry['regions']))},
    }
    return validate_plan_registry(result)


def _relative_path(root, relative, directory):
    if (not isinstance(relative, str) or '\\' in relative or ':' in relative
            or relative.startswith('/') or any(part in ('', '.', '..') for part in relative.split('/'))):
        raise RealEstateError('invalid_plan_path')
    parts = PurePosixPath(relative).parts
    if len(parts) < 2 or parts[0] != directory or not parts[-1].endswith('.json'):
        raise RealEstateError('invalid_plan_path')
    path = root.joinpath(*parts)
    _reject_links(path)
    if not path.resolve().is_relative_to(root / directory):
        raise RealEstateError('invalid_plan_path')
    return path


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RealEstateError('duplicate_plan_json_key')
        result[key] = value
    return result


def load_plan_registry(root, relative=DEFAULT_REGISTRY):
    path = _relative_path(root, relative, 'config')
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_REGISTRY_BYTES:
        raise RealEstateError('invalid_plan_registry_size')
    with path.open('rb') as stream:
        payload = stream.read(MAX_REGISTRY_BYTES + 1)
    if len(payload) > MAX_REGISTRY_BYTES:
        raise RealEstateError('invalid_plan_registry_size')
    try:
        value = json.loads(payload, object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise RealEstateError('invalid_plan_registry_json') from None
    validate_plan_registry(value)
    return value, sha256(payload)


def build_plan(registry, *, registry_sha256, as_of, months=61):
    validate_plan_registry(registry)
    if not _hash(registry_sha256):
        raise RealEstateError('invalid_plan_registry_reference')
    if type(months) is not int or not 1 <= months <= 61:
        raise RealEstateError('invalid_month_count')
    stamp = utc_instant(as_of).astimezone(KST)
    current = stamp.year * 12 + stamp.month - 1
    if current - months + 1 < 12:
        raise RealEstateError('invalid_plan_window')
    sequence = [f'{(current-i)//12:04d}{(current-i)%12+1:02d}' for i in range(months)]
    sequence = [sequence[1], sequence[0], *sequence[2:]] if months > 1 else sequence
    jobs = [{'trade_type': trade, 'lawd_code': row['lawd_code'], 'deal_month': month}
            for month in sequence for row in registry['regions'] for trade in ('sale', 'rent')]
    return {
        'schema_version': 1, 'kind': 'real-estate-offline-job-plan', 'as_of': as_of,
        'timezone': 'Asia/Seoul', 'registry_sha256': registry_sha256,
        'source_archive_sha256': registry['source']['sha256'],
        'regions_sha256': registry['audit']['regions_sha256'],
        'historical_coverage': HISTORY_SCOPE,
        'month_order': 'latest_completed_then_current_then_older', 'months': sequence,
        'region_count': len(registry['regions']), 'job_count': len(jobs),
        'job_status': 'planned_not_requested', 'source_calls': 0, 'reserved_calls': 0,
        'is_collection_checkpoint': False, 'data_acquired': False,
        'jobs': jobs,
    }


def write_plan(*, repo_root, registry_path=DEFAULT_REGISTRY,
               output='.local/property-plan-ci/plan.json', as_of, months=61):
    root = Path(repo_root).absolute()
    _reject_links(root)
    root = root.resolve()
    registry, registry_digest = load_plan_registry(root, registry_path)
    plan = build_plan(registry, registry_sha256=registry_digest, as_of=as_of, months=months)
    payload = canonical_bytes(plan)
    if len(payload) > MAX_PLAN_BYTES:
        raise RealEstateError('plan_size_limit')
    target = _relative_path(root, output, '.local')
    if target.exists():
        raise RealEstateError('plan_output_exists')
    # Only this small offline artifact uses this reserve. Collector's independent
    # 35 GiB start / 30 GiB ongoing data reserve remains unchanged.
    if shutil.disk_usage(root).free < PLAN_RESERVE_BYTES + len(payload):
        raise RealEstateError('plan_disk_reserve')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.pending-plan-', dir=target.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)  # Atomic create, never replace a previous plan.
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return {'status': 'planned', 'planning_only': True, 'regions': plan['region_count'],
            'months': len(plan['months']), 'jobs': plan['job_count'], 'source_calls': 0,
            'reserved_calls': 0, 'bytes': len(payload), 'sha256': sha256(payload),
            'registry_sha256': registry_digest}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path.cwd())
    parser.add_argument('--regions', default=DEFAULT_REGISTRY)
    parser.add_argument('--output', default='.local/property-plan-ci/plan.json')
    parser.add_argument('--months', type=int, default=61)
    parser.add_argument('--as-of', default=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    args = parser.parse_args(argv)
    try:
        result = write_plan(repo_root=args.repo_root, registry_path=args.regions,
                            output=args.output, as_of=args.as_of, months=args.months)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, 'real_estate_plan: '
                    + (error.code if isinstance(error, RealEstateError) else 'invalid_input') + '\n')


if __name__ == '__main__':
    main()
