from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pipeline import pages_retention_policy as publication
from pipeline.generated_work import FD_ENV, PATH_ENV, run_work, work_lock
from pipeline.pages_retention import load_policy, scheduled_retire

NOW=1_790_000_000
DATA_SHA='d'*64


def put(path,value,private=False):
    path.write_text(json.dumps(value),encoding='utf-8')
    if private:path.chmod(0o600)


def make_stage(root,letter,project='korea-replay',production=True):
    snapshot=letter*8
    suffix='production-'+snapshot if production else 'candidate'
    folder=root/f'.local/pages-release/{project}-{letter*16}-{suffix}'
    client=folder/'client';client.mkdir(parents=True)
    (client/'index.html').write_bytes(b'generated')
    entries=[{'target':'index.html','bytes':9,'sha256':hashlib.sha256(b'generated').hexdigest()}]
    put(folder/'asset-manifest.json',entries)
    data={'origin':'https://dddddddd.korea-replay-data.pages.dev','manifest_path':'/data/atlas/test/manifest.json','manifest_sha256':DATA_SHA}
    receipt={'schema_version':1,'complete':True,'platform':'cloudflare-pages','project':project,
        'artifact_sha256':letter*64,'release_id':'test-release','files':1,'bytes':9,
        'manifest_sha256':hashlib.sha256((folder/'asset-manifest.json').read_bytes()).hexdigest()}
    if project=='korea-replay':
        receipt['policy']={'snapshot_origin':f'https://{snapshot}.korea-replay.pages.dev' if production else None,'data':data}
        samples=['/api/v2/runtime','/','/data/catalog.json']
    else:
        receipt['atlas_manifest']={'path':data['manifest_path'],'sha256':DATA_SHA}
        samples=[data['manifest_path']]
    proof={'schema_version':1,'passed':True,'project':project,'artifact_sha256':letter*64,
        'release_id':'test-release','origin':f'https://{project}.pages.dev' if production else f'https://{snapshot}.{project}.pages.dev',
        'origin_kind':'production' if production else 'immutable','snapshot_origin':receipt.get('policy',{}).get('snapshot_origin'),
        'samples':[{'path':x,'passed':True} for x in samples+['/data/__pages-verification-missing__.json']]}
    put(folder/'receipt.json',receipt)
    put(folder/('verified-production.json' if production else f'verified-preview-{snapshot}.json'),proof)
    return (folder/'receipt.json').relative_to(root).as_posix()


@pytest.fixture
def context(tmp_path):
    service=tmp_path/'korea-replay';root=service/'workspaces/app';root.mkdir(parents=True)
    (service/'shared/data').mkdir(parents=True)
    current=make_stage(root,'a');previous=make_stage(root,'b');source=make_stage(root,'c')
    data=make_stage(root,'d',project='korea-replay-data',production=False)
    active=make_stage(root,'e',production=False)
    value={'schema_version':1,'project_root':str(root),'service_root':str(service),
        'current':[current],'previous':[previous],'source':[source], 'data':[data],
        'active':[active],'presence_locks':[]}
    inventory=service/'shared/publication-inventory.json';put(inventory,value,True)
    return root,service,inventory,service/'shared/retention-policy.json',value


def refresh(context,**kwargs):
    root,service,inventory,policy,_=context
    with work_lock(service/'shared/data/bulk-work.lock'):
        return publication.refresh_locked(inventory,policy,root=root,service=service,now=NOW,
            runtime_check=lambda pin:None,**kwargs)


def test_complete_verified_publication_builds_closure_and_noop(context):
    root,_,_,policy,value=context
    first=refresh(context);assert first['status']=='written'
    result,refs,sha=load_policy(policy,root,NOW)
    assert sha==first['policy_sha256']
    assert len(refs)==7
    assert {x['path'] for x in result['protected']['current']}==set(value['current']+value['data'])
    assert {x['path'] for x in result['protected']['previous']}==set(value['previous']+value['data'])
    assert {x['path'] for x in result['protected']['active']}==set(value['active']+value['data'])
    assert result['activity_locks'][1]['path'].endswith('/publication.lock')
    before=(policy.read_bytes(),policy.stat().st_mtime_ns)
    assert refresh(context)['status']=='unchanged'
    assert (policy.read_bytes(),policy.stat().st_mtime_ns)==before
    assert policy.stat().st_mode & 0o077==0


