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
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import stat
import sys
import urllib.request

STAGE = re.compile(r'korea-replay(?:-data)?-[a-f0-9]{16}-(?:candidate|production-[a-f0-9]{8})$')
SHA = re.compile('[a-f0-9]{64}$')
MAX_CONFIG_BYTES = 64 * 1024
MAX_LOG_BYTES = 1024 * 1024
CONFIG_LIFETIME = 86400


def no_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('linked retention path')
    return path.resolve(strict=False)


def checked_file(path, maximum):
    path = no_links(path)
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('invalid retention metadata')
    return path.read_bytes()


def file_sha(path):
    result=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''): result.update(block)
    return result.hexdigest()

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


def retire(root, protected, *, apply=False, now=None, ttl=86400, pre_delete=None):
    root = no_links(root)
    if ttl < 86400 or not protected: raise ValueError('explicit retention references required')
    parent = root / '.local/pages-release'
    for path in (root, root / '.local', parent):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('linked stage root')
    keep = set()
    for entry in protected:
        path = no_links(entry)
        if path.name != 'receipt.json' or path.parent.parent != parent or not path.is_file():
            raise ValueError('invalid protected receipt')
        keep.add(path.parent.name)
    instant = time.time() if now is None else now
    result = {'applied': apply, 'protected': sorted(keep), 'retired': [], 'skipped': []}
    stages=sorted(parent.iterdir())
    if len(stages)>2000: raise ValueError('retention stage scan limit')
    for stage in stages:
        if not STAGE.fullmatch(stage.name) or stage.name in keep: continue
        if stage.is_symlink(): raise ValueError('linked stage')
        if not stage.is_dir(): continue
        client = stage / 'client'
        if not client.exists(): continue
        no_links(client)
        receipt_path, manifest_path = stage/'receipt.json', stage/'asset-manifest.json'
        if not receipt_path.is_file() or not manifest_path.is_file():
            result['skipped'].append(stage.name); continue
        if max(receipt_path.stat().st_mtime,client.stat().st_mtime)>instant-ttl:
            result['skipped'].append(stage.name); continue
        receipt_bytes=checked_file(receipt_path,32768); receipt=json.loads(receipt_bytes)
        manifest_bytes=checked_file(manifest_path,8*1024*1024)
        if (receipt.get('complete') is not True or receipt.get('platform')!='cloudflare-pages'
            or receipt.get('manifest_sha256')!=hashlib.sha256(manifest_bytes).hexdigest()):
            result['skipped'].append(stage.name); continue
        entries=json.loads(manifest_bytes)
        if not isinstance(entries,list) or len(entries)>20000: raise ValueError('invalid stage manifest')
        expected={}
        for entry in entries:
            name=entry.get('target'); size=entry.get('bytes'); digest=entry.get('sha256')
            if (not isinstance(name,str) or '\\' in name or name.startswith('/')
                or any(part in ('','.','..') for part in name.split('/')) or name in expected
                or type(size) is not int or size<0 or size>25*1024*1024
                or not isinstance(digest,str) or not SHA.fullmatch(digest)):
                raise ValueError('invalid stage manifest entry')
            expected[name]=(size,digest)
        files=list(regular_tree(client))
        if any(path.stat().st_mtime>instant-ttl for path in files):
            result['skipped'].append(stage.name); continue
        actual={p.relative_to(client).as_posix():(p.stat().st_size,file_sha(p)) for p in files}
        if actual!=expected: result['skipped'].append(stage.name); continue
        logical=sum(size for size,_ in actual.values())
        if apply:
            if pre_delete: pre_delete()
            marker=no_links(stage/'payload-retired.json')
            if marker.exists(): raise ValueError('populated stage already has retirement marker')
            if receipt_path.read_bytes()!=receipt_bytes or manifest_path.read_bytes()!=manifest_bytes:
                raise ValueError('stage changed before retirement')
            # Catch tree replacement, late additions and same-size edits after inspection.
            rechecked={p.relative_to(client).as_posix():(p.stat().st_size,file_sha(p)) for p in regular_tree(client)}
            if rechecked!=expected: raise ValueError('stage payload changed before retirement')
            # Preserve receipts/manifests for investigation; only generated payload is retired.
            shutil.rmtree(client)
            with marker.open('x') as handle:
                handle.write(json.dumps({'at':instant,'logical_bytes':logical,
                    'manifest_sha256':receipt['manifest_sha256'],'reason':'expired unreferenced generated candidate'}))
        result['retired'].append({'stage':stage.name,'logical_bytes':logical})
    return result


