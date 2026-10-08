"""Publish a private retention policy only after verified Pages publication.

This reads generated assets and writes one small policy. It never deletes payloads,
changes a public deployment, renews policy from inside the retention runner, or
selects a rollback/source merely because a directory looks recent.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from .pages_retention import (
    CONFIG_LIFETIME, MAX_CONFIG_BYTES, SHA, STAGE, checked_file,
    file_sha, inside, load_policy, no_links, regular_tree, timestamp,
    verify_public_runtime,
)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def stamp(instant):
    return datetime.fromtimestamp(instant, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def private_file(path):
    path = no_links(path)
    body = checked_file(path, MAX_CONFIG_BYTES)
    info = path.stat()
    if os.name != 'nt' and (stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.geteuid()):
        raise ValueError('private inventory/policy must be owner-only and owned by runner')
    return body


def scoped_roots(root, service):
    root, service = no_links(root), no_links(service)
    if (service.name != 'korea-replay' or root == service or not root.is_relative_to(service)
            or not root.is_dir() or not (service/'shared/data').is_dir()):
        raise ValueError('invalid project/service roots')
    return root, service


def read_inventory(path):
    path = no_links(path)
    body = private_file(path)
    value = json.loads(body)
    required = {'schema_version', 'project_root', 'service_root', 'current', 'previous',
                'source', 'data', 'active', 'presence_locks'}
    if not isinstance(value, dict) or set(value) != required or value['schema_version'] != 1:
        raise ValueError('invalid publication inventory')
    root, service = scoped_roots(value['project_root'], value['service_root'])
    if not path.is_relative_to(service):
        raise ValueError('inventory outside service')
    for role in ('current', 'previous', 'source', 'data', 'active'):
        refs = value[role]
        if (not isinstance(refs, list) or len(refs) > 30 or (role != 'active' and not refs)
                or any(not isinstance(ref, str) for ref in refs) or len(set(refs)) != len(refs)):
            raise ValueError('invalid inventory role')
    if len(value['current']) != 1 or len(value['previous']) != 1:
        raise ValueError('one current and one previous application required')
    if value['current'] == value['previous']:
        raise ValueError('current cannot be its own rollback')
    locks = value['presence_locks']
    if not isinstance(locks, list) or len(locks) > 18 or len(set(locks)) != len(locks):
        raise ValueError('invalid presence lock inventory')
    for item in locks:
        lock = inside(root, item)
        if not lock.name.endswith('.lock'):
            raise ValueError('invalid presence lock path')
    return value, root, service, digest(body)


def verified_stage(root, relative):
    """Recheck generated body bytes; receipts alone cannot prove a restorable base."""
    receipt_path = inside(root, relative)
    stage = receipt_path.parent
    if (receipt_path.name != 'receipt.json' or stage.parent != root/'.local/pages-release'
            or not STAGE.fullmatch(stage.name)):
        raise ValueError('expected scoped Pages receipt')
    receipt_body = checked_file(receipt_path, 32768)
    receipt = json.loads(receipt_body)
    if not isinstance(receipt, dict):
        raise ValueError('invalid stage receipt body')
    project = receipt.get('project')
    artifact = receipt.get('artifact_sha256')
    if (receipt.get('schema_version') != 1 or receipt.get('platform') != 'cloudflare-pages'
            or receipt.get('complete') is not True or project not in ('korea-replay', 'korea-replay-data')
            or not isinstance(artifact, str) or not SHA.fullmatch(artifact)
            or not stage.name.startswith(f'{project}-{artifact[:16]}-')):
        raise ValueError('invalid complete stage receipt')
    client = no_links(stage/'client')
    if not client.is_dir() or (stage/'payload-retired.json').exists():
        raise ValueError('protected stage payload missing or retired')
    manifest_body = checked_file(stage/'asset-manifest.json', 8*1024*1024)
    if digest(manifest_body) != receipt.get('manifest_sha256'):
        raise ValueError('protected stage manifest hash mismatch')
    entries = json.loads(manifest_body)
    if not isinstance(entries, list) or not entries or len(entries) > 20000:
        raise ValueError('invalid stage manifest')
    expected = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('invalid stage manifest entry')
        name, size, sha = entry.get('target'), entry.get('bytes'), entry.get('sha256')
        inside(client, name)
        if (name in expected or type(size) is not int or not 0 <= size <= 25*1024*1024
                or not isinstance(sha, str) or not SHA.fullmatch(sha)):
            raise ValueError('invalid stage manifest entry')
        expected[name] = (size, sha)
    if receipt.get('files') != len(expected) or receipt.get('bytes') != sum(x[0] for x in expected.values()):
        raise ValueError('stage receipt file count/size mismatch')
    actual = {}
    for path in regular_tree(client):
        name = path.relative_to(client).as_posix()
        if name not in expected or len(actual) >= 20000:
            raise ValueError('unexpected protected stage file')
        actual[name] = (path.stat().st_size, file_sha(path))
    if actual != expected:
        raise ValueError('protected stage payload hash mismatch')
    return {'path': relative, 'sha256': digest(receipt_body)}, receipt


def verification(root, ref, receipt, origin=None, production=False):
    stage = inside(root, ref['path']).parent
    if production:
        paths = [stage/'verified-production.json']
        expected_origin = 'https://korea-replay.pages.dev'
    else:
        if origin is not None:
            match = re.fullmatch(r'https://([a-f0-9]{8})\.(korea-replay(?:-data)?)\.pages\.dev', origin)
            if not match or match[2] != receipt['project']:
                raise ValueError('invalid verified immutable origin')
            paths = [stage/f'verified-preview-{match[1]}.json']
        else:
            paths = sorted(stage.glob('verified-preview-????????.json'))
            if len(paths) > 30:
                raise ValueError('verification proof scan limit')
        expected_origin = origin
    if not paths:
        raise ValueError('verified publication proof required')
    for path in paths:
        body = checked_file(path, MAX_CONFIG_BYTES)
        proof = json.loads(body)
        if not isinstance(proof, dict):
            raise ValueError('invalid publication proof body')
        samples = proof.get('samples', [])
        if (proof.get('schema_version') != 1 or proof.get('passed') is not True
                or proof.get('project') != receipt['project']
                or proof.get('artifact_sha256') != receipt['artifact_sha256']
                or proof.get('release_id') != receipt.get('release_id')
                or not isinstance(samples, list) or not samples
                or any(not isinstance(x, dict) or x.get('passed') is not True for x in samples)):
            raise ValueError('invalid publication proof')
        if expected_origin is not None and proof.get('origin') != expected_origin:
            raise ValueError('publication origin mismatch')
        if production:
            if (proof.get('origin_kind') != 'production'
                    or proof.get('snapshot_origin') != receipt.get('policy', {}).get('snapshot_origin')):
                raise ValueError('production snapshot proof mismatch')
        elif (proof.get('origin_kind') != 'immutable'
                or not re.fullmatch(r'https://[a-f0-9]{8}\.'+re.escape(receipt['project'])+r'\.pages\.dev', str(proof.get('origin')))):
            raise ValueError('invalid immutable publication proof')
        required = {'/data/__pages-verification-missing__.json'}
        if receipt['project'] == 'korea-replay':
            required |= {'/api/v2/runtime', '/', '/data/catalog.json'}
        else:
            required.add(receipt.get('atlas_manifest', {}).get('path'))
        if not required.issubset({x.get('path') for x in samples}):
            raise ValueError('publication proof lacks runtime/catalog/404 checks')
        return {'path': path.relative_to(root).as_posix(), 'sha256': digest(body)}
    raise ValueError('verified publication proof required')


def build_policy(value, root, service, now, runtime_check=verify_public_runtime):
    cache, proofs = {}, {}
    def stage(path):
        if path not in cache:
            cache[path] = verified_stage(root, path)
        return cache[path]
    def prove(path, origin=None, production=False):
        ref, receipt = stage(path)
        proof = verification(root, ref, receipt, origin, production)
        proofs[proof['path']] = proof
    def data_for(receipt):
        pin = receipt.get('policy', {}).get('data')
        if not isinstance(pin, dict):
            raise ValueError('application data pin missing')
        matches = []
        for path in value['data']:
            ref, candidate = stage(path)
            atlas = candidate.get('atlas_manifest', {})
            if candidate['project'] != 'korea-replay-data':
                raise ValueError('data inventory is not a data stage')
            if atlas.get('path') == pin.get('manifest_path') and atlas.get('sha256') == pin.get('manifest_sha256'):
                prove(path, pin.get('origin'))
                matches.append(ref)
        if len(matches) != 1:
            raise ValueError('one verified local data body required for application')
        return matches[0]
    protected = {key: [] for key in ('source', 'current', 'previous', 'active')}
    for role in protected:
        for path in value[role]:
            ref, receipt = stage(path)
            if role in ('current', 'previous') and receipt['project'] != 'korea-replay':
                raise ValueError('current/previous must be application receipts')
            if role in ('current', 'previous') and not receipt.get('policy', {}).get('snapshot_origin'):
                raise ValueError('current/previous production snapshot required')
            if role != 'active':
                prove(path, production=bool(receipt.get('policy', {}).get('snapshot_origin')))
            protected[role].append(ref)
            if role != 'source' and receipt['project'] == 'korea-replay':
                protected[role].append(data_for(receipt))
            elif role == 'active' and receipt['project'] == 'korea-replay-data':
                # A complete but not yet uploaded data candidate is protected locally.
                pass
        protected[role] = sorted({r['path']: r for r in protected[role]}.values(), key=lambda r:r['path'])
    current = stage(value['current'][0])[1]
    pin = {'url':'https://korea-replay.pages.dev/api/v2/runtime',
           'artifact_sha256':current['artifact_sha256'],
           'data_manifest_sha256':current['policy']['data']['manifest_sha256']}
    runtime_check(pin)
    marker = root/'.local/pages-release/publication.lock'
    presence = {str(marker)} | {str(inside(root, p)) for p in value['presence_locks']}
    locks = [{'path':str(service/'shared/data/bulk-work.lock'), 'mode':'flock'}]
    locks += [{'path':p, 'mode':'presence'} for p in sorted(presence)]
    return {'schema_version':1, 'project':'korea-replay', 'project_root':str(root), 'service_root':str(service),
            'generated_at':stamp(now), 'expires_at':stamp(now+CONFIG_LIFETIME),
            'protected':protected, 'public_runtime':pin, 'activity_locks':locks,
            'publication_evidence':sorted(proofs.values(), key=lambda p:p['path'])}


def atomic_policy(path, value, root, now):
    path = no_links(path)
    if not path.parent.is_dir():
        raise ValueError('policy parent missing')
    if os.name != 'nt' and (path.parent.stat().st_uid != os.geteuid() or path.parent.stat().st_mode & 0o022):
        raise ValueError('policy parent must be owned by runner and not group/world writable')
    if path.exists():
        private_file(path)
    body = (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()
    if len(body) > MAX_CONFIG_BYTES:
        raise ValueError('policy size limit')
    descriptor, temporary = tempfile.mkstemp(prefix='.retention-policy-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(body); output.flush(); os.fsync(output.fileno())
        load_policy(Path(temporary), root, now)
        no_links(path)
        os.replace(temporary, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()  # Only this invocation's small temporary policy.
    return digest(body)


def refresh_locked(inventory, policy, *, root, service, now=None,
                   owned_presence=None, runtime_check=verify_public_runtime):
    """Caller holds bulk-work.lock for the entire publication and refresh."""
    instant = int(time.time() if now is None else now)
    value, found_root, found_service, inventory_hash = read_inventory(inventory)
    if (root, service) != (found_root, found_service) or no_links(policy) != service/'shared/retention-policy.json':
        raise ValueError('publication policy scope mismatch')
    markers = {root/'.local/pages-release/publication.lock'} | {inside(root,p) for p in value['presence_locks']}
    def check_presence():
        for marker in markers:
            if no_links(marker).exists() and marker != owned_presence:
                raise ValueError('producer presence lock exists')
    check_presence()
    fresh = build_policy(value, root, service, instant, runtime_check)
    if digest(private_file(inventory)) != inventory_hash:
        raise ValueError('publication inventory changed during verification')
    for ref in fresh['publication_evidence']:
        if digest(checked_file(inside(root,ref['path']),MAX_CONFIG_BYTES)) != ref['sha256']:
            raise ValueError('publication proof changed during verification')
    target = no_links(policy)
    if target.exists():
        old = json.loads(private_file(target))
        keys = set(fresh)-{'generated_at','expires_at'}
        if (isinstance(old,dict) and all(old.get(key)==fresh[key] for key in keys)
                and timestamp(old.get('expires_at'))-instant >= CONFIG_LIFETIME//2):
            _, _, old_hash = load_policy(target,root,instant)
            return {'status':'unchanged','policy_sha256':old_hash}
    # Recheck public pointer immediately before commit after local body verification.
    runtime_check(fresh['public_runtime'])
    check_presence()
    if digest(private_file(inventory)) != inventory_hash:
        raise ValueError('publication inventory changed before policy commit')
    for ref in fresh['publication_evidence']:
        if digest(checked_file(inside(root,ref['path']),MAX_CONFIG_BYTES)) != ref['sha256']:
            raise ValueError('publication proof changed before policy commit')
    result = atomic_policy(target, fresh, root, instant)
    return {'status':'written','policy_sha256':result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory',type=Path,required=True)
    parser.add_argument('--policy',type=Path,required=True)
    args = parser.parse_args()
    try:
        _, root, service, _ = read_inventory(args.inventory)
        from .generated_work import work_lock
        with work_lock(service/'shared/data/bulk-work.lock'):
            print(json.dumps(refresh_locked(args.inventory,args.policy,root=root,service=service)))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2,json.dumps({'status':'blocked','reason':str(error)[:160]})+'\n')


if __name__ == '__main__':
    main()
