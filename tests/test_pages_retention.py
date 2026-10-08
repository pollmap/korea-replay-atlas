import hashlib,json,os
from pathlib import Path
import pytest
from pipeline.pages_retention import retire

def stage(root,name):
    folder=root/'.local/pages-release'/name;client=folder/'client';client.mkdir(parents=True)
    (client/'index.html').write_text('generated')
    entries=json.dumps([{'target':'index.html','bytes':9,'sha256':hashlib.sha256(b'generated').hexdigest()}]).encode()
    (folder/'asset-manifest.json').write_bytes(entries)
    receipt=folder/'receipt.json';receipt.write_text(json.dumps({'complete':True,'platform':'cloudflare-pages','manifest_sha256':hashlib.sha256(entries).hexdigest()}))
    for path in [client,receipt,client/'index.html']:os.utime(path,(100,100))
    return receipt

def test_dry_run_retains_current_and_raw_then_repeated_cleanup_is_noop(tmp_path):
    current=stage(tmp_path,'korea-replay-aaaaaaaaaaaaaaaa-candidate')
    old=stage(tmp_path,'korea-replay-bbbbbbbbbbbbbbbb-candidate')
    raw=tmp_path/'original.xml';raw.write_text('unique')
    assert len(retire(tmp_path,[current],now=100000)['retired'])==1
    assert (old.parent/'client/index.html').exists()
    assert len(retire(tmp_path,[current],apply=True,now=100000)['retired'])==1
    assert current.exists() and (current.parent/'client/index.html').exists()
    assert old.exists() and (old.parent/'asset-manifest.json').exists() and raw.read_text()=='unique'
    assert retire(tmp_path,[current],apply=True,now=100000)['retired']==[]

def test_incomplete_young_or_unexpected_payload_is_preserved(tmp_path):
    current=stage(tmp_path,'korea-replay-aaaaaaaaaaaaaaaa-candidate')
    old=stage(tmp_path,'korea-replay-bbbbbbbbbbbbbbbb-candidate')
    assert not retire(tmp_path,[current],apply=True,now=1000)['retired']
    (old.parent/'client/personal.txt').write_text('keep')
    assert not retire(tmp_path,[current],apply=True,now=100000)['retired']
    with pytest.raises(ValueError):retire(tmp_path,[],apply=True)

def test_same_size_modified_generated_file_is_not_deleted(tmp_path):
    current=stage(tmp_path,'korea-replay-aaaaaaaaaaaaaaaa-candidate')
    old=stage(tmp_path,'korea-replay-bbbbbbbbbbbbbbbb-candidate')
    file=old.parent/'client/index.html'; file.write_text('different');os.utime(file,(100,100))
    assert not retire(tmp_path,[current],apply=True,now=100000)['retired']
    assert file.read_text()=='different'

def test_client_symlink_and_manifest_path_escape_fail_closed(tmp_path):
    current=stage(tmp_path,'korea-replay-aaaaaaaaaaaaaaaa-candidate')
    old=stage(tmp_path,'korea-replay-bbbbbbbbbbbbbbbb-candidate')
    client=old.parent/'client'; preserved=old.parent/'kept';client.rename(preserved)
    try:client.symlink_to(preserved,target_is_directory=True)
    except OSError:pytest.skip('symlink privileges unavailable')
    with pytest.raises(ValueError,match='linked'):retire(tmp_path,[current],apply=True,now=100000)
    assert (preserved/'index.html').exists()

def test_pre_delete_guard_failure_preserves_payload(tmp_path):
    current=stage(tmp_path,'korea-replay-aaaaaaaaaaaaaaaa-candidate')
    old=stage(tmp_path,'korea-replay-bbbbbbbbbbbbbbbb-candidate')
    def busy():raise ValueError('producer active')
    with pytest.raises(ValueError,match='producer active'):retire(tmp_path,[current],apply=True,now=100000,pre_delete=busy)
    assert (old.parent/'client/index.html').exists()

def test_retirement_marker_link_does_not_touch_unrelated_file(tmp_path):
    current=stage(tmp_path,'korea-replay-aaaaaaaaaaaaaaaa-candidate')
    old=stage(tmp_path,'korea-replay-bbbbbbbbbbbbbbbb-candidate')
    target=tmp_path/'personal.json';target.write_text('personal')
    try:(old.parent/'payload-retired.json').symlink_to(target)
    except OSError:pytest.skip('symlink privileges unavailable')
    with pytest.raises(ValueError,match='linked'):retire(tmp_path,[current],apply=True,now=100000)
    assert target.read_text()=='personal' and (old.parent/'client/index.html').exists()
