"""Restage a frontend against an unchanged, previously audited static release.

This deliberately does not call ``static_release.prepare``: the prior bundle is
the only source of data files, including every catalog and observation list.
``static_release.stage(reuse_bundle=...)`` still verifies the prior receipt,
configuration, manifest, Worker and *all payload bytes* before materializing a
new bundle. Its existing hardlink and 30 GiB reserve protections remain active.

Only existing Vite chunk families, index.html and download-gate.js may change,
plus explicitly audited point/navigation and canonical SGIS selection assets.
New chunk families, copied-library changes, Worker changes or deployment-policy
changes require full staging. No credentials are read to perform this operation;
the frontend scan rejects recognizable credential literals, but cannot prove
that an arbitrary unlabelled string is not a secret.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from . import static_release as release
from .core import LOCAL, ROOT, digest, now
from .recovery_bundle import (
    broker_deployment_contract, bundle_file_plan, no_links, read_json,
    regular_file, safe_relative, tree_files,
)

GENERATED = frozenset(('data/catalog.json', '_headers', '404.html'))
MUTABLE_ROOT = frozenset(('index.html', 'download-gate.js'))
ROOT_FILES = MUTABLE_ROOT | {'.assetsignore'}
CHUNK_NAME = re.compile(r'assets/([A-Za-z0-9_][A-Za-z0-9_.-]*)-[A-Za-z0-9_-]{8}\.(js|css)\Z')
SEOUL_KAPT_GEOJSON_SHA = '8360eb2d88be0ab4259b5d92e5a98d25372e6bf19ad739dfbad6c26622debe82'
SEOUL_KAPT_JOINED_SHA = '33058dae0a1d86c302b2f1c5b0dff9d71241a60031880f4f738c9fe506611792'
SEOUL_KAPT_RECENT_SHA = 'b63b62af834062de98b142f4caef4f8a2087bd8e713c15ba3351fcf3dc06859c'
SEOUL_KAPT_ASSET_HASHES = {
    '8360eb2d88be0ab4': SEOUL_KAPT_GEOJSON_SHA,
    '33058dae0a1d86c3': SEOUL_KAPT_JOINED_SHA,
    'b63b62af834062de': SEOUL_KAPT_RECENT_SHA,
    '14916a799c24a49e': '14916a799c24a49e0bd2c91311dfc8e98ff287fb4ce8d09a958bad75f1208c35',
}
SEOUL_KAPT_GEOJSON = re.compile(r'assets/seoul-kapt-points-([a-f0-9]{16})-[A-Za-z0-9_-]{8}\.geojson\Z')
PROPERTY_NAVIGATION = re.compile(r'assets/seoul-property-navigation-(?:[a-f0-9]{16}-)?[A-Za-z0-9_-]{8}\.json\Z')
PROPERTY_NAVIGATION_SHA = 'b93cbc63ea3e74836f349ed11dc73742ee2095f9ba258a13b9812c726879cf7e'
PROPERTY_NAVIGATION_ASSETS = {
    (PROPERTY_NAVIGATION_SHA, 55929),
    ('9ee094b267838e30d2f5117c027444d16fedc8b5348f1214011a265e93432e7c', 55930),
}
REGION_ASSET = re.compile(r'assets/(boundary-(?:[0-9]{2}|[0-9]{5})|dongs-[0-9]{5})-[A-Za-z0-9_-]{8}\.json\Z')
REGION_SOURCE_ROOT = Path(__file__).resolve().parents[1] / 'src/data'
REGION_ARCHIVE_SHA = 'f1cf0f9de453ac7eaacb273f39cee52851183372b9ddfda428a967c3a670b2c6'
REGION_REFERENCE_DATE = '2025-06-30'
REGION_MAX_FILE_BYTES = 1024 * 1024
REGION_MAX_TOTAL_BYTES = 10 * 1024 * 1024
REGION_MAX_FILES = 521


def _region_asset_inventory():
    """Trust only the canonical audited source manifests, never dist manifests.

    Geometry is source-audited separately. This gate pins every published byte,
    administrative code, feature count and source revision to that source tree.
    No user-supplied manifest path or frontend option can widen this inventory.
    """
    result = {}
    total_bytes = 0
    for stem, prefix, directory, expected_chunks, expected_features in (
        ('region-selection', 'boundary', 'region-selection-areas', 269, 269),
        ('region-selection-dongs', 'dongs', 'region-selection-dongs', 252, 3559),
    ):
        index = REGION_SOURCE_ROOT / (stem + '.json')
        if regular_file(index).st_size > REGION_MAX_FILE_BYTES:
            raise ValueError('Region source manifest exceeds size budget')
        manifest = read_json(index)
        if (manifest.get('schema') != 1 or manifest.get('referenceDate') != REGION_REFERENCE_DATE
                or manifest.get('namespace') != 'SGIS administrative'
                or manifest.get('archiveSha256') != REGION_ARCHIVE_SHA
                or manifest.get('featureCount') != expected_features
                or not isinstance(manifest.get('chunks'), list)
                or len(manifest['chunks']) != expected_chunks):
            raise ValueError('Region source manifest revision or count is not audited')
        feature_ids = set()
        for row in manifest['chunks']:
            if not isinstance(row, list) or len(row) != 3:
                raise ValueError('Invalid region source manifest reference')
            code, size, sha = row
            pattern = r'(?:[0-9]{2}|[0-9]{5})' if prefix == 'boundary' else r'[0-9]{5}'
            if (not isinstance(code, str) or not re.fullmatch(pattern, code)
                    or type(size) is not int or not 0 < size <= REGION_MAX_FILE_BYTES
                    or not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha)):
                raise ValueError('Invalid region source identity or byte budget')
            key = prefix + '-' + code
            if key in result:
                raise ValueError('Duplicate region source identity')
            source = REGION_SOURCE_ROOT / directory / (key + '.json')
            if regular_file(source).st_size != size:
                raise ValueError('Region source size differs from audited manifest')
            body = source.read_bytes()
            if len(body) != size or hashlib.sha256(body).hexdigest() != sha:
                raise ValueError('Region source hash differs from audited manifest')
            if any(pattern.search(body) for pattern in CREDENTIAL_PATTERNS):
                raise ValueError('Credential-like content is forbidden in region assets')
            features = json.loads(body)
            if not isinstance(features, list) or not features or prefix == 'boundary' and len(features) != 1:
                raise ValueError('Invalid region source feature count')
            for feature in features:
                if (not isinstance(feature, list) or len(feature) != 4
                        or not isinstance(feature[0], str) or not isinstance(feature[1], str)
                        or type(feature[2]) is not int or not 0 <= feature[2] <= 9
                        or not isinstance(feature[3], list) or not feature[3]
                        or (feature[0] != code if prefix == 'boundary' else not re.fullmatch(code + r'[0-9]{3}', feature[0]))
                        or feature[0] in feature_ids):
                    raise ValueError('Invalid or duplicate region source feature identity')
                feature_ids.add(feature[0])
            result[key] = {'bytes': size, 'sha256': sha}
            total_bytes += size
        if len(feature_ids) != expected_features:
            raise ValueError('Region source feature count differs from manifest')
    if len(result) != REGION_MAX_FILES or total_bytes > REGION_MAX_TOTAL_BYTES:
        raise ValueError('Region source inventory exceeds audited count or byte budget')
    return result


def _approved_point_asset(name: str, sha: str, size: int) -> bool:
    match = SEOUL_KAPT_GEOJSON.fullmatch(name)
    return bool(match and SEOUL_KAPT_ASSET_HASHES.get(match[1]) == sha and size <= 2 * 1024 * 1024
                or PROPERTY_NAVIGATION.fullmatch(name) and (sha, size) in PROPERTY_NAVIGATION_ASSETS)
FORBIDDEN_NAME = re.compile(r'(?i)(?:^|[-_.])(?:secrets?|credentials?|tokens?|private|id_rsa)(?:$|[-_.])')
CREDENTIAL_PATTERNS = (
    re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----'),
    re.compile(rb'\b(?:github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16})\b'),
    re.compile(rb'''(?ix)(?:DATA_GO_KR_SERVICE_KEY|SEOUL_SUBWAY_API_KEY|CLOUDFLARE_API_TOKEN|
        QA_TOKEN|oauth_token|service_?key|auth_?key|api_?key|access_token|client_secret)
        ["']?\s*[:=]\s*["']?([A-Za-z0-9+/%=_-]{12,})'''),
    re.compile(rb'''(?i)\bAuthorization["']?\s*[:=]\s*["']?Bearer\s+[A-Za-z0-9._~+/-]{12,}'''),
)


def _inside(root: Path, path: Path, description: str) -> Path:
    value = Path(path)
    if '..' in value.parts:
        raise ValueError(description + ' must not traverse outside its directory')
    value = value if value.is_absolute() else root / value
    no_links(value)
    value = value.resolve()
    if value == root or not value.is_relative_to(root):
        raise ValueError(description + ' must stay inside the workspace')
    return value


def _family(name: str):
    match = CHUNK_NAME.fullmatch(name)
    return match.groups() if match else None


def _frontend_name(name: str) -> None:
    safe_relative(name)
    if name == '.assetsignore':
        return
    if (any(part.startswith('.') for part in name.split('/'))
            or FORBIDDEN_NAME.search(Path(name).name)):
        raise ValueError('Private or hidden files are forbidden in the frontend')
    if name not in MUTABLE_ROOT and not name.startswith('cesium/') and not _family(name) and not SEOUL_KAPT_GEOJSON.fullmatch(name) and not PROPERTY_NAVIGATION.fullmatch(name) and not REGION_ASSET.fullmatch(name):
        raise ValueError('Unknown frontend file; full staging is required')


def _frontend_entries(client: Path, previous: dict):
    """Closed path inventory, unchanged vendor files, and bounded text scanning."""
    old = {name: entry for name, entry in previous.items()
           if not name.startswith('data/') and name not in GENERATED}
    if 'index.html' not in old:
        raise ValueError('Previous frontend has no index.html')
    families = set()
    directories = {'assets'}
    for name in old:
        _frontend_name(name)
        family = _family(name)
        if family:
            if family in families:
                raise ValueError('Ambiguous previous frontend chunk family')
            families.add(family)
        directories.update(parent.as_posix() for parent in Path(name).parents if parent.as_posix() != '.')

    # tree_files rejects links, junctions, sockets and nonregular files. Check
    # directories too so an empty data/ or hidden directory cannot slip through.
    paths = sorted(tree_files(client))
    region_inventory = _region_asset_inventory() if any(REGION_ASSET.fullmatch(path.relative_to(client).as_posix()) for path in paths) else {}
    seen_regions = set()
    pending = [client]
    while pending:
        folder = pending.pop()
        for child in folder.iterdir():
            no_links(child)
            if child.is_dir():
                if child.relative_to(client).as_posix() not in directories:
                    raise ValueError('Unknown frontend directory; full staging is required')
                pending.append(child)
    result = []
    names = set()
    seen_families = set()
    for path in paths:
        name = path.relative_to(client).as_posix()
        _frontend_name(name)
        if name.casefold() in names:
            raise ValueError('Duplicate frontend path')
        names.add(name.casefold())
        family = _family(name)
        if family:
            if family not in families or family in seen_families:
                raise ValueError('Changed frontend chunk families; full staging is required')
            seen_families.add(family)
        elif name not in old and not SEOUL_KAPT_GEOJSON.fullmatch(name) and not PROPERTY_NAVIGATION.fullmatch(name) and not REGION_ASSET.fullmatch(name):
            raise ValueError('Unknown frontend file; full staging is required')
        size = regular_file(path).st_size
        if not 0 <= size < release.MAX_FILE_BYTES:
            raise ValueError('Frontend file exceeds the static asset size ceiling')
        sha = digest(path)
        prior = old.get(name)
        unchanged = prior and prior['sha256'] == sha and prior['bytes'] == size
        approved_point_asset = _approved_point_asset(name, sha, size)
        region_match = REGION_ASSET.fullmatch(name)
        approved_region_asset = bool(region_match and region_inventory.get(region_match[1]) == {'bytes': size, 'sha256': sha})
        if region_match:
            if not approved_region_asset:
                raise ValueError('Region asset is unlisted or differs from audited manifest')
            if region_match[1] in seen_regions:
                raise ValueError('Duplicate published region asset identity')
            seen_regions.add(region_match[1])
        if name not in MUTABLE_ROOT and not family and not unchanged and not approved_point_asset and not approved_region_asset:
            raise ValueError('Copied frontend assets changed; full staging is required')
        if (family or region_match) and prior and not unchanged:
            raise ValueError('An immutable frontend asset URL changed bytes')
        if name in MUTABLE_ROOT or family or approved_point_asset or approved_region_asset:
            # Reads are bounded by the same 24 MiB ceiling as static staging.
            body = path.read_bytes()
            if len(body) != size or hashlib.sha256(body).hexdigest() != sha:
                raise ValueError('Frontend changed during inspection')
            if b'\x00' in body:
                raise ValueError('Frontend text contains binary content')
            try:
                body.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError('Frontend text is not UTF-8') from None
            if any(pattern.search(body) for pattern in CREDENTIAL_PATTERNS):
                raise ValueError('Credential-like content is forbidden in the frontend')
        result.append({'path': str(path), 'target': name, 'bytes': size, 'sha256': sha})
    # Immutable app previews retain their former URL. A replaced, SHA-checked
    # point or region asset need not be copied into the next app bundle.
    stable_names = set(old) - {name for name, entry in old.items()
                               if _family(name) or _approved_point_asset(name, entry['sha256'], entry['bytes'])
                               or REGION_ASSET.fullmatch(name) and REGION_ASSET.fullmatch(name)[1] in seen_regions}
    if seen_regions != set(region_inventory):
        raise ValueError('Audited region assets are missing from the frontend')
    if not stable_names.issubset({entry['target'] for entry in result}) or seen_families != families:
        raise ValueError('Frontend files or chunk families are missing; full staging is required')
    return result


def _verify_current_worker(worker: Path, receipt: dict) -> None:
    expected = {entry['target']: entry['sha256'] for entry in receipt['worker_files']}
    actual = {}
    for path in tree_files(worker):
        name = path.relative_to(worker).as_posix()
        safe_relative(name)
        # These Vite outputs are excluded by static_release.stage as well. Do
        # not open .dev.vars: recognizing its name is not permission to read it.
        if name in ('.dev.vars', 'wrangler.json') or name.startswith('.vite/'):
            continue
        if (any(part.startswith('.') for part in name.split('/'))
                or path.suffix not in ('.js', '.mjs', '.cjs', '.wasm', '.map')):
            raise ValueError('Unknown Worker build file; full staging is required')
        target = 'worker/' + name
        if target.casefold() in {value.casefold() for value in actual}:
            raise ValueError('Duplicate Worker build path')
        actual[target] = digest(path)
    if actual != expected:
        raise ValueError('Worker build changed; full staging is required')


def _prior_metadata(bundle: Path):
    # This reads small metadata and the catalog only. The existing stage function
    # performs full byte verification exactly through its normal reuse path.
    receipt, _ = bundle_file_plan(bundle)
    if (Path(receipt.get('bundle', '')).absolute() != bundle
            or Path(receipt.get('config', '')).absolute() != bundle / 'wrangler.json'
            or bundle.name != receipt['bundle_id']):
        raise ValueError('Previous receipt does not identify this immutable bundle')
    config = read_json(bundle / 'wrangler.json')
    if (receipt.get('schema_version') != 2
            or receipt.get('deployment_contract') != broker_deployment_contract()
            or config != release.deployment_config(receipt['release_id'])):
        raise ValueError('Deployment configuration changed; full staging is required')
    entries = read_json(bundle / 'asset-manifest.json')
    assets = {entry['target']: entry for entry in entries}
    if len(assets) != len(entries) or not GENERATED.issubset(assets):
        raise ValueError('Previous manifest has invalid generated files')
    files = [entry for entry in entries if entry['target'] not in GENERATED]
    headers = release.headers_for(files)
    for name, body in (('_headers', headers.encode()), ('404.html', release.NOT_FOUND.encode())):
        entry = assets[name]
        if entry['bytes'] != len(body) or entry['sha256'] != hashlib.sha256(body).hexdigest():
            raise ValueError('Generated deployment policy changed; full staging is required')
    fingerprint = hashlib.sha256(release.encoded({
        'assets': [(entry['target'], entry['sha256']) for entry in files],
        'worker': [(entry['target'], entry['sha256']) for entry in receipt['worker_files']],
        'catalog': receipt['catalog_hash'], 'headers': headers,
        'not_found': release.NOT_FOUND, 'config_version': 3,
        'config': config, 'deployment_contract': receipt['deployment_contract'],
    })).hexdigest()[:16]
    if fingerprint != receipt['bundle_id']:
        raise ValueError('Previous bundle fingerprint does not match its manifest')
    release_catalog = assets.get('data/releases/' + receipt['release_id'] + '.json')
    if not release_catalog or release_catalog['sha256'] != receipt['catalog_hash']:
        raise ValueError('Previous immutable catalog is not pinned to the receipt')
    return receipt, assets


def restage(*, reuse_bundle: Path, client_dir: Path | None = None,
            worker_dir: Path | None = None, output: Path | None = None,
            root: Path = ROOT):
    """Create a new frontend-only bundle; never regenerate or mutate map data."""
    no_links(root)
    root = Path(root).resolve()
    bundle = _inside(root, reuse_bundle, 'Reuse bundle')
    client = _inside(root, client_dir or root / 'dist/client', 'Frontend build')
    worker = _inside(root, worker_dir or root / 'dist/korea_replay', 'Worker build')
    output = _inside(root, output or root / '.local/deploy', 'Staging output')
    if (not client.is_relative_to(root / 'dist') or not worker.is_relative_to(root / 'dist')
            or not bundle.is_relative_to(root / '.local') or not output.is_relative_to(root / '.local')
            or output == bundle or output.is_relative_to(bundle)
            or client == worker or client.is_relative_to(worker) or worker.is_relative_to(client)):
        raise ValueError('Frontend-only inputs must use distinct dist and local bundle directories')
    receipt, assets = _prior_metadata(bundle)
    receipt_hash = digest(bundle / 'receipt.json')
    _verify_current_worker(worker, receipt)
    frontend = _frontend_entries(client, assets)
    data = [
        {'path': str(bundle / 'client' / name), 'target': name,
         'bytes': entry['bytes'], 'sha256': entry['sha256']}
        for name, entry in assets.items()
        if name.startswith('data/') and name not in GENERATED
    ]
    files = data + frontend
    if len(files) + len(GENERATED) > release.MAX_FILES:
        raise ValueError('Frontend-only bundle exceeds the static file count ceiling')
    plan = {
        'schema_version': 1, 'mode': 'static', 'release_id': receipt['release_id'], 'created_at': now(),
        'catalog_path': str(bundle / 'client/data/catalog.json'), 'catalog_hash': receipt['catalog_hash'],
        'count': len(files) + len(GENERATED), 'bytes': sum(entry['bytes'] for entry in files), 'files': files,
        'audit': {'passed': True, 'release_id': receipt['release_id'],
                  'scope': 'Frontend-only; inherited audited data, with full static-stage byte verification'},
        'frontend_only': {
            'schema_version': 1, 'reuse_bundle_id': receipt['bundle_id'], 'receipt_hash': receipt_hash,
            'manifest_hash': receipt['manifest_hash'], 'config_hash': receipt['config_hash'],
            'source_catalog_hash': receipt['catalog_hash'], 'immutable_data_files': len(data) + 1,
            'immutable_data_bytes': sum(entry['bytes'] for entry in data) + assets['data/catalog.json']['bytes'],
        },
    }
    if digest(bundle / 'receipt.json') != receipt_hash:
        raise ValueError('Previous receipt changed during frontend inspection')
    _verify_current_worker(worker, receipt)
    # Pin actual staged code to the verified prior Worker. A concurrent Vite build
    # cannot inject a different Worker between our check and stage's materializer.
    return release.stage(plan, output=output, worker_dir=bundle / 'worker', reuse_bundle=bundle)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-bundle', type=Path, required=True,
                        help='Complete immutable staged bundle; all original bytes are still verified')
    parser.add_argument('--client-dir', type=Path, default=ROOT / 'dist/client')
    parser.add_argument('--worker-dir', type=Path, default=ROOT / 'dist/korea_replay')
    parser.add_argument('--output', type=Path, default=LOCAL / 'deploy')
    args = parser.parse_args(argv)
    try:
        result = restage(reuse_bundle=args.reuse_bundle, client_dir=args.client_dir,
                         worker_dir=args.worker_dir, output=args.output)
    except (OSError, ValueError) as error:
        # Never include payload bytes or scanner matches in diagnostic output.
        parser.exit(2, 'Frontend-only staging refused: ' + str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    main()