def timestamp(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',value):
        raise ValueError('invalid config time')
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def inside(root, value):
    if not isinstance(value,str) or not value or '\\' in value:
        raise ValueError('invalid scoped path')
    path=Path(value)
    if path.is_absolute() or any(part in ('','.','..') for part in value.split('/')):
        raise ValueError('invalid scoped path')
    path=no_links(root/path)
    if not path.is_relative_to(root): raise ValueError('path outside project')
    return path


def load_policy(path, root, now):
    """Private, expiring references must be refreshed by a verified publication."""
    body=checked_file(path,MAX_CONFIG_BYTES); policy=json.loads(body)
    if not isinstance(policy,dict): raise ValueError('invalid retention policy')
    if policy.get('schema_version')!=1 or policy.get('project')!='korea-replay' or policy.get('project_root')!=str(root):
        raise ValueError('retention policy identity mismatch')
    generated=timestamp(policy.get('generated_at')); expires=timestamp(policy.get('expires_at'))
    if not generated<=now<expires or expires-generated>CONFIG_LIFETIME or now-generated>CONFIG_LIFETIME:
        raise ValueError('retention policy expired or future dated')
    protected=policy.get('protected')
    if not isinstance(protected,dict) or set(protected)!={'source','current','previous','active'}:
        raise ValueError('all retention reference roles required')
    receipts=[]; current=[]
    for role,refs in protected.items():
        if not isinstance(refs,list) or len(refs)>30 or (role!='active' and not refs):
            raise ValueError('invalid retention role')
        for ref in refs:
            if not isinstance(ref,dict) or set(ref)!={'path','sha256'} or not SHA.fullmatch(str(ref['sha256'])):
                raise ValueError('invalid pinned receipt')
            receipt=inside(root,ref['path'])
            if receipt.name!='receipt.json': raise ValueError('not a receipt reference')
            stage_parent=root/'.local/pages-release'
            source_parent=root/'.local/deploy/bundles'
            if receipt.parent.parent==stage_parent and STAGE.fullmatch(receipt.parent.name):
                if not no_links(receipt.parent/'client').is_dir(): raise ValueError('protected stage payload missing')
                receipts.append(receipt)
            elif role=='source' and receipt.is_relative_to(source_parent):
                pass  # This source is verified but outside the only deletion scope.
            else: raise ValueError('receipt outside allowed stage roots')
            receipt_body=checked_file(receipt,32768)
            if hashlib.sha256(receipt_body).hexdigest()!=ref['sha256']:
                raise ValueError('protected receipt hash mismatch')
            parsed=json.loads(receipt_body)
            if not isinstance(parsed,dict): raise ValueError('invalid protected receipt body')
            if receipt.parent.parent==stage_parent and (not isinstance(parsed,dict) or parsed.get('complete') is not True or parsed.get('platform')!='cloudflare-pages'):
                raise ValueError('protected stage is incomplete')
            if role=='current' and parsed.get('project')=='korea-replay': current.append(parsed)
    if len(current)!=1 or not receipts: raise ValueError('one current application receipt required')
    runtime=policy.get('public_runtime')
    if not isinstance(runtime,dict) or set(runtime)!={'url','artifact_sha256','data_manifest_sha256'}:
        raise ValueError('public runtime pin required')
    if runtime['url']!='https://korea-replay.pages.dev/api/v2/runtime':
        raise ValueError('unexpected runtime endpoint')
    if any(not SHA.fullmatch(str(runtime[key])) for key in ('artifact_sha256','data_manifest_sha256')):
        raise ValueError('invalid runtime hash')
    current_policy=current[0].get('policy')
    if not isinstance(current_policy,dict) or not isinstance(current_policy.get('data'),dict):
        raise ValueError('current application data pin missing')
    if (current[0].get('artifact_sha256')!=runtime['artifact_sha256']
        or current_policy['data'].get('manifest_sha256')!=runtime['data_manifest_sha256']):
        raise ValueError('runtime pin is not the current protected application')
    locks=policy.get('activity_locks')
    if not isinstance(locks,list) or not locks or len(locks)>20: raise ValueError('activity lock references required')
    seen_locks=set()
    for lock in locks:
        if not isinstance(lock,dict) or set(lock)!={'path','mode'} or lock['mode'] not in ('flock','presence'):
            raise ValueError('invalid activity lock')
        if not isinstance(lock['path'],str): raise ValueError('invalid activity lock path')
        target=Path(lock['path'])
        # Shared collector locks may live outside this Git checkout, but must be
        # in this service's explicitly named directory, never arbitrary /tmp locks.
        service=Path(policy.get('service_root',''))
        if (not service.is_absolute() or service.name!='korea-replay' or not root.is_relative_to(service)
            or not target.is_absolute() or not target.is_relative_to(service)):
            raise ValueError('activity lock outside service')
        if target!=no_links(target) or target in seen_locks: raise ValueError('noncanonical or duplicate activity lock')
        seen_locks.add(target)
        if not target.name.endswith('.lock'): raise ValueError('invalid activity lock name')
    if {'path':str(service/'shared/data/bulk-work.lock'),'mode':'flock'} not in locks:
        raise ValueError('project bulk-work lock required')
    return policy,receipts,hashlib.sha256(body).hexdigest()


@contextmanager
def exclusive_lock(path):
    """Persistent lock inode, released by the OS; never steal/delete stale files."""
    path=no_links(path)
    if not path.parent.is_dir(): raise ValueError('lock parent missing')
    flags=os.O_RDWR|os.O_CREAT
    if hasattr(os,'O_NOFOLLOW'): flags|=os.O_NOFOLLOW
    descriptor=os.open(path,flags,0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode): raise ValueError('non-regular lock')
        if os.name=='nt':
            import msvcrt
            if os.fstat(descriptor).st_size==0: os.write(descriptor,b'0')
            os.lseek(descriptor,0,os.SEEK_SET)
            try: msvcrt.locking(descriptor,msvcrt.LK_NBLCK,1)
            except OSError as error: raise ValueError('retention or producer already running') from error
        else:
            import fcntl
            try: fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as error: raise ValueError('retention or producer already running') from error
        try: yield
        finally:
            if os.name=='nt':
                os.lseek(descriptor,0,os.SEEK_SET); msvcrt.locking(descriptor,msvcrt.LK_UNLCK,1)
            else: fcntl.flock(descriptor,fcntl.LOCK_UN)
    finally: os.close(descriptor)


def verify_public_runtime(pin):
    """Only the public read endpoint; no credentials, redirects or broad crawling."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs): return None
    request=urllib.request.Request(pin['url'],headers={'Accept':'application/json','User-Agent':'korea-replay-retention/1.0'})
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=10) as response:
        body=response.read(64*1024+1)
        if response.status!=200 or len(body)>64*1024: raise ValueError('runtime unavailable')
    value=json.loads(body)
    if (not isinstance(value,dict) or value.get('schema_version')!=2 or value.get('platform')!='cloudflare-pages' or value.get('project')!='korea-replay'
        or value.get('artifact_sha256')!=pin['artifact_sha256'] or not isinstance(value.get('data'),dict)
        or value['data'].get('manifest_sha256')!=pin['data_manifest_sha256']):
        raise ValueError('public runtime changed; refresh retention policy')


def bounded_log(path, entry):
    path=no_links(path); previous=path.with_suffix(path.suffix+'.1')
    no_links(previous)
    if previous.exists():
        if not previous.is_file(): raise ValueError('invalid log rotation target')
        if previous.stat().st_size>MAX_LOG_BYTES:
            with previous.open('rb') as handle:
                handle.seek(-MAX_LOG_BYTES,os.SEEK_END);tail=handle.read(MAX_LOG_BYTES)
            previous.write_bytes(tail.partition(b'\n')[2])
    body=(json.dumps(entry,sort_keys=True,separators=(',',':'))+'\n').encode()
    if len(body)>8192: raise ValueError('retention log record too large')
    if path.exists() and not path.is_file(): raise ValueError('invalid retention log')
    if path.exists() and path.stat().st_size+len(body)>MAX_LOG_BYTES:
        if previous.exists() and not previous.is_file(): raise ValueError('invalid log rotation target')
        if path.stat().st_size>MAX_LOG_BYTES:
            with path.open('rb') as handle:
                handle.seek(-MAX_LOG_BYTES,os.SEEK_END); tail=handle.read(MAX_LOG_BYTES)
            # Keep only complete recent lines if an older installation overgrew.
            previous.write_bytes(tail.partition(b'\n')[2]);path.write_bytes(b'')
        else: os.replace(path,previous)
    with path.open('ab') as handle: handle.write(body)


def scheduled_retire(root, config, *, apply=False, now=None, runtime_check=verify_public_runtime):
    root=no_links(root); instant=time.time() if now is None else now
    policy,receipts,config_hash=load_policy(config,root,instant)
    state_dir=no_links(root/'.local/retention')
    state_dir.mkdir(exist_ok=True)
    with exclusive_lock(state_dir/'retention.lock'), ExitStack() as locks:
        for item in sorted(policy['activity_locks'],key=lambda item:item['path']):
            path=no_links(item['path'])
            if item['mode']=='presence':
                if path.exists(): raise ValueError('producer presence lock exists')
            else:
                locks.enter_context(exclusive_lock(path))
        runtime_check(policy['public_runtime'])
        def recheck():
            _,_,fresh=load_policy(config,root,instant if now is not None else time.time())
            if fresh!=config_hash: raise ValueError('retention policy changed during execution')
            for item in policy['activity_locks']:
                if item['mode']=='presence' and no_links(item['path']).exists():
                    raise ValueError('producer started during retention')
        recheck()
        result=retire(root,receipts,apply=apply,now=instant,pre_delete=recheck)
        summary={'at':datetime.fromtimestamp(instant,timezone.utc).isoformat(),'config_sha256':config_hash,
                 'applied':apply,'retired_count':len(result['retired']),'skipped_count':len(result['skipped']),
                 'logical_bytes':sum(stage['logical_bytes'] for stage in result['retired'])}
        bounded_log(state_dir/'retention.jsonl',summary)
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--protect',type=Path,action='append')
    parser.add_argument('--config',type=Path,help='Private expiring references for scheduled execution')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--summary',action='store_true',help='Bounded scheduler output without per-stage lists')
    args=parser.parse_args()
    if bool(args.config)==bool(args.protect): parser.error('choose --config or --protect')
    try:
        result=scheduled_retire(args.root,args.config,apply=args.apply) if args.config else retire(args.root,args.protect,apply=args.apply)
        output={'status':'ok','applied':result['applied'],'retired_count':len(result['retired']),
                'skipped_count':len(result['skipped']),'logical_bytes':sum(stage['logical_bytes'] for stage in result['retired'])} if args.summary else result
        print(json.dumps(output,indent=2))
    except (ValueError,OSError,KeyError,TypeError) as error:
        # Avoid dumping private config paths or source bodies into scheduled logs.
        print(json.dumps({'status':'blocked','reason':str(error)[:200]}),file=sys.stderr)
        raise SystemExit(2) from None

if __name__=='__main__': main()
