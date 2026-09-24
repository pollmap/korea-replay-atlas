from copy import deepcopy
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from pipeline.real_estate import RealEstateError, canonical_bytes, sha256
from pipeline.real_estate_fetch import Collector, month_sequence
from pipeline.real_estate_regions import load_registry
from pipeline.real_estate_plan import (DEFAULT_REGISTRY, MAX_REGISTRY_BYTES,
    build_plan, load_plan_registry, main, validate_plan_registry, write_plan)

ROOT = Path(__file__).resolve().parents[1]
STAMP = '2026-09-19T19:08:00Z'
REGISTRY_SHA = '8000c758b3f32c702b87d69e2b11952c6615493703f20e07ec18f6d809bad57d'


def checked_registry():
    return json.loads((ROOT / DEFAULT_REGISTRY).read_bytes())


def one_region():
    value = checked_registry()
    value['regions'] = value['regions'][:1]
    value['audit']['active_regions'] = 1
    value['audit']['regions_sha256'] = sha256(canonical_bytes(value['regions']))
    return value


def local_registry(tmp_path, value=None):
    path = tmp_path / DEFAULT_REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value or one_region()))
    return path


def test_official_public_subset_is_pinned_and_cannot_be_used_as_collection_registry():
    value, digest = load_plan_registry(ROOT)
    assert digest == REGISTRY_SHA
    assert len(value['regions']) == 256
    assert value['source']['sha256'] == '44b96f4a86ad102057463a05aae8842f1d706d3e9e69d2dfc409023bf75ca56b'
    assert value['source']['registry_sha256'] == 'e1421b849ec1490e494f0b48a9fee63656528ba4624ae7557250c53106dbe671'
    assert value['audit']['regions_sha256'] == 'f7ebfeddf73a2ba3d60085efc4f3a42c9fbdd374939d28c4f4a21aa6c3eee81b'
    assert value['audit']['source_rows'] == 53387
    assert value['retrieved_at'] == '2026-09-19T17:09:59Z'
    # Preserve official names including the actual trailing spaces in three rows.
    assert next(r for r in value['regions'] if r['lawd_code'] == '41192')['name'].endswith(' ')
    assert 'legal_dongs' not in value and 'transactions' not in value
    with pytest.raises(RealEstateError, match='invalid_legal_registry'):
        load_registry(ROOT / DEFAULT_REGISTRY)


def test_full_plan_has_31232_unique_jobs_and_preserves_collector_month_order():
    value, digest = load_plan_registry(ROOT)
    first = build_plan(value, registry_sha256=digest, as_of=STAMP)
    second = build_plan(deepcopy(value), registry_sha256=digest, as_of=STAMP)
    assert canonical_bytes(first) == canonical_bytes(second)
    assert first['job_count'] == 31232 == len(first['jobs'])
    assert first['months'] == month_sequence(STAMP, 61)
    assert first['months'][:3] == ['202608', '202609', '202607']
    assert first['months'][-1] == '202109'
    assert len({(r['trade_type'], r['lawd_code'], r['deal_month']) for r in first['jobs']}) == 31232
    assert first['source_calls'] == first['reserved_calls'] == 0
    assert first['is_collection_checkpoint'] is first['data_acquired'] is False
    assert first['job_status'] == 'planned_not_requested'
    assert first['historical_coverage'] == value['historical_coverage']


@pytest.mark.parametrize('stamp,count', [
    ('2025-12-31T14:59:59Z', 1), ('2025-12-31T15:00:00Z', 61),
    ('2024-02-29T15:00:00Z', 2), ('2026-09-20T00:00:00Z', 5),
])
def test_kst_month_boundary_matches_actual_collection_contract(stamp, count):
    plan = build_plan(one_region(), registry_sha256=REGISTRY_SHA, as_of=stamp, months=count)
    assert plan['months'] == month_sequence(stamp, count)
    assert plan['job_count'] == 2 * count


@pytest.mark.parametrize('count', [0, 122, -1, True, 1.5, '61'])
def test_invalid_count_is_not_a_collectable_plan(count):
    with pytest.raises(RealEstateError, match='invalid_month_count'):
        build_plan(one_region(), registry_sha256=REGISTRY_SHA, as_of=STAMP, months=count)