def test_expired_policy_refresh_requires_reverification(context):
    root,service,inventory,policy,_=context;refresh(context)
    old=policy.read_bytes()
    def unavailable(pin):raise ValueError('public unavailable')
    with work_lock(service/'shared/data/bulk-work.lock'):
        with pytest.raises(ValueError,match='public unavailable'):
            publication.refresh_locked(inventory,policy,root=root,service=service,now=NOW+86401,runtime_check=unavailable)
        assert policy.read_bytes()==old
        assert publication.refresh_locked(inventory,policy,root=root,service=service,now=NOW+86401,
            runtime_check=lambda pin:None)['status']=='written'
    load_policy(policy,root,NOW+86401)


@pytest.mark.parametrize('fault',['source_missing','same_size_edit','extra_file','proof_missing','wrong_artifact',
    'wrong_origin','missing_404','data_mismatch','data_origin_mismatch','data_duplicate','current_is_candidate'])
def test_invalid_or_missing_body_proof_and_data_preserve_old_policy(context,fault):
    root,_,inventory,policy,value=context;refresh(context);before=policy.read_bytes()
    source=root/value['source'][0];current=root/value['current'][0]
    proof=current.parent/'verified-production.json'
    if fault=='source_missing':(source.parent/'client/index.html').unlink()
    if fault=='same_size_edit':(source.parent/'client/index.html').write_bytes(b'corrupted')
    if fault=='extra_file':(current.parent/'client/unknown').write_text('new')
    if fault=='proof_missing':proof.unlink()
    if fault in ('wrong_artifact','wrong_origin','missing_404'):
        body=json.loads(proof.read_text())
        if fault=='wrong_artifact':body['artifact_sha256']='f'*64
        if fault=='wrong_origin':body['origin']='https://attacker.example'
        if fault=='missing_404':body['samples']=body['samples'][:-1]
        put(proof,body)
    if fault in ('data_mismatch','data_origin_mismatch'):
        body=json.loads(current.read_text())
        body['policy']['data']['manifest_sha256' if fault=='data_mismatch' else 'origin']='f'*64
        put(current,body)
    if fault=='data_duplicate':
        other=make_stage(root,'f',project='korea-replay-data',production=False)
        old=root/value['data'][0];new=root/other
        (new.parent/'verified-preview-dddddddd.json').write_bytes((old.parent/'verified-preview-dddddddd.json').read_bytes())
        body=json.loads((new.parent/'verified-preview-dddddddd.json').read_text());body['artifact_sha256']='f'*64
        put(new.parent/'verified-preview-dddddddd.json',body)
        value['data'].append(other);put(inventory,value,True)
    if fault=='current_is_candidate':value['current']=value['active'];put(inventory,value,True)
    with pytest.raises(ValueError):refresh(context)
    assert policy.read_bytes()==before
    assert not list(policy.parent.glob('.retention-policy-*.tmp'))


def test_policy_write_is_atomic_and_failure_does_not_truncate(context,monkeypatch):
    _,_,inventory,policy,value=context;refresh(context);before=policy.read_bytes()
    value['active']=[];put(inventory,value,True)
    def fail(*args):raise OSError('simulated replace failure')
    monkeypatch.setattr(publication.os,'replace',fail)
    with pytest.raises(OSError,match='replace failure'):refresh(context)
    assert policy.read_bytes()==before and not list(policy.parent.glob('.retention-policy-*.tmp'))


def test_presence_and_inventory_mutation_block_refresh(context):
    root,service,inventory,policy,value=context
    marker=root/'.local/pages-release/publication.lock';marker.write_text('active')
    with pytest.raises(ValueError,match='presence'):refresh(context)
    marker.unlink()
    def changed(pin):
        altered=deepcopy(value);altered['active']=[];put(inventory,altered,True)
    with work_lock(service/'shared/data/bulk-work.lock'):
        with pytest.raises(ValueError,match='inventory changed'):
            publication.refresh_locked(inventory,policy,root=root,service=service,now=NOW,runtime_check=changed)
    assert not policy.exists()


def test_owner_only_inventory_and_symlink_rejected(context):
    root,service,inventory,policy,value=context
    inventory.chmod(0o644)
    with pytest.raises(ValueError,match='owner-only'):refresh(context)
    inventory.chmod(0o600)
    current=root/value['current'][0];target=current.parent/'client/index.html';external=service/'other';external.write_text('generated')
    target.unlink();target.symlink_to(external)
    with pytest.raises(ValueError,match='linked'):refresh(context)
    assert external.read_text()=='generated' and not policy.exists()


