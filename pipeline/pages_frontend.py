"""Inspect a fresh frontend against a lean Pages stage without restoring 3D.

Called by the Pages stager after full source-stage verification. This inspector
reuses the frontend, source-data and source-audited Worker gates of frontend_release.
It writes no payload files and performs no network requests.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from . import frontend_release as frontend
from .core import ROOT, digest
from .recovery_bundle import no_links, read_json, regular_file


# Full compiled diff reviewed against PR95 (09019d2 / a3b80cf): only SunCalc
# implementation and the retired sun source/coverage records disappear. All API
# handlers, bindings and routing bytes remain unchanged. No generic Worker bypass.
RETIRED_WORKER_FROM = 'f2d2d04f607fdca15f50fb863bbb1fab94aef713851c89bc9eeb9dd14f218acf'
RETIRED_WORKER_TO = 'd5547d557c8802ef8de00284844cfc375d1a4d9912f78fbaedd9e736b036c17d'
RETIRED_WORKER_BYTES = 149458
WORKER_TRANSITION = 'sun-runtime-retirement-20261009'


def _inspect_worker(worker, receipt, retire_3d):
    expected = {entry['target']: entry['sha256'] for entry in receipt['worker_files']}
    if (retire_3d and expected == {'worker/index.js': RETIRED_WORKER_FROM}
            and regular_file(worker / 'index.js').st_size == RETIRED_WORKER_BYTES
            and digest(worker / 'index.js') == RETIRED_WORKER_TO):
        # This also rejects any extra/missing/hidden module, not just index.js.
        frontend._verify_current_worker(worker, {'worker_files': [
            {'target': 'worker/index.js', 'sha256': RETIRED_WORKER_TO}]})
        return [{'target': '_worker.js/app/index.js', 'path': str(worker / 'index.js'),
                 'bytes': RETIRED_WORKER_BYTES, 'sha256': RETIRED_WORKER_TO,
                 'previous_sha256': RETIRED_WORKER_FROM, 'transition': WORKER_TRANSITION}]
    frontend._verify_current_worker(worker, receipt)
    return []

PAGES_FILES = frozenset(('_routes.json', '_redirects'))


def inspect(*, receipt_path: Path, client_dir: Path, worker_dir: Path,
            artifact_sha256: str, root: Path = ROOT, retire_3d: bool = False):
    no_links(root)
    root = Path(root).resolve()
    receipt_path = frontend._inside(root, receipt_path, 'Pages receipt')
    client = frontend._inside(root, client_dir, 'Frontend build')
    worker = frontend._inside(root, worker_dir, 'Worker build')
    if (not receipt_path.is_relative_to(root / '.local/pages-release')
            or not client.is_relative_to(root / 'dist')
            or not worker.is_relative_to(root / 'dist')
            or client == worker or client.is_relative_to(worker)
            or worker.is_relative_to(client)):
        raise ValueError('Use a Pages receipt and distinct dist build directories')
    receipt_hash = digest(receipt_path)
    receipt = read_json(receipt_path)
    manifest_path = receipt_path.parent / 'asset-manifest.json'
    regular_file(manifest_path)
    if (receipt.get('schema_version') != 1 or receipt.get('complete') is not True
            or receipt.get('project') != 'korea-replay'
            or receipt.get('platform') != 'cloudflare-pages'
            or receipt.get('artifact_sha256') != artifact_sha256
            or digest(manifest_path) != receipt.get('manifest_sha256')):
        raise ValueError('Pages base changed or is not the verified application')
    entries = read_json(manifest_path)
    prior = {entry['target']: entry for entry in entries
             if not entry['target'].startswith('_worker.js/')
             and entry['target'] not in PAGES_FILES
             and not entry['target'].startswith('collection/')}
    if retire_3d:
        # Explicitly retire only the spatial renderer and its private workers.
        retired = {('MapScene', 'js'), ('MapScene', 'css'),
                   ('geometry.worker', 'js'), ('search.worker', 'js')}
        prior = {name: entry for name, entry in prior.items()
                 if not name.startswith('cesium/') and frontend._family(name) not in retired}
        if any(p.relative_to(client).as_posix().startswith('cesium/')
               or frontend._family(p.name if p.parent == client else p.relative_to(client).as_posix()) in retired
               for p in client.rglob('*') if p.is_file()):
            raise ValueError('Retired 3D assets remain in the new frontend')
    worker_receipt = {'worker_files': [
        {'target': 'worker/' + entry['target'][len('_worker.js/app/'):],
         'sha256': entry['sha256']}
        for entry in entries if entry['target'].startswith('_worker.js/app/')
    ]}
    if not worker_receipt['worker_files']:
        raise ValueError('Pages base has no application Worker')
    worker_replacements = _inspect_worker(worker, worker_receipt, retire_3d)
    if (client / 'collection').exists():
        raise ValueError('Frontend cannot replace the separately published collection')
    result = frontend._frontend_entries(client, prior)
    if _inspect_worker(worker, worker_receipt, retire_3d) != worker_replacements:
        raise ValueError('Worker changed during frontend inspection')
    if (digest(receipt_path) != receipt_hash
            or digest(manifest_path) != receipt['manifest_sha256']):
        raise ValueError('Pages base changed during frontend inspection')
    return {'schema_version': 1, 'artifact_sha256': artifact_sha256,
            'manifest_sha256': receipt['manifest_sha256'], 'files': result,
            'worker_replacements': worker_replacements}


def main():
    request = json.loads(sys.stdin.buffer.read(65537))
    result = inspect(receipt_path=Path(request['receiptPath']),
                     client_dir=Path(request['clientDirectory']),
                     worker_dir=Path(request['workerDirectory']),
                     artifact_sha256=request['artifactSha256'], retire_3d=request.get('retire3d') is True)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
