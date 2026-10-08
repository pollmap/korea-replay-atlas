from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from pipeline.pages_retention import (MAX_LOG_BYTES,bounded_log,exclusive_lock,load_policy,scheduled_retire)

NOW=100000
ARTIFACT='a'*64
DATA='d'*64


def stamp(value):return datetime.fromtimestamp(value,timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def stage(root,letter):
    folder=root/f'.local/pages-release/korea-replay-{letter*16}-candidate';client=folder/'client';client.mkdir(parents=True)
    target=client/'index.html';target.write_bytes(b'generated')
    entries=json.dumps([{'target':'index.html','bytes':9,'sha256':hashlib.sha256(b'generated').hexdigest()}]).encode()
    (folder/'asset-manifest.json').write_bytes(entries)
    receipt=folder/'receipt.json'
    receipt.write_text(json.dumps({'complete':True,'platform':'cloudflare-pages','project':'korea-replay',
        'artifact_sha256':letter*64,'policy':{'data':{'manifest_sha256':DATA}},'manifest_sha256':hashlib.sha256(entries).hexdigest()}))
    for path in (client,target,receipt):os.utime(path,(100,100))
    return receipt


@pytest.fixture
def setup(tmp_path):
    service=tmp_path/'korea-replay';root=service/'workspaces/project';root.mkdir(parents=True)
    current=stage(root,'a');previous=stage(root,'b');old=stage(root,'c');active=stage(root,'d')
    shared=service/'shared/data';shared.mkdir(parents=True)
    def ref(path):return {'path':path.relative_to(root).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    value={'schema_version':1,'project':'korea-replay','project_root':str(root),'service_root':str(service),
        'generated_at':stamp(NOW-60),'expires_at':stamp(NOW+3600),
        'protected':{'source':[ref(current)],'current':[ref(current)],'previous':[ref(previous)],'active':[ref(active)]},
        'public_runtime':{'url':'https://korea-replay.pages.dev/api/v2/runtime','artifact_sha256':ARTIFACT,'data_manifest_sha256':DATA},
        'activity_locks':[{'path':str(shared/'bulk-work.lock'),'mode':'flock'},
                          {'path':str(root/'.local/pages-release/publication.lock'),'mode':'presence'}]}
    config=service/'private-retention.json';config.write_text(json.dumps(value))
    return root,config,value,current,previous,old,active


def run(setup,**options):
    root,config,*_=setup
    return scheduled_retire(root,config,now=NOW,runtime_check=lambda pin:None,**options)


def test_scheduled_dry_run_apply_noop_and_preserved_references(setup):
    root,_,_,current,previous,old,active=setup
    assert len(run(setup)['retired'])==1 and (old.parent/'client/index.html').exists()
    result=run(setup,apply=True)
    assert len(result['retired'])==1
    assert not (old.parent/'client').exists() and old.exists()
    assert all((receipt.parent/'client/index.html').exists() for receipt in (current,previous,active))
    files={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    assert run(setup,apply=True)['retired']==[]
    assert files=={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize('change',[{'expires_at':stamp(NOW)}, {'generated_at':stamp(NOW+1)},
                                  {'expires_at':stamp(NOW+86400)}, {'protected':{'current':[]}},
                                  {'service_root':'/'}])
def test_stale_incomplete_or_unscoped_policy_fails_closed(setup,change):
    _,config,value,_,_,old,_=setup;value.update(change);config.write_text(json.dumps(value))
    with pytest.raises(ValueError):run(setup,apply=True)
    assert (old.parent/'client/index.html').exists()


def test_runtime_pin_must_match_current_receipt(setup):
    _,config,value,_,_,old,_=setup;value['public_runtime']['artifact_sha256']='f'*64;config.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='current protected'):run(setup,apply=True)
    assert (old.parent/'client').exists()


def test_runtime_failure_or_change_stops_before_delete(setup):
    root,config,_,_,_,old,_=setup
    def mismatch(pin):raise ValueError('runtime changed')
    with pytest.raises(ValueError,match='runtime changed'):
        scheduled_retire(root,config,now=NOW,apply=True,runtime_check=mismatch)
    assert (old.parent/'client').exists()


def test_changed_protected_receipt_is_never_accepted(setup):
    _,_,_,current,_,old,_=setup;current.write_text('{}')
    with pytest.raises(ValueError,match='hash mismatch'):run(setup,apply=True)
    assert (old.parent/'client').exists()


def test_presence_lock_and_collector_lock_prevent_cleanup(setup):
    _,_,value,_,_,old,_=setup
    presence=Path(value['activity_locks'][1]['path']);presence.write_text('producer')
    with pytest.raises(ValueError,match='presence'):run(setup,apply=True)
    presence.unlink()
    with exclusive_lock(Path(value['activity_locks'][0]['path'])):
        with pytest.raises(ValueError,match='already running'):run(setup,apply=True)
    assert (old.parent/'client').exists()
    assert run(setup,apply=True)['retired']


def test_duplicate_scheduler_and_config_change_fail_closed(setup):
    root,config,value,_,_,old,_=setup
    state=root/'.local/retention';state.mkdir()
    with exclusive_lock(state/'retention.lock'):
        with pytest.raises(ValueError,match='already running'):run(setup,apply=True)
    def change(pin):
        value['expires_at']=stamp(NOW+300);config.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='changed during'):
        scheduled_retire(root,config,now=NOW,apply=True,runtime_check=change)
    assert (old.parent/'client').exists()


def test_logs_are_bounded_and_do_not_follow_links(tmp_path):
    log=tmp_path/'retention.jsonl'
    log.write_bytes(b'x'*(MAX_LOG_BYTES*2)+b'\n')
    bounded_log(log,{'status':'noop'})
    assert log.stat().st_size<=MAX_LOG_BYTES and log.with_suffix('.jsonl.1').stat().st_size<=MAX_LOG_BYTES
    other=tmp_path/'personal.txt';other.write_text('keep');log.unlink()
    try:log.symlink_to(other)
    except OSError:pytest.skip('symlink privileges unavailable')
    with pytest.raises(ValueError,match='linked'):bounded_log(log,{'status':'noop'})
    assert other.read_text()=='keep'


def test_cli_dry_run_without_network_using_local_http_not_allowed(setup):
    root,config,value,_,_,old,_=setup
    value['public_runtime']['url']='http://127.0.0.1:1/runtime'
    value['generated_at']=stamp(time.time()-60);value['expires_at']=stamp(time.time()+3600);config.write_text(json.dumps(value))
    result=subprocess.run([sys.executable,'-m','pipeline.pages_retention','--root',str(root),'--config',str(config),'--apply'],
                          text=True,capture_output=True)
    assert result.returncode==2 and 'unexpected runtime endpoint' in result.stderr
    assert (old.parent/'client').exists()


def test_actual_cli_apply_and_repeat_with_verified_runtime_response(setup,monkeypatch,capsys):
    from pipeline import pages_retention
    root,config,_,_,_,old,_=setup
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return json.dumps({'schema_version':2,'platform':'cloudflare-pages','project':'korea-replay',
            'artifact_sha256':ARTIFACT,'data':{'manifest_sha256':DATA}}).encode()
    class Opener:
        def open(self,request,timeout):
            assert request.full_url=='https://korea-replay.pages.dev/api/v2/runtime' and timeout==10
            return Response()
    monkeypatch.setattr(pages_retention.urllib.request,'build_opener',lambda *args:Opener())
    monkeypatch.setattr(pages_retention.time,'time',lambda:NOW)
    monkeypatch.setattr(sys,'argv',['pages_retention','--root',str(root),'--config',str(config),'--apply'])
    pages_retention.main();first=json.loads(capsys.readouterr().out)
    assert len(first['retired'])==1 and not (old.parent/'client').exists()
    pages_retention.main();second=json.loads(capsys.readouterr().out)
    assert second['retired']==[]
