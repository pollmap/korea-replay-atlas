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
    for path in [client,receipt]:os.utime(path,(100,100))
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
