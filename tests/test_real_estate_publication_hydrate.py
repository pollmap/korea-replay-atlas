import json
import sqlite3

import pytest

from pipeline.real_estate import RealEstateError
from pipeline.real_estate_archive import backup
from pipeline.real_estate_publication_hydrate import _hydrate, _reference, hydrate_for_publication
from pipeline.real_estate_remote import RemoteWorkspace
from pipeline.real_estate_publish import publish
from test_real_estate_archive import LocalD1
from test_real_estate_publish import setup
from test_real_estate_fetch import registry


def test_selective_restore_reuses_current_and_refresh_sources(tmp_path):
    root = setup(tmp_path)
    with sqlite3.connect(root / 'checkpoint.sqlite') as db:
        old = db.execute("SELECT id FROM jobs WHERE status='complete' LIMIT 1").fetchone()[0]
        db.execute("UPDATE jobs SET status='pending',pages='[]' WHERE id=?", (old,))
    store = LocalD1()
    backup(root, store)
    workspace = RemoteWorkspace(tmp_path / 'hydrated', store)
    result = hydrate_for_publication(workspace, reuse_roots=[root])
    assert result['refresh_source_references'] == 1
    assert result['reused_files'] >= 3
    assert result['public_release'] is False
    release = publish(workspace.root, registry(), tmp_path / 'candidates', reserve_bytes=0)
    assert release['audit']['source_rows'] == 4
    assert release['audit']['stale_jobs'] == 1


def test_selective_restore_rejects_reference_absent_from_pinned_head(tmp_path):
    root = setup(tmp_path)
    store = LocalD1()
    backup(root, store)
    workspace = RemoteWorkspace(tmp_path / 'hydrated', store)
    with pytest.raises(RealEstateError, match='publication_reference_not_in_head'):
        _reference(workspace, {'path': 'raw/sale/11110/202609/' + '0' * 64 + '.xml',
                               'sha256': '0' * 64, 'bytes': 1})
    with pytest.raises(RealEstateError, match='archive_path'):
        _reference(workspace, {'path': '../secret', 'sha256': '0' * 64, 'bytes': 1})


def test_corrupt_cache_is_ignored_and_remote_bytes_are_verified(tmp_path):
    root = setup(tmp_path)
    store = LocalD1()
    backup(root, store)
    cache = tmp_path / 'cache'
    cache.mkdir()
    with sqlite3.connect(root / 'checkpoint.sqlite') as db:
        raw = json.loads(db.execute("SELECT pages FROM jobs WHERE status='complete' LIMIT 1").fetchone()[0])[0]
    target = cache / raw['path']
    target.parent.mkdir(parents=True)
    target.write_bytes(b'bad')
    workspace = RemoteWorkspace(tmp_path / 'hydrated', store)
    result = hydrate_for_publication(workspace, reuse_roots=[cache])
    assert result['reused_files'] == 0
    assert (workspace.root / raw['path']).read_bytes() == (root / raw['path']).read_bytes()


def test_remote_hydration_groups_files_by_backing_pack(tmp_path):
    class RecordingWorkspace:
        root = tmp_path
        files = {
            'raw/a.xml': {'object': {'sha256': 'a'}},
            'raw/b.xml': {'object': {'sha256': 'b'}},
            'raw/c.xml': {'object': {'sha256': 'a'}},
        }

        def __init__(self):
            self.calls = []

        def hydrate(self, names):
            self.calls.append(list(names))

    workspace = RecordingWorkspace()
    assert _hydrate(workspace, set(workspace.files), ()) == 0
    assert workspace.calls == [[], ['raw/a.xml', 'raw/c.xml', 'raw/b.xml']]


def test_publication_workspace_resumes_only_verified_existing_checkpoint(tmp_path):
    root = setup(tmp_path)
    store = LocalD1()
    backup(root, store)
    folder = tmp_path / 'hydrated'
    initial = RemoteWorkspace(folder, store)
    resumed = RemoteWorkspace(folder, store, resume_head=initial.head)
    assert resumed.head == initial.head
    assert resumed.hydrated_files == 0
    with pytest.raises(RealEstateError, match='resumed_workspace_read_only'):
        resumed.publish(None)
    with pytest.raises(RealEstateError, match='archive_head_changed'):
        RemoteWorkspace(folder, store, resume_head={'sha256': '0' * 64, 'bytes': 1})
    (folder / 'checkpoint.sqlite').write_bytes(b'corrupt')
    with pytest.raises(RealEstateError, match='remote_local_file_changed'):
        RemoteWorkspace(folder, store, resume_head=initial.head)
