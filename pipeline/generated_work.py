"""VPS Pages producer lock and verified-publication policy hook. No shell eval."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from .pages_retention import bounded_log, no_links

FD_ENV = 'KOREA_REPLAY_BULK_LOCK_FD'
PATH_ENV = 'KOREA_REPLAY_BULK_LOCK_PATH'


@contextmanager
def work_lock(path):
    """Reuse only a valid inherited descriptor for the identical lock inode.

    Child wrappers must not open and flock the same path a second time. The
    inherited open-file description owns the lock until its parent exits.
    """
    if os.name == 'nt':
        raise ValueError('generated-work wrapper requires VPS POSIX locks')
    import fcntl
    path = no_links(path)
    inherited = os.environ.get(FD_ENV)
    if inherited is not None:
        if not inherited.isdigit() or int(inherited) < 3 or os.environ.get(PATH_ENV) != str(path):
            raise ValueError('invalid inherited project lock')
        descriptor = os.dup(int(inherited))
    else:
        descriptor = os.open(path, os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    try:
        opened, named = os.fstat(descriptor), path.stat()
        if (not stat.S_ISREG(opened.st_mode) or (opened.st_dev,opened.st_ino)!=(named.st_dev,named.st_ino)):
            raise ValueError('project lock inode mismatch')
        try: fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as error: raise ValueError('retention or producer already running') from error
        # Closing the final descriptor releases the OS lock. An explicit LOCK_UN
        # would also release a still-running child's inherited lock description.
        yield descriptor
    finally: os.close(descriptor)


def run_work(root, service, command, *, inventory=None, policy=None, runner=subprocess.run, refresh=None):
    from .pages_retention_policy import scoped_roots, refresh_locked
    root, service = scoped_roots(root,service)
    if not command or bool(inventory)!=bool(policy):
        raise ValueError('command and paired publication inventory/policy required')
    refresh = refresh_locked if refresh is None else refresh
    state = no_links(root/'.local/retention'); state.mkdir(exist_ok=True)
    marker = no_links(root/'.local/pages-release/publication.lock')
    lock = service/'shared/data/bulk-work.lock'
    with work_lock(lock) as descriptor:
        identity = None
        if inventory:
            # The marker survives crashes, command failure or failed verification.
            # Only this invocation's identical inode is removed after policy commit.
            with marker.open('x') as output:
                output.write(json.dumps({'schema_version':1,'pid':os.getpid(),'started_at':int(time.time())}))
                output.flush();os.fsync(output.fileno())
            identity = (marker.stat().st_dev,marker.stat().st_ino)
        environment = dict(os.environ)
        environment.update({FD_ENV:str(descriptor),PATH_ENV:str(lock)})
        status = 'failed'; code = 2
        try:
            completed = runner(command,cwd=root,env=environment,pass_fds=(descriptor,),check=False)
            code = completed.returncode
            if code:
                return code
            if inventory:
                refresh(inventory,policy,root=root,service=service,owned_presence=marker)
                no_links(marker)
                if not marker.is_file() or (marker.stat().st_dev,marker.stat().st_ino)!=identity:
                    raise ValueError('publication marker changed')
                marker.unlink()
            status = 'ok'
            return 0
        finally:
            bounded_log(state/'generated-work.jsonl', {'at':int(time.time()),'status':status,
                'command_exit':code,'publication':bool(inventory)})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--service-root',type=Path,required=True)
    parser.add_argument('--inventory',type=Path)
    parser.add_argument('--policy',type=Path)
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    try:
        raise SystemExit(run_work(args.root,args.service_root,command,inventory=args.inventory,policy=args.policy))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2,json.dumps({'status':'blocked','reason':str(error)[:160]})+'\n')


if __name__=='__main__':
    main()
