"""Portable, hash-checked deployable software releases; never deletes any files.

Only an immutable deployment's allowlisted client/worker/config files are packed.
Raw data, local credentials, Wrangler caches and unrelated workspace files are not
traversed. Restoration requires a new directory and retains failures for inspection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import zipfile

CHUNK = 1024 * 1024
MAX_PART = 1024 * CHUNK
MAX_FILE = 24 * CHUNK
MAX_METADATA = 20 * CHUNK
MAX_ASSETS = 20000
MAX_WORKER_FILES = 100
METADATA_NAMES = ('wrangler.json', 'asset-manifest.json', 'receipt.json')
HASH = re.compile(r'[a-f0-9]{64}')


def digest(path: Path) -> str:
    regular_file(path)
    value = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(CHUNK), b''):
            value.update(chunk)
    return value.hexdigest()


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096 or '\\' in value or ':' in value:
        raise ValueError('Unsafe relative recovery path')
    parts = value.split('/')
    reserved = re.compile(r'(?i)^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)')
    if PurePosixPath(value).is_absolute() or any(
        part in ('', '.', '..') or len(part) > 255 or part.endswith((' ', '.'))
        or any(ord(char) < 32 or char in '<>"|?*' for char in part) or reserved.match(part)
        for part in parts
    ):
        raise ValueError('Unsafe relative recovery path')
    return value


def no_links(path: Path) -> None:
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ValueError('Recovery paths must not contain links or junctions')


def regular_file(path: Path):
    no_links(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Recovery inputs must be regular files')
    return info


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate recovery JSON key')
        value[key] = item
    return value


def _invalid_constant(value):
    raise ValueError('Invalid recovery JSON constant: ' + value)


def read_json(path: Path):
    if regular_file(path).st_size > MAX_METADATA:
        raise ValueError('Recovery metadata exceeds 20 MiB')
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def write_json_new(path: Path, value) -> None:
    no_links(path)
    with path.open('xb') as output:
        output.write(json_bytes(value))


def reserve(parent: Path, required: int, reserve_bytes: int) -> None:
    if type(required) is not int or type(reserve_bytes) is not int or required < 0 or reserve_bytes < 0:
        raise ValueError('Invalid disk budget')
    existing = parent
    while not existing.exists():
        existing = existing.parent
    no_links(existing)
    if shutil.disk_usage(existing).free < required + reserve_bytes:
        raise ValueError('Insufficient free space including the recovery reserve')


def valid_identity(value) -> None:
    if not isinstance(value, dict) or not isinstance(value.get('bundle_id'), str) or not isinstance(value.get('release_id'), str):
        raise ValueError('Invalid release identity')
    if not re.fullmatch(r'[a-f0-9]{16}', value['bundle_id']) or not re.fullmatch(r'pub-[a-f0-9]{16}', value['release_id']):
        raise ValueError('Invalid release identity')


def broker_deployment_contract():
    """An immutable release declares its exact, external transit dependency."""
    return {'schema_version': 1, 'live_transit_mode': 'broker',
            'broker': {'binding': 'LIVE_TRANSIT_BROKER', 'service': 'korea-replay-live-broker'}}


def validate_config(config, receipt) -> None:
    if not isinstance(config, dict) or not isinstance(receipt, dict):
        raise ValueError('Invalid static deployment configuration')
    variables = config.get('vars', {})
    assets = config.get('assets', {})
    if not isinstance(variables, dict) or not isinstance(assets, dict):
        raise ValueError('Invalid static deployment configuration')
    if (config.get('name') != 'korea-replay' or config.get('main') != './worker/index.js'
            or config.get('no_bundle') is not True or assets.get('directory') != './client'
            or assets.get('binding') != 'ASSETS' or assets.get('run_worker_first') != ['/api/*']
            or assets.get('not_found_handling') != 'none'
            or config.get('version_metadata', {}).get('binding') != 'CF_VERSION_METADATA'
            or config.get('preview_urls') is not True):
        raise ValueError('Invalid static deployment configuration')
    if variables.get('STATIC_RELEASE_ID') != receipt['release_id']:
        raise ValueError('Code and map release do not match')
    if (variables.get('ENVIRONMENT') != 'production' or variables.get('DATA_STORAGE') != 'static'
            or variables.get('COLLECTORS_ENABLED') != 'false' or config.get('r2_buckets')
            or config.get('d1_databases') or config.get('triggers', {}).get('crons')):
        raise ValueError('Recovery requires the free static deployment configuration')
    if any(key in config for key in ('env', 'durable_objects', 'migrations', 'exports')):
        raise ValueError('Map deployment must not override environments or broker state bindings')
    schema = receipt.get('schema_version', 1)
    if type(schema) is not int or schema not in (1, 2):
        raise ValueError('Unknown deployment receipt schema')
    if 'deployment_contract' not in receipt:
        # Archives made before centralized transit remain valid, but cannot
        # acquire a new service dependency without a new audited receipt.
        if schema != 1:
            raise ValueError('Broker receipt requires its deployment contract')
        if 'LIVE_TRANSIT_MODE' in variables or ('services' in config and config['services'] != []):
            raise ValueError('Legacy deployment must retain its original transit configuration')
        return
    contract = receipt['deployment_contract']
    if (schema != 2 or not isinstance(contract, dict) or type(contract.get('schema_version')) is not int
            or contract != broker_deployment_contract()):
        raise ValueError('Invalid broker deployment contract')
    expected_vars = {'ENVIRONMENT': 'production', 'DATA_STORAGE': 'static',
                     'COLLECTORS_ENABLED': 'false', 'STATIC_RELEASE_ID': receipt['release_id'],
                     'LIVE_TRANSIT_MODE': 'broker'}
    expected_keys = {'name', 'main', 'compatibility_date', 'compatibility_flags', 'no_bundle',
                     'assets', 'vars', 'services', 'version_metadata', 'observability',
                     'workers_dev', 'preview_urls'}
    if (set(config) != expected_keys or variables != expected_vars
            or config.get('services') != [contract['broker']]):
        raise ValueError('Broker deployment requires the exact mode, service and configuration fields')


def tree_files(directory: Path):
    pending = [directory]
    while pending:
        folder = pending.pop()
        no_links(folder)
        if not folder.is_dir():
            raise ValueError('Missing deployment directory')
        with os.scandir(folder) as entries:
            for entry in entries:
                item = Path(entry.path)
                no_links(item)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(item)
                elif stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
                    yield item
                else:
                    raise ValueError('Only regular deployment files are supported')


def bundle_file_plan(bundle: Path):
    """Check deployment metadata; payload contents are checked by the caller."""
    no_links(bundle)
    receipt = read_json(bundle / 'receipt.json')
    valid_identity(receipt)
    if (receipt.get('complete') is not True or receipt.get('mode') != 'static'
            or receipt.get('audit', {}).get('passed') is not True
            or receipt.get('audit', {}).get('release_id') != receipt['release_id']):
        raise ValueError('Only complete audited static bundles can be exported')
    for name, key in [('wrangler.json', 'config_hash'), ('asset-manifest.json', 'manifest_hash')]:
        if digest(bundle / name) != receipt[key]:
            raise ValueError(f'Changed deployment metadata: {name}')
    config = read_json(bundle / 'wrangler.json')
    validate_config(config, receipt)
    assets = read_json(bundle / 'asset-manifest.json')
    if (type(receipt.get('count')) is not int or not 1 <= receipt['count'] <= MAX_ASSETS
            or not isinstance(assets, list) or len(assets) != receipt['count']):
        raise ValueError('Invalid static asset count')
    workers = receipt.get('worker_files')
    if not isinstance(workers, list) or not 1 <= len(workers) <= MAX_WORKER_FILES:
        raise ValueError('Invalid Worker file count')
    files = []
    for entry in assets:
        files.append({'path': 'client/' + safe_relative(entry['target']), 'bytes': entry['bytes'], 'sha256': entry['sha256']})
    for entry in workers:
        name = safe_relative(entry['target'])
        if not name.startswith('worker/'):
            raise ValueError('Worker entry outside worker directory')
        files.append({'path': name, 'bytes': regular_file(bundle / name).st_size, 'sha256': entry['sha256']})
    for name in METADATA_NAMES:
        files.append({'path': name, 'bytes': regular_file(bundle / name).st_size, 'sha256': digest(bundle / name)})
    check_files(files)
    if type(receipt.get('bytes')) is not int or receipt['bytes'] != sum(entry['bytes'] for entry in assets):
        raise ValueError('Invalid static asset byte total')
    if digest(bundle / 'client/data/catalog.json') != receipt['catalog_hash']:
        raise ValueError('Catalog identity mismatch')
    catalog = read_json(bundle / 'client/data/catalog.json')
    if catalog.get('schema_version') != 2 or catalog.get('release_id') != receipt['release_id']:
        raise ValueError('Catalog release identity mismatch')
    return receipt, sorted(files, key=lambda entry: entry['path'])


def check_bundle_tree(bundle: Path, files) -> None:
    # Refuse added or linked files inside deployed trees. Never scan .wrangler.
    actual = set()
    for subtree in ('client', 'worker'):
        actual.update(item.relative_to(bundle).as_posix() for item in tree_files(bundle / subtree))
    if actual != {entry['path'] for entry in files if entry['path'].startswith(('client/', 'worker/'))}:
        raise ValueError('Added or missing deployable files')


def inspect_bundle(bundle: Path):
    receipt, files = bundle_file_plan(bundle)
    check_bundle_tree(bundle, files)
    for entry in files:
        source = bundle / entry['path']
        if regular_file(source).st_size != entry['bytes'] or digest(source) != entry['sha256']:
            raise ValueError(f"Changed deployment file: {entry['path']}")
    return receipt, sorted(files, key=lambda entry: entry['path'])


def check_files(files) -> None:
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_ASSETS + MAX_WORKER_FILES + len(METADATA_NAMES):
        raise ValueError('Invalid recovery file count')
    names, exact_names, client_count, worker_count = set(), set(), 0, 0
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError('Invalid recovery file entry')
        name = safe_relative(entry['path'])
        if name not in METADATA_NAMES and not name.startswith(('client/', 'worker/')):
            raise ValueError('File outside deployable allowlist')
        if name.casefold() in names:
            raise ValueError('Duplicate recovery file')
        names.add(name.casefold())
        exact_names.add(name)
        client_count += name.startswith('client/')
        worker_count += name.startswith('worker/')
        limit = MAX_FILE if name.startswith(('client/', 'worker/')) else MAX_METADATA + 1
        if (type(entry['bytes']) is not int or not 0 <= entry['bytes'] < limit
                or not isinstance(entry['sha256'], str) or not HASH.fullmatch(entry['sha256'])):
            raise ValueError('Invalid recovery size or hash')
    if not 1 <= client_count <= MAX_ASSETS or not 1 <= worker_count <= MAX_WORKER_FILES:
        raise ValueError('Invalid client or Worker file count')
    if not set(METADATA_NAMES).union({'client/data/catalog.json', 'worker/index.js'}).issubset(exact_names):
        raise ValueError('Missing required recovery files')
    for name in names:
        if any('/'.join(name.split('/')[:index]) in names for index in range(1, len(name.split('/')))):
            raise ValueError('Recovery file and directory paths conflict')


def portable_receipt(receipt):
    # Local source hints are not required to redeploy and must not leak usernames.
    fields = ('schema_version', 'mode', 'release_id', 'created_at', 'catalog_hash', 'count', 'bytes',
              'audit', 'config_hash', 'manifest_hash', 'bundle_id', 'complete', 'deployment_contract')
    return {**{key: receipt[key] for key in fields if key in receipt},
            'bundle': '.', 'config': 'wrangler.json', 'catalog_path': 'client/data/catalog.json',
            'worker_files': [{'target': entry['target'], 'path': entry['target'], 'sha256': entry['sha256']}
                             for entry in receipt['worker_files']]}


def export_bundle(bundle: Path, output: Path, *, part_bytes=900 * CHUNK, reserve_bytes=30 * 1024**3):
    bundle, output = bundle.absolute(), output.absolute()
    no_links(output)
    if output.exists() or output.is_relative_to(bundle):
        raise ValueError('Export requires a new directory outside the bundle')
    if type(part_bytes) is not int or not 1 <= part_bytes <= MAX_PART:
        raise ValueError('Invalid archive part budget')
    receipt, files = inspect_bundle(bundle)
    original_receipt_hash = next(entry['sha256'] for entry in files if entry['path'] == 'receipt.json')
    receipt_body = json_bytes(portable_receipt(receipt))
    files = [({'path': entry['path'], 'bytes': len(receipt_body), 'sha256': hashlib.sha256(receipt_body).hexdigest()}
              if entry['path'] == 'receipt.json' else entry) for entry in files]
    check_files(files)
    groups, group, size = [], [], 0
    for entry in files:
        if group and size + entry['bytes'] > part_bytes:
            groups.append(group)
            group, size = [], 0
        group.append(entry)
        size += entry['bytes']
    if group:
        groups.append(group)
    if len(groups) > 1000:
        raise ValueError('Too many recovery parts')
    reserve(output.parent, sum(entry['bytes'] for entry in files) + len(files) * 1024, reserve_bytes)
    no_links(output)
    output.mkdir(parents=True, exist_ok=False)
    parts = []
    for index, group in enumerate(groups, 1):
        name = f"korea-replay-{receipt['bundle_id']}-{index:03d}.zip"
        archive = output / name
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as target:
            for entry in group:
                source = bundle / entry['path']
                no_links(source)
                value, count = hashlib.sha256(), 0
                info = zipfile.ZipInfo(entry['path'], date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                # ZipInfo supplied to open() does not inherit ZipFile.compresslevel.
                info.compress_level = 1
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                if entry['path'] == 'receipt.json':
                    if digest(source) != original_receipt_hash:
                        raise ValueError('Source changed during export')
                    target.writestr(info, receipt_body)
                    continue
                with source.open('rb') as raw, target.open(info, 'w', force_zip64=True) as destination:
                    for chunk in iter(lambda: raw.read(CHUNK), b''):
                        count += len(chunk)
                        if count > entry['bytes']:
                            raise ValueError('Source changed during export')
                        value.update(chunk)
                        destination.write(chunk)
                if count != entry['bytes'] or value.hexdigest() != entry['sha256']:
                    raise ValueError('Source changed during export')
        if archive.stat().st_size >= 2 * 1024**3:
            raise ValueError('Archive exceeds the release asset limit')
        parts.append({'name': name, 'bytes': archive.stat().st_size, 'sha256': digest(archive), 'files': [entry['path'] for entry in group]})
        print(f"Exported part {index}/{len(groups)}: {archive.stat().st_size:,} bytes", flush=True)
    manifest = {'schema_version': 1, 'kind': 'korea-replay-deployable-release', 'bundle_id': receipt['bundle_id'], 'release_id': receipt['release_id'], 'complete': True, 'source_receipt_sha256': original_receipt_hash, 'files': files, 'parts': parts}
    write_json_new(output / 'recovery-manifest.json', manifest)
    return manifest


def restore_bundle(manifest_path: Path, destination: Path, *, reserve_bytes=30 * 1024**3):
    manifest_path, destination = manifest_path.absolute(), destination.absolute()
    no_links(manifest_path)
    no_links(destination)
    if destination.exists():
        raise ValueError('Restore requires a new directory; existing files are never overwritten')
    manifest = read_json(manifest_path)
    valid_identity(manifest)
    if (type(manifest.get('schema_version')) is not int or manifest.get('schema_version') != 1
            or manifest.get('kind') != 'korea-replay-deployable-release' or manifest.get('complete') is not True):
        raise ValueError('Incomplete or unsupported recovery manifest')
    files, parts = manifest['files'], manifest['parts']
    check_files(files)
    expected = {entry['path']: entry for entry in files}
    if not isinstance(parts, list) or not 1 <= len(parts) <= 1000:
        raise ValueError('Invalid recovery parts')
    for part in parts:
        if (not isinstance(part, dict) or not isinstance(part.get('files'), list)
                or not 1 <= len(part['files']) <= len(files)
                or any(not isinstance(name, str) for name in part['files'])):
            raise ValueError('Invalid recovery part file list')
    listed = [name for part in parts for name in part['files']]
    if not parts or len(parts) > 1000 or len(listed) != len(set(listed)) or set(listed) != set(expected):
        raise ValueError('Missing or duplicated recovery parts')
    part_names = set()
    for part in parts:
        name = safe_relative(part['name'])
        if '/' in name or name.casefold() in part_names or not name.endswith('.zip'):
            raise ValueError('Invalid archive name')
        part_names.add(name.casefold())
        archive = manifest_path.parent / name
        no_links(archive)
        if (type(part['bytes']) is not int or not 0 < part['bytes'] < 2 * 1024**3
                or not isinstance(part['sha256'], str) or not HASH.fullmatch(part['sha256'])):
            raise ValueError('Invalid archive size or hash')
        if archive.stat().st_size != part['bytes'] or digest(archive) != part['sha256']:
            raise ValueError('Recovery archive hash mismatch')
        # Validate every central-directory entry before creating the restore tree.
        with zipfile.ZipFile(archive) as package:
            validate_members(package, part, expected)
    reserve(destination.parent, sum(entry['bytes'] for entry in files), reserve_bytes)
    destination.mkdir(parents=True, exist_ok=False)
    for index, part in enumerate(parts, 1):
        with zipfile.ZipFile(manifest_path.parent / part['name']) as archive:
            members = validate_members(archive, part, expected)
            for item in members:
                name = safe_relative(item.filename)
                entry = expected[name]
                # Preserve the exact archived receipt; write the relocated receipt
                # once, to a separate new file. No existing file is overwritten.
                target = destination / ('receipt.original.json' if name == 'receipt.json' else name)
                no_links(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                no_links(target)
                value, count = hashlib.sha256(), 0
                with archive.open(item) as source, target.open('xb') as output:
                    for chunk in iter(lambda: source.read(CHUNK), b''):
                        count += len(chunk)
                        if count > entry['bytes']:
                            raise ValueError('Archive expanded beyond declared size')
                        value.update(chunk)
                        output.write(chunk)
                if count != entry['bytes'] or value.hexdigest() != entry['sha256']:
                    raise ValueError('Restored file hash mismatch')
        print(f'Restored part {index}/{len(parts)}', flush=True)
    receipt = read_json(destination / 'receipt.original.json')
    valid_identity(receipt)
    if receipt['bundle_id'] != manifest['bundle_id'] or receipt['release_id'] != manifest['release_id']:
        raise ValueError('Restored release identity mismatch')
    # Relocation changes only local path hints, never code/map/config/asset hashes.
    relocated = {**receipt, 'bundle': str(destination), 'config': str(destination / 'wrangler.json'), 'catalog_path': str(destination / 'client/data/catalog.json'), 'worker_files': [{**entry, 'path': str(destination / entry['target'])} for entry in receipt['worker_files']]}
    write_json_new(destination / 'receipt.json', relocated)
    inspect_bundle(destination)
    result = {'schema_version': 1, 'passed': True, 'scope': 'Every restored code/map file and relocated receipt; not original-data or credential recovery', 'bundle_id': receipt['bundle_id'], 'release_id': receipt['release_id'], 'files': len(files), 'bytes': sum(entry['bytes'] for entry in files), 'recovery_manifest_sha256': digest(manifest_path)}
    write_json_new(destination / 'recovery-verification.json', result)
    return result


def validate_members(archive, part, expected):
    members = archive.infolist()
    if (len(members) != len(part['files'])
            or len({item.filename for item in members}) != len(members)
            or {item.filename for item in members} != set(part['files'])):
        raise ValueError('Archive file list does not match manifest')
    for item in members:
        name = safe_relative(item.filename)
        mode = item.external_attr >> 16
        if (item.is_dir() or stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                or (item.external_attr & 0xFFFF) & (0x400 | 0x10)):
            raise ValueError('Only regular files may be restored')
        if item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError('Unsupported recovery ZIP encoding')
        if item.file_size != expected[name]['bytes']:
            raise ValueError('Archive expanded size mismatch')
    return members


def pinned_manifest(path: Path, expected_sha256: str, kind: str):
    if not isinstance(expected_sha256, str) or not HASH.fullmatch(expected_sha256):
        raise ValueError('A trusted manifest SHA-256 is required')
    if regular_file(path).st_size > MAX_METADATA:
        raise ValueError('Recovery metadata exceeds 20 MiB')
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise ValueError('Pinned recovery manifest hash mismatch')
    manifest = json.loads(body, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    valid_identity(manifest)
    if (type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1
            or manifest.get('kind') != kind or manifest.get('complete') is not True):
        raise ValueError('Incomplete or unsupported recovery manifest')
    check_files(manifest.get('files'))
    return manifest


def checked_parts(parts, expected):
    """Validate a complete, nonoverlapping assignment of files to bounded ZIPs."""
    if not isinstance(parts, list) or len(parts) > 1000 or bool(parts) != bool(expected):
        raise ValueError('Invalid recovery parts')
    names, listed = set(), []
    for part in parts:
        if (not isinstance(part, dict) or not isinstance(part.get('files'), list)
                or not 1 <= len(part['files']) <= len(expected)
                or any(not isinstance(name, str) for name in part['files'])):
            raise ValueError('Invalid recovery part file list')
        name = safe_relative(part.get('name'))
        if '/' in name or name.casefold() in names or not name.endswith('.zip'):
            raise ValueError('Invalid archive name')
        names.add(name.casefold())
        if (type(part.get('bytes')) is not int or not 0 < part['bytes'] < 2 * 1024**3
                or not isinstance(part.get('sha256'), str) or not HASH.fullmatch(part['sha256'])):
            raise ValueError('Invalid archive size or hash')
        listed.extend(part['files'])
    if len(listed) != len(set(listed)) or set(listed) != set(expected):
        raise ValueError('Missing, unexpected or duplicated recovery files')
    return parts


def inspect_archives(directory: Path, parts, expected, *, verify_hash: bool):
    for part in parts:
        archive = directory / part['name']
        if regular_file(archive).st_size != part['bytes']:
            raise ValueError('Recovery archive size mismatch')
        if verify_hash and digest(archive) != part['sha256']:
            raise ValueError('Recovery archive hash mismatch')
        with zipfile.ZipFile(archive) as package:
            validate_members(package, part, expected)


def base_dependency(manifest, manifest_sha256):
    return {'manifest_sha256': manifest_sha256, 'bundle_id': manifest['bundle_id'],
            'release_id': manifest['release_id'],
            'parts': [{key: part[key] for key in ('name', 'bytes', 'sha256')}
                      for part in manifest['parts']]}


def reusable_files(files, base_files):
    return {entry['path'] for entry in files
            if entry['path'] in base_files
            and entry['bytes'] == base_files[entry['path']]['bytes']
            and entry['sha256'] == base_files[entry['path']]['sha256']}


def export_delta_bundle(base_manifest_path: Path, bundle: Path, output: Path, *,
                        base_manifest_sha256: str, part_bytes=900 * CHUNK,
                        reserve_bytes=30 * 1024**3):
    """Pack only changed files; base content hashes are verified when restoring."""
    base_manifest_path, bundle, output = (path.absolute() for path in (base_manifest_path, bundle, output))
    no_links(output)
    if output.exists():
        raise ValueError('Export requires a new directory; existing files are never overwritten')
    if output.is_relative_to(bundle) or output.is_relative_to(base_manifest_path.parent):
        raise ValueError('Delta output must be outside the bundle and base archives')
    if type(part_bytes) is not int or not 1 <= part_bytes <= MAX_PART:
        raise ValueError('Invalid recovery part budget')
    base = pinned_manifest(base_manifest_path, base_manifest_sha256, 'korea-replay-deployable-release')
    base_files = {entry['path']: entry for entry in base['files']}
    checked_parts(base.get('parts'), base_files)
    # Read central directories and sizes, but do not rehash gigabytes of unchanged
    # payloads during a small delta export. Restore verifies every pinned archive.
    inspect_archives(base_manifest_path.parent, base['parts'], base_files, verify_hash=False)
    original_receipt_hash = digest(bundle / 'receipt.json')
    receipt, files = bundle_file_plan(bundle)
    check_bundle_tree(bundle, files)
    receipt_body = json_bytes(portable_receipt(receipt))
    files = [{**entry, 'bytes': len(receipt_body), 'sha256': hashlib.sha256(receipt_body).hexdigest()}
             if entry['path'] == 'receipt.json' else entry for entry in files]
    check_files(files)
    reused = reusable_files(files, base_files)
    changed = [entry for entry in files if entry['path'] not in reused]
    # Verify all new input before writing output; verify it again while packing.
    for entry in changed:
        if entry['path'] == 'receipt.json':
            continue
        source = bundle / entry['path']
        if regular_file(source).st_size != entry['bytes'] or digest(source) != entry['sha256']:
            raise ValueError('Changed delta source file: ' + entry['path'])
    groups, group, size = [], [], 0
    for entry in changed:
        if group and size + entry['bytes'] > part_bytes:
            groups.append(group)
            group, size = [], 0
        group.append(entry)
        size += entry['bytes']
    if group:
        groups.append(group)
    if len(groups) > 1000:
        raise ValueError('Too many recovery parts')
    reserve(output.parent, sum(entry['bytes'] for entry in changed) + len(files) * 1024, reserve_bytes)
    if digest(bundle / 'receipt.json') != original_receipt_hash:
        raise ValueError('Source changed during delta export')
    no_links(output)
    output.mkdir(parents=True, exist_ok=False)
    parts = []
    for index, group in enumerate(groups, 1):
        archive = output / f"korea-replay-{receipt['bundle_id']}-delta-{index:03d}.zip"
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as target:
            for entry in group:
                info = zipfile.ZipInfo(entry['path'], date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.compress_level = 1
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                if entry['path'] == 'receipt.json':
                    target.writestr(info, receipt_body)
                    continue
                source = bundle / entry['path']
                regular_file(source)
                value, count = hashlib.sha256(), 0
                with source.open('rb') as raw, target.open(info, 'w', force_zip64=True) as destination:
                    for chunk in iter(lambda: raw.read(CHUNK), b''):
                        count += len(chunk)
                        if count > entry['bytes']:
                            raise ValueError('Source changed during delta export')
                        value.update(chunk)
                        destination.write(chunk)
                if count != entry['bytes'] or value.hexdigest() != entry['sha256']:
                    raise ValueError('Source changed during delta export')
        if archive.stat().st_size >= 2 * 1024**3:
            raise ValueError('Archive exceeds the release asset limit')
        parts.append({'name': archive.name, 'bytes': archive.stat().st_size,
                      'sha256': digest(archive), 'files': [entry['path'] for entry in group]})
        print(f"Exported delta part {index}/{len(groups)}: {archive.stat().st_size:,} bytes", flush=True)
    if (digest(bundle / 'receipt.json') != original_receipt_hash
            or digest(base_manifest_path) != base_manifest_sha256):
        raise ValueError('Dependency changed during delta export')
    manifest = {'schema_version': 1, 'kind': 'korea-replay-deployable-delta',
                'bundle_id': receipt['bundle_id'], 'release_id': receipt['release_id'], 'complete': True,
                'source_receipt_sha256': original_receipt_hash,
                'base': base_dependency(base, base_manifest_sha256), 'files': files, 'parts': parts,
                'export_verification': {'metadata_and_delta_contents_sha256': True,
                                        'base_archive_checks': 'size-and-members-only',
                                        'reused_contents_rehashed': False}}
    checked_parts(parts, {entry['path']: entry for entry in changed})
    if len(json_bytes(manifest)) > MAX_METADATA:
        raise ValueError('Recovery metadata exceeds 20 MiB')
    write_json_new(output / 'recovery-delta-manifest.json', manifest)
    return manifest


def extract_selected(directory: Path, parts, archive_files, selected, destination: Path):
    written = set()
    for part in parts:
        archive_path = directory / part['name']
        regular_file(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            for item in validate_members(archive, part, archive_files):
                name = safe_relative(item.filename)
                if name not in selected:
                    continue
                entry = archive_files[name]
                target = destination / ('receipt.original.json' if name == 'receipt.json' else name)
                no_links(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                no_links(target)
                value, count = hashlib.sha256(), 0
                with archive.open(item) as source, target.open('xb') as output:
                    for chunk in iter(lambda: source.read(CHUNK), b''):
                        count += len(chunk)
                        if count > entry['bytes']:
                            raise ValueError('Archive expanded beyond declared size')
                        value.update(chunk)
                        output.write(chunk)
                if count != entry['bytes'] or value.hexdigest() != entry['sha256']:
                    raise ValueError('Restored file hash mismatch')
                written.add(name)
    return written


def restore_delta_bundle(manifest_path: Path, base_manifest_path: Path, destination: Path, *,
                         manifest_sha256: str, reserve_bytes=30 * 1024**3):
    manifest_path, base_manifest_path, destination = (
        path.absolute() for path in (manifest_path, base_manifest_path, destination))
    no_links(destination)
    if destination.exists():
        raise ValueError('Restore requires a new directory; existing files are never overwritten')
    manifest = pinned_manifest(manifest_path, manifest_sha256, 'korea-replay-deployable-delta')
    dependency = manifest.get('base')
    if not isinstance(dependency, dict):
        raise ValueError('Missing pinned base dependency')
    base_hash = dependency.get('manifest_sha256')
    base = pinned_manifest(base_manifest_path, base_hash, 'korea-replay-deployable-release')
    base_files = {entry['path']: entry for entry in base['files']}
    checked_parts(base.get('parts'), base_files)
    if dependency != base_dependency(base, base_hash):
        raise ValueError('Pinned base dependency mismatch')
    files = {entry['path']: entry for entry in manifest['files']}
    reused = reusable_files(manifest['files'], base_files)
    changed = {name: entry for name, entry in files.items() if name not in reused}
    checked_parts(manifest.get('parts'), changed)
    # Both complete archive sets must pass before the output directory is created.
    inspect_archives(base_manifest_path.parent, base['parts'], base_files, verify_hash=True)
    inspect_archives(manifest_path.parent, manifest['parts'], changed, verify_hash=True)
    reserve(destination.parent, sum(entry['bytes'] for entry in files.values()), reserve_bytes)
    no_links(destination)
    destination.mkdir(parents=True, exist_ok=False)
    written = extract_selected(base_manifest_path.parent, base['parts'], base_files, reused, destination)
    written |= extract_selected(manifest_path.parent, manifest['parts'], changed, set(changed), destination)
    if written != set(files):
        raise ValueError('Incomplete final recovery file set')
    receipt = read_json(destination / 'receipt.original.json')
    valid_identity(receipt)
    if receipt['bundle_id'] != manifest['bundle_id'] or receipt['release_id'] != manifest['release_id']:
        raise ValueError('Restored release identity mismatch')
    relocated = {**receipt, 'bundle': str(destination), 'config': str(destination / 'wrangler.json'),
                 'catalog_path': str(destination / 'client/data/catalog.json'),
                 'worker_files': [{**entry, 'path': str(destination / entry['target'])}
                                  for entry in receipt['worker_files']]}
    write_json_new(destination / 'receipt.json', relocated)
    _, restored_files = inspect_bundle(destination)
    restored = {entry['path']: entry for entry in restored_files if entry['path'] != 'receipt.json'}
    if restored != {name: entry for name, entry in files.items() if name != 'receipt.json'}:
        raise ValueError('Final bundle differs from pinned file manifest')
    if digest(manifest_path) != manifest_sha256 or digest(base_manifest_path) != base_hash:
        raise ValueError('Dependency changed during delta restore')
    result = {'schema_version': 1, 'passed': True, 'kind': 'korea-replay-deployable-delta',
              'scope': 'Every final code/map file; not original-data or credential recovery',
              'bundle_id': receipt['bundle_id'], 'release_id': receipt['release_id'],
              'files': len(files), 'bytes': sum(entry['bytes'] for entry in files.values()),
              'reused_files': len(reused), 'delta_files': len(changed),
              'base_archive_sha256_verified': True, 'delta_archive_sha256_verified': True,
              'base_manifest_sha256': base_hash, 'recovery_manifest_sha256': manifest_sha256}
    write_json_new(destination / 'recovery-verification.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export')
    export.add_argument('--bundle', type=Path, required=True)
    export.add_argument('--output', type=Path, required=True)
    restore = commands.add_parser('restore')
    restore.add_argument('--manifest', type=Path, required=True)
    restore.add_argument('--destination', type=Path, required=True)
    delta_export = commands.add_parser('export-delta')
    delta_export.add_argument('--base-manifest', type=Path, required=True)
    delta_export.add_argument('--base-sha256', required=True)
    delta_export.add_argument('--bundle', type=Path, required=True)
    delta_export.add_argument('--output', type=Path, required=True)
    delta_restore = commands.add_parser('restore-delta')
    delta_restore.add_argument('--manifest', type=Path, required=True)
    delta_restore.add_argument('--manifest-sha256', required=True)
    delta_restore.add_argument('--base-manifest', type=Path, required=True)
    delta_restore.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'export':
        result = export_bundle(args.bundle, args.output)
    elif args.command == 'restore':
        result = restore_bundle(args.manifest, args.destination)
    elif args.command == 'export-delta':
        result = export_delta_bundle(args.base_manifest, args.bundle, args.output,
                                     base_manifest_sha256=args.base_sha256)
    else:
        result = restore_delta_bundle(args.manifest, args.base_manifest, args.destination,
                                      manifest_sha256=args.manifest_sha256)
    print(json.dumps({key: value for key, value in result.items() if key not in ('files', 'parts') or isinstance(value, int)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