def test_publication_wrapper_keeps_lock_during_command_and_refresh(context):
    root,service,inventory,policy,_=context
    marker=root/'.local/pages-release/publication.lock';seen=[]
    def runner(args,**options):
        assert marker.exists() and options['pass_fds']
        with pytest.raises(ValueError,match='already running'):
            with work_lock(service/'shared/data/bulk-work.lock'):pass
        seen.append(args)
        return subprocess.CompletedProcess(args,0)
    def hook(*args,**options):
        return publication.refresh_locked(*args,**options,now=NOW,runtime_check=lambda pin:None)
    assert run_work(root,service,['safe','literal;not-shell'],inventory=inventory,policy=policy,runner=runner,refresh=hook)==0
    assert seen==[['safe','literal;not-shell']] and not marker.exists()
    load_policy(policy,root,NOW)
    assert len((root/'.local/retention/generated-work.jsonl').read_text().splitlines())==1


@pytest.mark.parametrize('failure',['command','hook','marker_replaced'])
def test_failed_publication_keeps_presence_and_previous_policy(context,failure):
    root,service,inventory,policy,_=context;refresh(context);old=policy.read_bytes()
    marker=root/'.local/pages-release/publication.lock'
    def runner(args,**options):return subprocess.CompletedProcess(args,3 if failure=='command' else 0)
    def hook(*args,**options):
        if failure=='hook':raise ValueError('verification failed')
        replacement=marker.with_suffix('.new');replacement.write_text('replacement');os.replace(replacement,marker)
    if failure=='command':assert run_work(root,service,['x'],inventory=inventory,policy=policy,runner=runner,refresh=hook)==3
    else:
        with pytest.raises(ValueError):run_work(root,service,['x'],inventory=inventory,policy=policy,runner=runner,refresh=hook)
    assert marker.exists() and policy.read_bytes()==old
    with pytest.raises(ValueError,match='presence'):
        scheduled_retire(root,policy,now=NOW,apply=True,runtime_check=lambda pin:None)


def test_real_nested_wrapper_cli_reuses_lock_and_rejects_unrelated_descriptor(context):
    root,service,_,_,_=context
    repo=Path(__file__).resolve().parents[1]
    env=dict(os.environ,PYTHONPATH=str(repo));env.pop(FD_ENV,None);env.pop(PATH_ENV,None)
    nested=[sys.executable,'-m','pipeline.generated_work','--root',str(root),'--service-root',str(service),'--']
    code='from pathlib import Path; Path("nested-ok.txt").write_text("ok")'
    result=subprocess.run(nested+nested+[sys.executable,'-c',code],env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert (root/'nested-ok.txt').read_text()=='ok'
    with work_lock(service/'shared/data/bulk-work.lock'):
        blocked=subprocess.run(nested+[sys.executable,'-c','pass'],env=env,capture_output=True,text=True)
    assert blocked.returncode==2 and 'already running' in blocked.stderr
    unrelated=service/'unrelated.lock';unrelated.touch()
    with unrelated.open('r') as handle:
        invalid=dict(env);invalid.update({FD_ENV:str(handle.fileno()),PATH_ENV:str(service/'shared/data/bulk-work.lock')})
        result=subprocess.run(nested+[sys.executable,'-c','pass'],env=invalid,pass_fds=(handle.fileno(),),capture_output=True,text=True)
    assert result.returncode==2 and 'inode mismatch' in result.stderr


def test_standalone_cli_rejects_scope_before_network(context):
    root,_,inventory,policy,value=context
    value['source']=['../../unsafe/receipt.json'];put(inventory,value,True)
    result=subprocess.run([sys.executable,'-m','pipeline.pages_retention_policy','--inventory',str(inventory),'--policy',str(policy)],
        text=True,capture_output=True)
    assert result.returncode==2 and not policy.exists()


def test_lock_survives_until_final_inherited_descriptor_closes(context):
    _,service,*_=context
    lock=service/'shared/data/bulk-work.lock'
    with work_lock(lock) as descriptor:
        child_copy=os.dup(descriptor)
    try:
        with pytest.raises(ValueError,match='already running'):
            with work_lock(lock):pass
    finally:os.close(child_copy)
    with work_lock(lock):pass


def test_actual_command_and_policy_hook_finish_before_marker_clears(context):
    root,service,inventory,policy,_=context
    def hook(*args,**options):
        assert (root/'producer-finished.txt').read_text()=='complete'
        return publication.refresh_locked(*args,**options,now=NOW,runtime_check=lambda pin:None)
    command=[sys.executable,'-c','from pathlib import Path; Path("producer-finished.txt").write_text("complete")']
    assert run_work(root,service,command,inventory=inventory,policy=policy,refresh=hook)==0
    load_policy(policy,root,NOW)
    assert not (root/'.local/pages-release/publication.lock').exists()