def test_ten_year_offline_plan_is_a_plan_not_collected_data():
    plan=build_plan(one_region(),registry_sha256=REGISTRY_SHA,as_of=STAMP,months=121)
    assert plan['months'][-1]=='201609' and plan['job_count']==242
    assert plan['source_calls']==0 and plan['data_acquired'] is False


@pytest.mark.parametrize('damage,code', [
    (lambda v: v.update(kind='molit-legal-region-registry'), 'invalid_plan_registry'),
    (lambda v: v.update(planning_only=False), 'invalid_plan_registry'),
    (lambda v: v.update(credentials={'fixture': 'not-a-real-key'}), 'invalid_plan_registry'),
    (lambda v: v['source'].update(download_url='https://example.test/codes'), 'invalid_plan_source'),
    (lambda v: v['source'].update(sha256='not-a-hash'), 'invalid_plan_source'),
    (lambda v: v['rights'].update(license_statement='unknown'), 'invalid_plan_rights'),
    (lambda v: v['regions'][0].update(lawd_code='00000'), 'invalid_plan_region'),
    (lambda v: v['regions'][0].update(legal_code='1111010000'), 'invalid_plan_region'),
    (lambda v: v['regions'][0].update(name='fixture=secret'), 'invalid_plan_region'),
    (lambda v: v['regions'][0].update(name='지역\n이름'), 'invalid_plan_region'),
    (lambda v: v['regions'][0].update(email='fixture@example.invalid'), 'invalid_plan_region'),
    (lambda v: v['audit'].update(regions_sha256='0' * 64), 'invalid_plan_registry_audit'),
    (lambda v: v['regions'].append(deepcopy(v['regions'][0])), 'duplicate_plan_region'),
])
def test_registry_rejects_ambiguous_identity_secret_fields_and_modified_audit(damage, code):
    value = one_region()
    damage(value)
    with pytest.raises(RealEstateError, match=code):
        validate_plan_registry(value)


def test_unsorted_and_duplicate_json_keys_are_rejected(tmp_path):
    value = checked_registry()
    value['regions'].reverse()
    with pytest.raises(RealEstateError, match='unsorted_plan_regions'):
        validate_plan_registry(value)
    path = local_registry(tmp_path)
    path.write_bytes(path.read_bytes().replace(b'{', b'{"kind":"unexpected",', 1))
    with pytest.raises(RealEstateError, match='invalid_plan_registry_json'):
        load_plan_registry(tmp_path)


@pytest.mark.parametrize('path', ['../config/x.json', 'config/../config/x.json',
    '/config/x.json', 'config\\x.json', '.local/private.json', 'config/x.txt', 'C:/config/x.json'])
def test_registry_input_stays_in_config_without_traversal(tmp_path, path):
    with pytest.raises(RealEstateError, match='invalid_plan_path'):
        load_plan_registry(tmp_path, path)


def test_oversize_registry_is_rejected_before_read(tmp_path):
    path = local_registry(tmp_path)
    with path.open('wb') as stream:
        stream.truncate(MAX_REGISTRY_BYTES + 1)
    with pytest.raises(RealEstateError, match='invalid_plan_registry_size'):
        load_plan_registry(tmp_path)


def test_linked_input_and_parent_are_rejected_without_reading_target(tmp_path, monkeypatch):
    path = local_registry(tmp_path)
    original = Path.is_symlink
    for linked in (path, path.parent):
        monkeypatch.setattr(Path, 'is_symlink', lambda self: self == linked or original(self))
        with pytest.raises(RealEstateError, match='linked_path'):
            load_plan_registry(tmp_path)


def test_actual_symlink_is_rejected_when_host_allows_creation(tmp_path):
    path = local_registry(tmp_path)
    linked = tmp_path / 'config' / 'linked.json'
    try:
        linked.symlink_to(path)
    except OSError:
        pytest.skip('This host does not permit creating symlinks')
    with pytest.raises(RealEstateError, match='linked_path'):
        load_plan_registry(tmp_path, 'config/linked.json')


