"""显式目录重认不能落到 bind、只读卷、其他容器或变化后的计划。"""
import json
from copy import deepcopy
import pytest
from anp.management import dbx_export_identity as repair


def snapshot():
    return {'Id':'container-a','Image':'image-a','State':{'Status':'running'},'Mounts':[
        {'Destination':repair.MOUNT,'Type':'volume','Name':'owned','RW':True}]}


@pytest.mark.parametrize('change',[
    lambda v:v['State'].update(Status='exited'),
    lambda v:v['Mounts'][0].update(Type='bind'),
    lambda v:v['Mounts'][0].update(Name='foreign'),
    lambda v:v['Mounts'][0].update(RW=False),
    lambda v:v['Mounts'].clear(),
    lambda v:v['Mounts'].append(deepcopy(v['Mounts'][0])),
])
def test_rejects_unverified_storage_before_any_mutation(monkeypatch,change):
    info=snapshot();change(info)
    calls=[]
    def inspect(command,**kw):calls.append(command);return json.dumps([info])
    monkeypatch.setattr(repair,'run',inspect)
    with pytest.raises(ValueError):repair.inspect_mount('platform','owned')
    assert calls==[['docker','inspect','platform']]


@pytest.mark.parametrize('field',['container_id','image_id','mount'])
def test_apply_rejects_stale_plan_without_entering_container(tmp_path,monkeypatch,field):
    binding={'container_id':'a','image_id':'b','mount':{'Name':'owned'}}
    old=deepcopy(binding);old[field]='changed'
    plan=tmp_path/'plan.json';plan.write_bytes(json.dumps({'binding':old}).encode())
    monkeypatch.setattr(repair,'inspect_mount',lambda *a:binding)
    def denied(*a,**kw):raise AssertionError('不应触碰容器或状态库')
    monkeypatch.setattr(repair,'probe',denied)
    with pytest.raises(ValueError,match='计划后变化'):
        repair.main(['apply','--volume','owned','--plan',str(plan)])


def test_inspection_refuses_to_overwrite_prior_plan(tmp_path,monkeypatch):
    plan=tmp_path/'plan.json';plan.write_bytes(b'original')
    monkeypatch.setattr(repair,'inspect_mount',lambda *a:{})
    monkeypatch.setattr(repair,'probe',lambda *a:{'needs_reconfirmation':True})
    with pytest.raises(FileExistsError):repair.main(['inspect','--volume','owned','--plan',str(plan)])
    assert plan.read_bytes()==b'original'


def test_frozen_sdk_requires_real_workspace_before_any_docker_access(tmp_path,monkeypatch):
    monkeypatch.setattr(repair,'ROOT',tmp_path/'installed-sdk')
    def denied(*a,**kw):raise AssertionError('应先确认备份工作区')
    monkeypatch.setattr(repair,'inspect_mount',denied)
    with pytest.raises(ValueError,match='ANP_ROOT'):
        repair.main(['apply','--volume','owned','--plan',str(tmp_path/'plan.json')])
