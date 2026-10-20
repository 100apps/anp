"""私有配置可重复初始化；不能重置操作者的存储路径或已有密码。"""
from pathlib import Path
import json
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import manage


def test_init_preserves_explicit_root_and_credentials(monkeypatch,tmp_path):
    monkeypatch.setattr(manage,'ROOT',tmp_path)
    (tmp_path/'business').mkdir()
    (tmp_path/'.env').write_text('ANP_DATA_ROOT="/srv/anp/owned-data"\nANP_SERVICES=redis\nPOSTGRES_PASSWORD=existing-secret-for-test\n',encoding='utf-8')
    manage.init()
    first=manage.read_env(tmp_path/'.env')
    manage.init()
    second=manage.read_env(tmp_path/'.env')
    assert first==second
    assert second['ANP_DATA_ROOT']=='/srv/anp/owned-data'
    assert second['POSTGRES_PASSWORD']=='existing-secret-for-test'
    assert second['ANP_SERVICES']=='redis'


def test_foreign_compose_instance_is_rejected(monkeypatch):
    def output(command,**kwargs):
        if command[1]=='ps':return 'foreign-container\n'
        return json.dumps([{'Config':{'Labels':{'dev.anp.workspace':'another-workspace'}}}])
    monkeypatch.setattr(manage.subprocess,'check_output',output)
    with pytest.raises(RuntimeError,match='不属于本工作区'):
        manage.ensure_owned_project('this-workspace')


def test_owned_compose_instance_can_be_managed(monkeypatch):
    def output(command,**kwargs):
        if command[1]=='ps':return 'owned-container\n'
        return json.dumps([{'Config':{'Labels':{'dev.anp.workspace':'this-workspace'}}}])
    monkeypatch.setattr(manage.subprocess,'check_output',output)
    manage.ensure_owned_project('this-workspace')
