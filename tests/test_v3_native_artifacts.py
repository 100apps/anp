"""原生镜像更新须去除旧文件，并在失败重试时保留 DBX 与 Agent 数据。"""
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import native_artifacts as artifacts


@pytest.fixture
def runtime(tmp_path,monkeypatch):
    root=(tmp_path/'runtime').resolve();root.mkdir()
    previous={'workspace':'test-workspace','image':'sha256:'+'a'*64}
    (root/'.anp-native-base.json').write_text(json.dumps(previous))
    for path,data in [('src/obsolete.py','old-code'),('roots/dbx/state/connections.db','user-config'),
        ('state/agent/.langgraph_api/checkpoint','user-checkpoint')]:
        file=root/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(data)
    monkeypatch.setattr(artifacts,'active_native',lambda:False)
    expected={**previous,'image':'sha256:'+'b'*64,'layout':artifacts.LAYOUT}
    return root,previous,expected


def extracted(root):
    for path in artifacts.ANCHORS:
        target=root/path
        if path.endswith(('site-packages','roots/dbx')):target.mkdir(parents=True,exist_ok=True)
        else:target.parent.mkdir(parents=True,exist_ok=True);target.write_text('extracted')


def test_clean_update_keeps_user_state_and_backup(runtime):
    root,_,expected=runtime
    result=artifacts.begin(root,expected)
    assert not result['cached'] and not (root/'src/obsolete.py').exists()
    assert (root/'state/agent/.langgraph_api/checkpoint').read_text()=='user-checkpoint'
    with pytest.raises(RuntimeError,match='尚未完成'):artifacts.validate(root,expected)
    extracted(root);artifacts.finish(root,expected)
    assert (root/'roots/dbx/state/connections.db').read_text()=='user-config'
    assert (root/result['dbx_backup']/'connections.db').read_text()=='user-config'
    assert artifacts.validate(root,expected)==root
    (root/'roots/dbx/state/connections.db').write_text('new-user-config')
    (root/'.anp-native-pending.json').write_text(json.dumps({'expected':expected,'dbx_backup':result['dbx_backup']}))
    assert artifacts.begin(root,expected)['cached']
    assert (root/'roots/dbx/state/connections.db').read_text()=='new-user-config'
    assert not (root/'.anp-native-pending.json').exists()


def test_interrupted_extraction_resumes_from_original_backup(runtime):
    root,_,expected=runtime
    first=artifacts.begin(root,expected)
    partial=root/'roots/dbx/state/connections.db';partial.parent.mkdir(parents=True)
    partial.write_text('partial-copy')
    again=artifacts.begin(root,expected)
    assert again['dbx_backup']==first['dbx_backup']
    extracted(root);artifacts.finish(root,expected)
    assert (root/'roots/dbx/state/connections.db').read_text()=='user-config'


def test_running_runtime_cannot_be_replaced(runtime,monkeypatch):
    root,previous,expected=runtime
    monkeypatch.setattr(artifacts,'active_native',lambda:True)
    with pytest.raises(RuntimeError,match='先停止'):artifacts.begin(root,expected)
    assert json.loads((root/'.anp-native-base.json').read_text())==previous
    assert (root/'src/obsolete.py').read_text()=='old-code'


def test_unknown_workspace_and_nonartifact_path_cannot_be_deleted(runtime,tmp_path):
    root,_,expected=runtime
    with pytest.raises(RuntimeError,match='其他工作区'):
        artifacts.begin(root,{**expected,'workspace':'other-workspace'})
    outside=tmp_path/'outside.txt';outside.write_text('keep')
    for name in ('state','../outside.txt'):
        with pytest.raises(RuntimeError,match='允许更新'):artifacts.remove_artifact(root,name)
    assert outside.read_text()=='keep'
    assert (root/'state/agent/.langgraph_api/checkpoint').read_text()=='user-checkpoint'


def test_wrong_image_and_incomplete_copy_cannot_be_started(runtime):
    root,_,expected=runtime
    artifacts.begin(root,expected)
    with pytest.raises(RuntimeError,match='不完整'):artifacts.finish(root,expected)
    extracted(root);artifacts.finish(root,expected)
    with pytest.raises(RuntimeError,match='镜像身份不符'):
        artifacts.validate(root,{**expected,'image':'sha256:'+'c'*64})


def test_native_cold_start_pulls_missing_image_before_inspection(monkeypatch):
    import subprocess
    import prepare_native
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if len(calls)==1:return subprocess.CompletedProcess(command,1,'')
        return subprocess.CompletedProcess(command,0,json.dumps([{'Id':'sha256:'+'b'*64}]))
    monkeypatch.setattr(prepare_native.subprocess,'run',run)
    assert prepare_native.image_identity('ghcr.io/example/platform:latest')=='sha256:'+'b'*64
    assert calls==[['docker','image','inspect','ghcr.io/example/platform:latest'],
        ['docker','pull','ghcr.io/example/platform:latest'],['docker','image','inspect','ghcr.io/example/platform:latest']]


def test_existing_native_image_does_not_require_registry_access(monkeypatch):
    import subprocess
    import prepare_native
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command,0,json.dumps([{'Id':'sha256:'+'b'*64}]))
    monkeypatch.setattr(prepare_native.subprocess,'run',run)
    assert prepare_native.image_identity('local/platform:dev')=='sha256:'+'b'*64
    assert len(calls)==1 and calls[0][:3]==['docker','image','inspect']
