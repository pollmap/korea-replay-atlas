import hashlib
import json
from pathlib import Path

import pytest

from pipeline import pages_frontend
from pipeline.core import atomic_json, digest
from test_frontend_release import fixture  # noqa: F401 - shared audited build fixture


@pytest.fixture
def pages(fixture):
    directory = fixture.root / '.local/pages-release/korea-replay-fixture-candidate'
    entries = json.loads((fixture.bundle / 'asset-manifest.json').read_text())
    entries.append({'target': '_worker.js/app/index.js',
                    'bytes': (fixture.worker / 'index.js').stat().st_size,
                    'sha256': digest(fixture.worker / 'index.js')})
    # Policy entries are not frontend assets; they stay unchanged in Node staging.
    entries.extend({'target': name, 'bytes': 2, 'sha256': hashlib.sha256(b'{}').hexdigest()}
                   for name in ('_routes.json', '_redirects', '_worker.js/policy.js'))
    atomic_json(directory / 'asset-manifest.json', entries)
    receipt = {'schema_version': 1, 'complete': True, 'platform': 'cloudflare-pages',
               'project': 'korea-replay', 'artifact_sha256': 'a' * 64,
               'manifest_sha256': digest(directory / 'asset-manifest.json')}
    atomic_json(directory / 'receipt.json', receipt)
    return dict(receipt_path=directory / 'receipt.json', client_dir=fixture.client,
                worker_dir=fixture.worker, artifact_sha256='a' * 64, root=fixture.root)


def test_new_frontend_uses_lean_manifest_without_spatial_payloads(pages, fixture):
    before = digest(pages['receipt_path'])
    result = pages_frontend.inspect(**pages)
    assert result['artifact_sha256'] == 'a' * 64
    targets = {entry['target'] for entry in result['files']}
    assert 'assets/index-abcdefgh.js' in targets
    assert 'assets/index-12345678.js' not in targets
    assert not any(name.startswith(('data/', '_worker.js/')) for name in targets)
    assert digest(pages['receipt_path']) == before


@pytest.mark.parametrize('change', ['worker', 'vendor', 'credential', 'manifest', 'artifact'])
def test_rejects_unverified_build_or_base(pages, fixture, change):
    if change == 'worker':
        (fixture.worker / 'index.js').write_text('export default {changed:true};')
    elif change == 'vendor':
        (fixture.client / 'cesium/Workers/engine.js').write_text('changed')
    elif change == 'credential':
        (fixture.client / '.env').write_text('PRIVATE_FIXTURE=true')
    elif change == 'manifest':
        (pages['receipt_path'].parent / 'asset-manifest.json').write_text('[]')
    else:
        pages['artifact_sha256'] = 'b' * 64
    with pytest.raises(ValueError):
        pages_frontend.inspect(**pages)


def test_rejects_non_dist_input(pages, fixture):
    pages['client_dir'] = fixture.root / '.local/private'
    Path(pages['client_dir']).mkdir()
    with pytest.raises(ValueError, match='distinct dist'):
        pages_frontend.inspect(**pages)


def test_rejects_missing_worker_inventory(pages):
    manifest = pages['receipt_path'].parent / 'asset-manifest.json'
    entries = [item for item in json.loads(manifest.read_text())
               if not item['target'].startswith('_worker.js/app/')]
    atomic_json(manifest, entries)
    receipt = json.loads(pages['receipt_path'].read_text())
    receipt['manifest_sha256'] = digest(manifest)
    atomic_json(pages['receipt_path'], receipt)
    with pytest.raises(ValueError, match='no application Worker'):
        pages_frontend.inspect(**pages)


def test_retirement_requires_vendor_removed_but_preserves_other_audits(pages, fixture):
    import shutil
    with pytest.raises(ValueError, match='Retired 3D'):
        pages_frontend.inspect(**pages, retire_3d=True)
    shutil.rmtree(fixture.client / 'cesium')
    for prefix in ('MapScene-', 'geometry.worker-', 'search.worker-'):
        for path in (fixture.client / 'assets').glob(prefix+'*'): path.unlink()
    result = pages_frontend.inspect(**pages, retire_3d=True)
    assert not any(row['target'].startswith('cesium/') for row in result['files'])
    (fixture.worker / 'index.js').write_text('changed')
    with pytest.raises(ValueError):
        pages_frontend.inspect(**pages, retire_3d=True)


def test_separate_collection_is_preserved_outside_frontend_build(pages, fixture):
    manifest = pages['receipt_path'].parent / 'asset-manifest.json'
    entries = json.loads(manifest.read_text())
    entries.append({'target': 'collection/assets/collection-12345678.js',
                    'bytes': 10, 'sha256': 'c' * 64})
    atomic_json(manifest, entries)
    receipt = json.loads(pages['receipt_path'].read_text())
    receipt['manifest_sha256'] = digest(manifest)
    atomic_json(pages['receipt_path'], receipt)
    result = pages_frontend.inspect(**pages)
    assert not any(row['target'].startswith('collection/') for row in result['files'])
    (fixture.client / 'collection').mkdir()
    (fixture.client / 'collection/index.html').write_text('replacement')
    with pytest.raises(ValueError, match='separately published collection'):
        pages_frontend.inspect(**pages)


def test_worker_retirement_is_exact_and_does_not_disable_inventory_checks(pages, fixture, monkeypatch):
    import shutil
    shutil.rmtree(fixture.client / 'cesium')
    for path in (fixture.client / 'assets').glob('search.worker-*'): path.unlink()
    old = digest(fixture.worker / 'index.js')
    body = b'export default {sunRetired:true};'
    (fixture.worker / 'index.js').write_bytes(body)
    monkeypatch.setattr(pages_frontend, 'RETIRED_WORKER_FROM', old)
    monkeypatch.setattr(pages_frontend, 'RETIRED_WORKER_TO', digest(fixture.worker / 'index.js'))
    monkeypatch.setattr(pages_frontend, 'RETIRED_WORKER_BYTES', len(body))
    result = pages_frontend.inspect(**pages, retire_3d=True)
    assert result['worker_replacements'] == [{
        'target': '_worker.js/app/index.js', 'path': str(fixture.worker / 'index.js'),
        'bytes': len(body), 'sha256': digest(fixture.worker / 'index.js'),
        'previous_sha256': old, 'transition': pages_frontend.WORKER_TRANSITION}]
    with pytest.raises(ValueError):
        pages_frontend.inspect(**pages, retire_3d=False)
    (fixture.worker / 'extra.js').write_text('export const injected=true;')
    with pytest.raises(ValueError):
        pages_frontend.inspect(**pages, retire_3d=True)
    (fixture.worker / 'extra.js').unlink()
    (fixture.worker / 'index.js').write_bytes(body + b'//changed')
    with pytest.raises(ValueError):
        pages_frontend.inspect(**pages, retire_3d=True)


def test_post_inspection_worker_mutation_is_rejected(pages, fixture, monkeypatch):
    from pipeline import frontend_release
    original = frontend_release._frontend_entries
    def changed(client, prior):
        value = original(client, prior)
        (fixture.worker / 'index.js').write_text('export default {injected:true};')
        return value
    monkeypatch.setattr(frontend_release, '_frontend_entries', changed)
    with pytest.raises(ValueError):
        pages_frontend.inspect(**pages)
