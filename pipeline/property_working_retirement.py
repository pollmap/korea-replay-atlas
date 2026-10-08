"""Opt-in, bounded retirement after a verified private backup.

Default deployments never delete plaintext automatically. The operator enables
this only after both reader builds and a first manual migration are verified.
This hook cannot publish a release or call the transaction provider.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import urlopen

from .real_estate import RealEstateError, canonical_bytes, sha256, _reject_links
from .real_estate_local_archive_set import LocalArchiveSet
from .real_estate_manifest import validate_object
from .real_estate_working_store import INDEX, READER_CONTRACT

ENABLE = 'KOREA_REPLAY_AUTO_RETIRE_WORKING'
LIMIT = 'KOREA_REPLAY_RETIRE_MAX_FILES'
TIMEOUT = 'KOREA_REPLAY_RETIRE_MAX_SECONDS'
READER_MARKER = 'working-store-readers.json'
STATE = 'working-store-retirement.json'
API_PROBE = 'http://api:8330/api/v1/property/working-store-reader'
MAX_MESSAGE = 16 * 1024


def _read(path):
    _reject_links(path)
    if not path.is_file():
        return None
    if not 0 < path.stat().st_size <= MAX_MESSAGE:
        raise RealEstateError('working_retirement_state_limit')
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise RealEstateError('working_retirement_state_invalid')
    return value


def _save(path, value):
    _reject_links(path)
    payload = canonical_bytes(value)
    if path.is_file() and path.read_bytes() == payload:
        return
    next_path = path.with_suffix('.next'); _reject_links(next_path)
    with next_path.open('wb') as handle:
        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
    os.replace(next_path, path)


def _bounded_setting(name, fallback, low, high):
    text = os.environ.get(name, str(fallback))
    if not re.fullmatch('[0-9]{1,5}', text) or not low <= int(text) <= high:
        raise RealEstateError('working_retirement_budget')
    return int(text)


def _acknowledge(marker):
    with urlopen(API_PROBE, timeout=5) as response:
        body = response.read(MAX_MESSAGE + 1)
    if len(body) > MAX_MESSAGE:
        raise RealEstateError('working_retirement_reader_ack')
    value = json.loads(body)
    if (not isinstance(value, dict) or value.get('ready') is not True
            or value.get('read_only_index') is not True
            or value.get('reader_contract') != READER_CONTRACT
            or value.get('build') != marker['api_build']
            or not re.fullmatch('[a-f0-9]{64}', str(value.get('probe_sha256', '')))):
        raise RealEstateError('working_retirement_reader_ack')


def _execute(root, backups, head, limit, timeout):
    command = [sys.executable, '-m', 'pipeline.real_estate_working_store',
               '--root', str(root), '--store', str(backups), '--limit', str(limit),
               '--retire-plaintext', '--readers-deployed',
               '--expected-head-sha', head['sha256'], '--expected-head-bytes', str(head['bytes'])]
    # A child gives the *whole operation* a wall-clock bound, including loading
    # manifests, SQLite backup and fsync. The closed-index protocol survives kill.
    process = subprocess.Popen(command, cwd=Path(__file__).parent.parent,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.communicate(timeout=5)
        raise RealEstateError('working_retirement_timeout') from None
    if process.returncode or len(output) > MAX_MESSAGE:
        raise RealEstateError('working_retirement_child_failed')
    value = json.loads(output)
    if (not isinstance(value, dict) or value.get('head') != head
            or value.get('source_calls') != 0 or value.get('archive_objects_written') != 0
            or value.get('ledger_changed') is not False
            or type(value.get('retired')) is not int or not 0 <= value['retired'] <= limit
            or type(value.get('scan_complete')) is not bool):
        raise RealEstateError('working_retirement_result_invalid')
    return value


def after_backup(root, backups, recovery, *, progress=None):
    """Called outside collect_once's bulk lock; failure never latches collection."""
    if os.environ.get(ENABLE) != '1':
        return {'state': 'disabled', 'retired': 0, 'source_calls': 0}
    root = Path(root).absolute(); backups = Path(backups).absolute()
    data = root.parent
    started = False
    try:
        _reject_links(root); _reject_links(backups)
        if not backups.is_dir():
            raise RealEstateError('working_store_archive_missing')
        if not (root / INDEX).is_file():
            return {'state': 'needs_manual_migration', 'retired': 0, 'source_calls': 0}
        marker = _read(data / READER_MARKER)
        if (not marker or marker.get('schema_version') != 1
                or marker.get('reader_contract') != READER_CONTRACT
                or not re.fullmatch('[a-f0-9]{7,40}', str(marker.get('api_build', '')))
                or not re.fullmatch('[a-f0-9]{7,40}', str(marker.get('collector_build', '')))
                or marker.get('collector_build') != os.environ.get('APP_BUILD')):
            raise RealEstateError('working_retirement_reader_marker')
        if not isinstance(recovery, dict) or recovery.get('status') not in ('verified', 'unchanged'):
            return {'state': 'awaiting_verified_backup', 'retired': 0, 'source_calls': 0}
        head = validate_object(recovery.get('head'))
        # Merely passing an old successful receipt cannot authorize a newer head.
        store = LocalArchiveSet(backups)
        if store.head() != head:
            raise RealEstateError('working_store_head_changed')
        identity = {'head': head, 'reader_marker_sha256': sha256(canonical_bytes(marker)),
                    'reader_contract': READER_CONTRACT}
        previous = _read(data / STATE)
        if (previous and all(previous.get(key) == value for key, value in identity.items())
                and previous.get('complete') is True):
            return {'state': 'unchanged', 'retired': 0, 'source_calls': 0, 'head': head}
        limit = _bounded_setting(LIMIT, 2000, 1, 5000)
        timeout = _bounded_setting(TIMEOUT, 120, 10, 300)
        _acknowledge(marker)
        if progress:
            progress({'phase': 'working-retirement', 'max_files': limit, 'max_seconds': timeout})
        started = True
        result = _execute(root, backups, head, limit, timeout)
        state = {**identity, 'complete': result['scan_complete'],
                 'state': 'complete' if result['scan_complete'] else 'batch_complete',
                 'retired': result['retired'], 'retired_logical_bytes': result['retired_logical_bytes'],
                 'source_calls': 0}
        _save(data / STATE, state)
        return state
    except Exception as error:
        state = {'state': 'deferred', 'source_calls': 0,
                 'retired': None if started else 0,
                 'error_code': error.code if isinstance(error, RealEstateError) else 'working_retirement_failed'}
        # Storage cleanup cannot turn a successful collection into a hold. Error
        # text, paths, request URLs and inherited environment are never logged.
        try:
            _save(data / STATE, state)
        except Exception:
            pass
        return state