def test_offline_plan_succeeds_at_14gb_without_keys_network_or_sqlite(tmp_path, monkeypatch):
    local_registry(tmp_path)
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=14 * 1000**3))
    def forbidden(*args, **kwargs):
        raise AssertionError('Offline planning must not initialize external services')
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(http.client, 'HTTPSConnection', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    monkeypatch.setattr('pipeline.real_estate_fetch.read_key', forbidden)
    result = write_plan(repo_root=tmp_path, as_of=STAMP)
    payload = (tmp_path / '.local/property-plan-ci/plan.json').read_bytes()
    assert sha256(payload) == result['sha256']
    assert result['jobs'] == 122 and result['source_calls'] == result['reserved_calls'] == 0
    assert len(list((tmp_path / '.local').rglob('*.*'))) == 1
    # Real data collection retains its existing 30 GiB lower bound on the same disk.
    with pytest.raises(RealEstateError, match='disk_reserve'):
        Collector(tmp_path / '.local/collection', {'regions': []})


def test_existing_output_and_public_output_are_never_overwritten(tmp_path):
    local_registry(tmp_path)
    result = write_plan(repo_root=tmp_path, as_of=STAMP)
    path = tmp_path / '.local/property-plan-ci/plan.json'
    original = path.read_bytes()
    with pytest.raises(RealEstateError, match='plan_output_exists'):
        write_plan(repo_root=tmp_path, as_of=STAMP)
    assert path.read_bytes() == original and sha256(original) == result['sha256']
    for invalid in ('public/plan.json', '.local/../public/plan.json', 'dist/plan.json'):
        with pytest.raises(RealEstateError, match='invalid_plan_path'):
            write_plan(repo_root=tmp_path, output=invalid, as_of=STAMP)
    assert not (tmp_path / 'public').exists()


def test_failed_atomic_commit_does_not_publish_a_partial_plan(tmp_path, monkeypatch):
    local_registry(tmp_path)
    def fail(*args, **kwargs):
        raise OSError('simulated_commit_failure')
    monkeypatch.setattr(os, 'link', fail)
    with pytest.raises(OSError):
        write_plan(repo_root=tmp_path, as_of=STAMP)
    assert list((tmp_path / '.local/property-plan-ci').iterdir()) == []


def test_small_plan_disk_reserve_and_cli_failure_do_not_print_supplied_input(tmp_path, monkeypatch, capsys):
    local_registry(tmp_path)
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    with pytest.raises(RealEstateError, match='plan_disk_reserve'):
        write_plan(repo_root=tmp_path, as_of=STAMP)
    assert not (tmp_path / '.local').exists()
    with pytest.raises(SystemExit):
        main(['--repo-root', str(tmp_path), '--regions', 'config/fixture-value-secret.json'])
    output = capsys.readouterr()
    assert 'fixture-value-secret' not in output.err + output.out
    assert output.err == 'real_estate_plan: invalid_plan_registry_size\n'


def test_checked_in_workflow_step_runs_with_default_input_and_no_package_install(tmp_path, monkeypatch, capsys):
    workflow = (ROOT / '.github/workflows/property-plan.yml').read_text(encoding='utf-8')
    assert "default: 'config/molit-legal-region-registry.json'" in workflow
    assert 'pip install' not in workflow and 'secrets.' not in workflow
    assert 'schedule:' not in workflow and 'contents: read' in workflow
    # Execute the exact checked-in Python heredoc, with only the two dispatch inputs.
    body = workflow.split("          python - <<'PY'\n", 1)[1].split('\n          PY', 1)[0]
    script = '\n'.join(line[10:] for line in body.splitlines())
    local_registry(tmp_path, checked_registry())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('REGISTRY_INPUT', DEFAULT_REGISTRY)
    monkeypatch.setenv('MONTHS_INPUT', '61')
    def forbidden(*args, **kwargs):
        raise AssertionError('Planning workflow attempted a source call or database access')
    monkeypatch.setattr(http.client, 'HTTPSConnection', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    exec(compile(script, '<property-plan-workflow>', 'exec'), {})
    report = json.loads(capsys.readouterr().out)
    assert report['jobs'] == 31232 and report['planning_only'] is True
    assert report['source_calls'] == 0
    plan = json.loads((tmp_path / '.local/property-plan-ci/plan.json').read_bytes())
    assert plan['months'] == month_sequence(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 61)
