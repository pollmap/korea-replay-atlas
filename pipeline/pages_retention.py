"""Retire expired generated Pages payloads, retaining small publication evidence.

The caller supplies current, rollback, source and active candidate receipt paths.
No raw data, Git checkout, server DB, public deployment or Docker volume is read
or deleted. Dry-run is the default. Metadata + generated-tree identity is checked
again immediately before removal; unknown files and symbolic links fail closed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time

STAGE = re.compile(r'korea-replay(?:-data)?-[a-f0-9]{16}-(?:candidate|production-[a-f0-9]{8})$')

def regular_tree(root):
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(base) / name
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError('linked generated tree')
        for name in files:
            path = Path(base) / name
            if not path.is_file(): raise ValueError('non-regular generated file')
            yield path


def retire(root, protected, *, apply=False, now=None, ttl=86400):
    root = Path(root).absolute()
    if ttl < 86400 or not protected: raise ValueError('explicit retention references required')
    parent = root / '.local/pages-release'
    for path in (root, root / '.local', parent):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('linked stage root')
    keep = set()
    for entry in protected:
        path = Path(entry).absolute()
        if path.name != 'receipt.json' or path.parent.parent != parent or not path.is_file():
            raise ValueError('invalid protected receipt')
        keep.add(path.parent.name)
    instant = time.time() if now is None else now
    result = {'applied': apply, 'protected': sorted(keep), 'retired': [], 'skipped': []}
    for stage in sorted(parent.iterdir()):
        if not STAGE.fullmatch(stage.name) or stage.name in keep: continue
        if stage.is_symlink(): raise ValueError('linked stage')
        client = stage / 'client'
        if not client.exists(): continue
        receipt_path, manifest_path = stage/'receipt.json', stage/'asset-manifest.json'
        if not receipt_path.is_file() or not manifest_path.is_file():
            result['skipped'].append(stage.name); continue
        if max(receipt_path.stat().st_mtime,client.stat().st_mtime)>instant-ttl:
            result['skipped'].append(stage.name); continue
        receipt_bytes=receipt_path.read_bytes(); receipt=json.loads(receipt_bytes)
        manifest_bytes=manifest_path.read_bytes()
        if (receipt.get('complete') is not True or receipt.get('platform')!='cloudflare-pages'
            or receipt.get('manifest_sha256')!=hashlib.sha256(manifest_bytes).hexdigest()):
            result['skipped'].append(stage.name); continue
        entries=json.loads(manifest_bytes)
        expected={entry['target']:entry['bytes'] for entry in entries}
        actual={p.relative_to(client).as_posix():p.stat().st_size for p in regular_tree(client)}
        if actual!=expected: result['skipped'].append(stage.name); continue
        logical=sum(actual.values())
        if apply:
            if receipt_path.read_bytes()!=receipt_bytes or manifest_path.read_bytes()!=manifest_bytes:
                raise ValueError('stage changed before retirement')
            # Preserve receipts/manifests for investigation; only generated payload is retired.
            shutil.rmtree(client)
            (stage/'payload-retired.json').write_text(json.dumps({'at':instant,'logical_bytes':logical,
                'manifest_sha256':receipt['manifest_sha256'],'reason':'expired unreferenced generated candidate'}))
        result['retired'].append({'stage':stage.name,'logical_bytes':logical})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--protect',type=Path,action='append',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(retire(args.root,args.protect,apply=args.apply),indent=2))

if __name__=='__main__': main()
