"""Materialize a verified private building-parts delta without activating it.

Only eight changed GLBs and a rewritten hierarchy are copied to a new immutable
public-data namespace. The flat candidate catalog is written under .local;
catalog pointers, source meshes and deployment are outside this module's scope.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath

from .building_parts_delta import require, sha256, verify_tree_delta
from .building_parts_national import encoded, memory_usage
from .hierarchy import replacement_budget

VERSION = 'building-parts-publication-1'
DEFAULT_TREE_SHA = '949777005051630f0be2ffef4649be629002be2d68f8ec4af742267e35bd63b9'
DEFAULT_AUDIT_SHA = 'cdf56b1ccc6f7760d65ad526544dae94603dc4a1876075780b409f9b8b69216e'
STORAGE_LIMIT = 256 * 1024 ** 2
MEMORY_LIMIT = 512 * 1024 ** 2
DISK_RESERVE = 30 * 1024 ** 3
HASH = re.compile(r'^[a-f0-9]{64}$')


def local_reference(manifest, uri, roots):
    """Relative URIs may reuse public ancestors, but cannot escape approved roots."""
    require(isinstance(uri, str) and bool(uri) and not PurePosixPath(uri).is_absolute()
            and not any(c in uri for c in ('\\', ':', '?', '#', '%'))
            and all(ord(c) >= 32 and ord(c) != 127 for c in uri), 'Invalid local content URI')
    resolved = (Path(manifest).parent / uri).resolve()
    require(any(resolved.is_relative_to(root) for root in roots), 'Content reference escaped approved roots')
    require(resolved.suffix == '.glb', 'Only embedded hierarchy GLB content is accepted')
    return resolved


def public_asset_path(url, public):
    require(isinstance(url, str) and url.startswith('/data/') and
            not any(c in url for c in ('\\', ':', '?', '#', '%')) and
            all(ord(c) >= 32 and ord(c) != 127 for c in url), 'Invalid public data URL')
    path = (public / url.removeprefix('/data/')).resolve()
    require(path.is_relative_to(public), 'Asset URL escaped public data')
    return path


def checked_bytes(path, expected=None, size=None, maximum=16 * 1024 ** 2):
    require(path.is_file() and path.stat().st_size <= maximum, 'Missing or oversized bounded input')
    raw = path.read_bytes()
    if expected is not None:
        require(isinstance(expected, str) and HASH.fullmatch(expected) is not None and
                sha256(raw) == expected, 'Input SHA-256 mismatch: ' + str(path))
    if size is not None: require(type(size) is int and len(raw) == size, 'Input byte length mismatch')
    return raw


class Artifacts:
    """One aggregate budget for the new public and local artifact folders."""
    def __init__(self, public, work, *, disk_reserve=DISK_RESERVE):
        self.public, self.work = public.resolve(), work.resolve()
        self.disk_reserve = disk_reserve; self.peak = 0
        for root in (self.public, self.work): root.mkdir(parents=True, exist_ok=True)
        self.check()

    def check(self, additional=0):
        rss, peak = memory_usage(); self.peak = max(self.peak, peak)
        require(max(rss, peak) < MEMORY_LIMIT, '512 MiB process memory budget exceeded')
        total = sum(p.stat().st_size for root in (self.public, self.work) for p in root.rglob('*') if p.is_file())
        require(total + additional <= STORAGE_LIMIT, '256 MiB aggregate artifact budget exceeded')
        require(shutil.disk_usage(self.work).free - additional >= self.disk_reserve, '30 GiB disk reserve would be crossed')
        return total

    def write(self, path, payload):
        path = path.resolve()
        require(any(path.is_relative_to(root) for root in (self.public, self.work)), 'Output escaped immutable folders')
        if path.exists():
            require(path.is_file() and path.read_bytes() == payload, 'Refusing to overwrite an existing artifact')
            return path
        self.check(len(payload)); path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream: stream.write(payload)
        return path

    def json(self, name, value):
        return self.write(self.work / name, encoded(value))


def walk_content(tree, manifest, roots):
    """Bounded inline tree traversal, with a unique immutable reference per node."""
    references = {}; nodes = []; stack = [(tree['root'], 'root')]
    while stack:
        node, name = stack.pop(); nodes.append(node)
        require(len(nodes) <= 20_000 and isinstance(node, dict), 'Hierarchy exceeds bounded node count')
        require('contents' not in node, 'Multiple contents are not supported')
        if node.get('content'):
            require(set(node['content']) == {'uri'}, 'Unexpected content fields')
            path = local_reference(manifest, node['content']['uri'], roots)
            require(path not in references, 'Duplicate hierarchy content reference')
            e = node.get('extras', {})
            require(e.get('lod_role') in ('detail', 'representative_subset') and HASH.fullmatch(str(e.get('sha256', '')))
                    and type(e.get('bytes')) is int and e['bytes'] > 0 and type(e.get('feature_count')) is int
                    and e['feature_count'] > 0, 'Invalid GLB content metadata')
            references[path] = {'node': node, 'name': name, 'sha256': e['sha256'], 'bytes': e['bytes'],
                                'feature_count': e['feature_count'], 'role': e['lod_role']}
        stack.extend((child, name + '/' + str(i)) for i, child in enumerate(node.get('children', [])))
    return references, nodes


def validation_reuse(audit, core, patches):
    """Require complete, matching core and extension audit coverage for the delta."""
    expected = {p['path']: p['sha256'] for p in patches}
    require(len(expected) == len(patches), 'Duplicate delta GLB patches')
    reports = core.get('reports', [])
    require(core.get('checked') == len(patches) and core.get('errors') == 0 and core.get('warnings') == 0
            and len(reports) == len(patches) and
            {r.get('path'): r.get('sha256') for r in reports} == expected and
            all(r.get('errors') == 0 and r.get('warnings') == 0 for r in reports),
            'Khronos validation does not cover exactly the unchanged delta GLBs')
    for patch in patches:
        meta = patch.get('metadata_audit', {})
        require(meta.get('sha256') == patch['sha256'] and meta.get('bytes') == patch['bytes']
                and meta.get('features') == patch.get('after_features')
                and meta.get('source_identity_rows') == meta.get('features')
                and patch.get('unaffected_features_bit_exact') is True
                and patch.get('unaffected_coordinate_conversion_error_m') == 0,
                'Delta extension/identity audit is incomplete or mismatched')
    require(audit.get('passed') is True and audit.get('private_candidate') is True
            and audit.get('public_assets_changed') is False and audit.get('unaffected_features_bit_exact') is True,
            'Private delta audit has not passed')
    return {'passed': True, 'new_glbs': len(patches), 'core_errors': 0, 'core_warnings': 0,
            'extension_and_identity_audit_reused': True, 'delta_glbs_byte_identical': True,
            'scope': 'Exact SHA-256 set of the previously validated delta GLBs; no whole-country geometry reread.'}


def rewrite_tree(private, private_path, destination, patch_paths, reusable, public):
    """Rewrite only content URIs and private-only root publication flags."""
    result = copy.deepcopy(private)
    refs, _ = walk_content(result, private_path, (private_path.parent, public))
    require(set(refs) == set(patch_paths) | set(reusable), 'Private hierarchy dependency closure mismatch')
    for source, row in refs.items():
        target = patch_paths.get(source, source)
        require(target.is_relative_to(public), 'Publication reference escaped public data')
        row['node']['content']['uri'] = Path(os.path.relpath(target, destination.parent)).as_posix()
    extras = result.setdefault('extras', {})
    extras.pop('private_candidate', None); extras.pop('public_assets_changed', None)
    extras['building_parts_publication_version'] = VERSION
    # Compare every node after restoring URIs. This includes IDs/counts, LOD,
    # bounds, errors and transforms; GLB geometry is copied without decoding.
    restored = copy.deepcopy(result)
    reverse = {new: old for old, new in patch_paths.items()}
    old_refs, _ = walk_content(private, private_path, (private_path.parent, public))
    final_refs, _ = walk_content(restored, destination, (public,))
    for target, row in final_refs.items():
        source = reverse.get(target, target)
        require(source in old_refs, 'Unexpected publication dependency')
        row['node']['content']['uri'] = old_refs[source]['node']['content']['uri']
    require(restored['root'] == private['root'], 'Publication changed hierarchy node data')
    return result


def hierarchy_nodes(source, final, source_path, final_path, public):
    """Stable structural IDs for later subtree splitting, not invented source IDs."""
    rows = []; stack = [(source['root'], final['root'], 'root', None)]
    while stack:
        old, new, name, parent = stack.pop()
        require(old.get('transform') == new.get('transform'), 'Node manifest transform changed')
        require(len(old.get('children', [])) == len(new.get('children', [])), 'Node manifest topology changed')
        row = {'node_id': name, 'parent_id': parent,
            'child_ids': [name + '/' + str(i) for i in range(len(new.get('children', [])))],
            'local_transform': new.get('transform'), 'local_transform_sha256': sha256(encoded(new.get('transform'))),
            'transform_matches_source': True, 'geometric_error': new['geometricError'],
            'bounding_region': new['boundingVolume']['region'], 'content_url': None, 'source_content_url': None}
        for node, path, key in ((old, source_path, 'source_content_url'), (new, final_path, 'content_url')):
            if node.get('content'):
                content = local_reference(path, node['content']['uri'], (public,))
                row[key] = '/data/' + content.relative_to(public).as_posix()
        rows.append(row)
        stack.extend((a, b, name + '/' + str(i), name) for i, (a, b) in
                     reversed(list(enumerate(zip(old.get('children', []), new.get('children', []))))))
    return {'schema_version': 1, 'node_id_policy': 'stable_inline_child_index_path',
        'transform_semantics': 'Column-major local transform; null means identity. Identical ancestry proves unchanged world placement.',
        'source_tree_sha256': sha256(encoded(source)), 'published_tree_sha256': sha256(encoded(final)), 'nodes': rows}


def publish(candidate_folder, input_catalog, *, repo, expected_tree_sha256, expected_audit_sha256,
            disk_reserve=DISK_RESERVE):
    repo = Path(repo).resolve(); public = (repo / 'public/data').resolve()
    candidate = Path(candidate_folder).resolve(); input_catalog = Path(input_catalog).resolve()
    require(public.is_relative_to(repo) and candidate.is_relative_to(repo / '.local')
            and input_catalog.is_relative_to(repo / '.local'), 'Inputs must be existing project-local candidates')
    pointer_paths = [repo / 'public/catalog.json', public / 'catalog.json']
    pointer_pins = {p: sha256(p.read_bytes()) if p.is_file() else None for p in pointer_paths}
    raw_audit = checked_bytes(candidate / 'audit.json', expected_audit_sha256)
    raw_tree = checked_bytes(candidate / 'tileset.json', expected_tree_sha256)
    raw_inputs = checked_bytes(candidate / 'inputs.json')
    raw_reuse = checked_bytes(candidate / 'reuse.json')
    raw_core = checked_bytes(candidate / 'gltf-core-validation.json')
    raw_catalog = checked_bytes(input_catalog)
    audit, private, inputs, reused, core, catalog = map(json.loads,
        (raw_audit, raw_tree, raw_inputs, raw_reuse, raw_core, raw_catalog))
    require(catalog.get('schema_version') == 1 and isinstance(catalog.get('assets'), list), 'Expected flat v1 source catalog')
    ids = [a['id'] for a in catalog['assets']]
    require(len(ids) == len(set(ids)), 'Duplicate catalog asset IDs')
    roots = [a for a in catalog['assets'] if a['id'] == 'buildings-korea-retiled']
    require(len(roots) == 1 and roots[0]['format'] == '3d-tiles', 'Expected exactly one national building root')
    asset = roots[0]
    source_path = public_asset_path(asset['url'], public)
    raw_source = checked_bytes(source_path, asset['sha256'], asset['byte_length'])
    original = json.loads(raw_source)
    require(audit['tileset_sha256'] == expected_tree_sha256 and audit['source_root_sha256'] == asset['sha256']
            and inputs['root_sha256'] == asset['sha256'] and inputs['version'] == audit['version'],
            'Candidate does not derive from the source catalog hierarchy')
    require(asset['count'] == audit['logical_source_building_count'], 'Source logical building count mismatch')
    patches = audit['patches']; require(bool(patches) and len(patches) <= 64, 'Invalid bounded delta patch count')
    reuse_validation = validation_reuse(audit, core, patches)
    private_refs, _ = walk_content(private, candidate / 'tileset.json', (candidate, public))
    source_refs, _ = walk_content(original, source_path, (public,))
    checked_patches = {}; source_changes = {}; remaining = {}
    for patch in patches:
        part = local_reference(candidate / 'tileset.json', patch['path'], (candidate,))
        require(part.parent == candidate / 'glb' and part not in checked_patches, 'Unexpected or duplicate delta GLB path')
        source = (repo / patch['source_path']).resolve()
        require(source.is_relative_to(public) and source in source_refs and source not in source_changes,
                'Delta source does not name one unique public GLB')
        before = source_refs[source]; after = private_refs.get(part)
        require(before['role'] == 'detail' and before['sha256'] == patch['source_sha256']
                and before['bytes'] == patch['source_bytes'] and before['feature_count'] == patch['before_features'],
                'Delta source descriptor differs from the pinned hierarchy')
        require(after and after['role'] == 'detail' and after['sha256'] == patch['sha256']
                and after['bytes'] == patch['bytes'] and after['feature_count'] == patch['after_features'],
                'Delta output descriptor differs from metadata audit')
        checked_patches[part] = patch; source_changes[source] = part
    require(len(reused) == len({r['path'] for r in reused}), 'Duplicate reused GLB records')
    for r in reused:
        path = (repo / r['path']).resolve()
        require(path.is_relative_to(public) and path in source_refs and path not in source_changes,
                'Unexpected reuse reference')
        before, after = source_refs[path], private_refs.get(path)
        require(after and before['sha256'] == after['sha256'] == r['sha256']
                and before['bytes'] == after['bytes'] == r['bytes'] and before['role'] == after['role'] == r['role']
                and before['feature_count'] == after['feature_count'], 'Reused content descriptor changed')
        require(path.is_file() and path.stat().st_size == r['bytes'], 'Reused public GLB missing or size changed')
        remaining[path] = r
    require(set(private_refs) == set(checked_patches) | set(remaining)
            and set(source_refs) == set(source_changes) | set(remaining), 'Incomplete or foreign GLB dependency closure')
    structure = verify_tree_delta(original, private, source_path, candidate / 'tileset.json', source_changes)
    require(structure == audit['structure'], 'Structural proof differs from the approved delta audit')
    features = sum(r['feature_count'] for r in private_refs.values() if r['role'] == 'detail')
    removed = [i for p in patches for i in p['removed_parent_ids']]
    added = [i for p in patches for i in p['added_part_ids']]
    require(len(removed) == len(set(removed)) == audit['removed_parents'] and
            len(added) == len(set(added)) == audit['added_parts'] and not (set(removed) & set(added)),
            'Replacement source IDs are duplicated or incomplete')
    require(features == audit['detailed_render_features'] == asset['count'] + len(added) - len(removed)
            and private['extras']['detail_count'] == features
            and private['extras']['logical_source_building_count'] == asset['count'], 'Render/logical count contract mismatch')
    require(audit['changed_detail_glbs'] == len(patches) and audit['unchanged_glb_references'] == len(remaining)
            and audit['unchanged_lod_glbs'] == sum(r['role'] == 'representative_subset' for r in remaining.values()),
            'Audit GLB totals differ from complete dependency closure')
    signature = {'version': VERSION, 'source_catalog_sha256': sha256(raw_catalog),
        'private_tree_sha256': expected_tree_sha256, 'private_audit_sha256': expected_audit_sha256,
        'private_inputs_sha256': sha256(raw_inputs), 'private_reuse_sha256': sha256(raw_reuse),
        'private_core_validation_sha256': sha256(raw_core)}
    fingerprint = sha256(encoded(signature))[:20]
    destination = public / 'building-parts' / fingerprint
    work = repo / '.local/building-parts-publication' / fingerprint
    require(destination.resolve().is_relative_to(public) and work.resolve().is_relative_to(repo / '.local'),
            'Immutable output folder escaped the project')
    artifacts = Artifacts(destination, work, disk_reserve=disk_reserve)
    artifacts.json('signature.json', signature)
    mapping = {p: (destination / 'glb' / p.name).resolve() for p in checked_patches}
    require(len(set(mapping.values())) == len(mapping), 'Delta filename collision')
    for part, patch in sorted(checked_patches.items()):
        raw = checked_bytes(part, patch['sha256'], patch['bytes'], maximum=4 * 1024 ** 2)
        target = mapping[part]; artifacts.write(target, raw)
        # The checkpoint records the actual destination bytes, not just the plan.
        copied = checked_bytes(target, patch['sha256'], patch['bytes'], maximum=4 * 1024 ** 2)
        artifacts.json('checkpoints/' + patch['sha256'][:32] + '.json', {
            'source': part.relative_to(repo).as_posix(), 'url': '/data/' + target.relative_to(public).as_posix(),
            'sha256': sha256(copied), 'byte_length': len(copied), 'byte_identical': copied == raw})
    final = rewrite_tree(private, candidate / 'tileset.json', destination / 'tileset.json', mapping, remaining, public)
    frontier = replacement_budget(final['root'])
    require(frontier == audit['replacement_frontier'], 'URI publication altered the replacement working-set budget')
    final_raw = encoded(final); artifacts.write(destination / 'tileset.json', final_raw)
    new_asset = {**asset, 'url': '/data/' + (destination / 'tileset.json').relative_to(public).as_posix(),
        'sha256': sha256(final_raw), 'bytes': len(final_raw), 'byte_length': len(final_raw),
        'count': features, 'feature_count': features, 'logical_source_building_count': asset['count'],
        'building_parts_parent_count': len(removed), 'building_parts_feature_count': len(added),
        'building_parts_publication_version': VERSION, 'geometric_error_policy': final['extras']['geometric_error_policy']}
    candidate_catalog = {**catalog, 'assets': [new_asset if a['id'] == asset['id'] else copy.deepcopy(a) for a in catalog['assets']]}
    require([a for a in candidate_catalog['assets'] if a['id'] != asset['id']] ==
            [a for a in catalog['assets'] if a['id'] != asset['id']], 'Unrelated catalog assets changed')
    catalog_raw = encoded(candidate_catalog); artifacts.write(work / 'catalog.json', catalog_raw)
    final_refs, _ = walk_content(final, destination / 'tileset.json', (public,))
    closure = [{'url': '/data/' + p.relative_to(public).as_posix(), 'sha256': r['sha256'], 'byte_length': r['bytes'],
                'feature_count': r['feature_count'], 'role': r['role'], 'reused': p in remaining}
               for p, r in sorted(final_refs.items())]
    artifacts.json('glb-dependencies.json', closure)
    node_inventory = hierarchy_nodes(original, final, source_path, destination / 'tileset.json', public)
    # Use the pinned source file hash, since its original JSON whitespace/order
    # need not match this module's canonical serialization.
    node_inventory['source_tree_sha256'] = asset['sha256']
    artifacts.json('hierarchy-nodes.json', node_inventory)
    artifacts.json('validation-reuse.json', {**reuse_validation, 'audit_path': (candidate / 'audit.json').relative_to(repo).as_posix(),
        'audit_sha256': expected_audit_sha256, 'core_report_path': (candidate / 'gltf-core-validation.json').relative_to(repo).as_posix(),
        'core_report_sha256': sha256(raw_core), 'candidate_inputs_sha256': sha256(raw_inputs)})
    require(checked_bytes(input_catalog) == raw_catalog and checked_bytes(source_path) == raw_source,
            'Source catalog or hierarchy changed during publication preparation')
    require(all((sha256(p.read_bytes()) if p.is_file() else None) == value for p, value in pointer_pins.items()),
            'Public catalog pointer changed during publication preparation')
    proof = {'version': VERSION, 'passed': True, 'remote_published': False, 'catalog_pointers_changed': False,
        'source_catalog': input_catalog.relative_to(repo).as_posix(), 'source_catalog_sha256': sha256(raw_catalog),
        'candidate_catalog': (work / 'catalog.json').relative_to(repo).as_posix(), 'candidate_catalog_sha256': sha256(catalog_raw),
        'source_root_sha256': asset['sha256'], 'private_tree_sha256': expected_tree_sha256,
        'private_audit_sha256': expected_audit_sha256, 'public_tree_url': new_asset['url'], 'public_tree_sha256': sha256(final_raw),
        'new_public_files': len(mapping) + 1, 'new_public_bytes': sum(p['bytes'] for p in patches) + len(final_raw),
        'new_glbs': len(mapping), 'reused_glbs': len(remaining), 'all_glb_references': len(final_refs),
        'reused_lod_glbs': sum(r['role'] == 'representative_subset' for r in remaining.values()),
        'removed_parents': len(removed), 'added_parts': len(added), 'detailed_render_features': features,
        'logical_source_building_count': asset['count'], 'unchanged_other_assets': len(catalog['assets']) - 1,
        'private_to_public_nodes_unchanged': True, 'glbs_byte_identical_to_approved_delta': True,
        'hierarchy_node_manifest': 'hierarchy-nodes.json', 'hierarchy_nodes': len(node_inventory['nodes']),
        'hierarchy_node_manifest_sha256': sha256(encoded(node_inventory)),
        'source_to_delta_structure': structure, 'replacement_frontier': frontier,
        'catalog_pointer_hashes': {p.relative_to(repo).as_posix(): value for p, value in pointer_pins.items()},
        'limitations': ['Prepared immutable public files and a local catalog; no public pointer or remote deployment was activated.',
            'Unchanged nationwide GLBs were checked by existing tree hashes, dependency metadata and file sizes, not fully rehashed.',
            'Khronos and extension validation are reused only for the exact copied delta hashes.',
            'Source geometry, ground elevations and heights retain the accuracy limits of the approved candidate.',
            'The input release_id is preserved in this flat preparation catalog; static_release must derive the final public release ID.']}
    artifacts.json('publication-proof.json', proof)
    if not (work / 'resources.json').exists():
        artifacts.json('resources.json', {'artifact_bytes_before_resource_report': artifacts.check(),
            'process_peak_bytes': artifacts.peak, 'disk_free_bytes': shutil.disk_usage(work).free,
            'disk_reserve_bytes': disk_reserve, 'network_bytes': 0,
            'aggregate_storage_limit': STORAGE_LIMIT, 'process_memory_limit': MEMORY_LIMIT})
    return work / 'catalog.json', proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, default=Path('.local/building-parts-delta/6057f4f7c4c008d4ccd7'))
    parser.add_argument('--catalog', type=Path, default=Path('.local/performance-20260917/catalog.json'))
    parser.add_argument('--expected-tree-sha256', default=DEFAULT_TREE_SHA)
    parser.add_argument('--expected-audit-sha256', default=DEFAULT_AUDIT_SHA)
    args = parser.parse_args()
    catalog, proof = publish(args.candidate, args.catalog, repo=Path.cwd(),
        expected_tree_sha256=args.expected_tree_sha256, expected_audit_sha256=args.expected_audit_sha256)
    print(json.dumps({k: proof[k] for k in ('candidate_catalog', 'candidate_catalog_sha256', 'public_tree_url',
                                           'public_tree_sha256', 'new_public_files', 'new_public_bytes', 'reused_glbs')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
