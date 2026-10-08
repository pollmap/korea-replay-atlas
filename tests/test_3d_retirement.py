"""Retired entrypoints must fail before generating files or loading 3D packages."""
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
MODULES=['mesh','glb_merge','terrain','national_terrain','mesh_metadata_audit',
    'hierarchy','lod_error','building_parts_delta','building_parts_national',
    'building_parts_publication','tileset_streaming','building_heights','elevation','retile','rail','depth']

@pytest.mark.parametrize('module', MODULES)
def test_retired_3d_cli_has_no_output_or_optional_imports(tmp_path,module):
    env={**os.environ,'PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'}
    result=subprocess.run([sys.executable,'-B','-m','pipeline.'+module],cwd=tmp_path,env=env,
        text=True,capture_output=True,timeout=10)
    assert result.returncode!=0
    assert '3D generation is retired.' in result.stderr
    assert 'Traceback' not in result.stderr
    assert list(tmp_path.iterdir())==[]

@pytest.mark.parametrize('args',[['pipeline.national','process'],*[
    ['pipeline.cli',command] for command in ['tiles','terrain','merge-terrain','depth','bootstrap']]])
def test_legacy_commands_reject_before_io(tmp_path,args):
    env={**os.environ,'PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'}
    result=subprocess.run([sys.executable,'-B','-m',*args],cwd=tmp_path,env=env,
        text=True,capture_output=True,timeout=10)
    assert result.returncode!=0
    assert '3D generation is retired.' in result.stderr
    assert 'Traceback' not in result.stderr
    assert list(tmp_path.iterdir())==[]
