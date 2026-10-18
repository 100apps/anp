"""候选制品跨工作流复用时，严格保留来源、源码和配方边界。"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import reuse_component as cache


def test_partial_probe_falls_back_in_order_and_validates_all_ids_before_io(monkeypatch):
    calls=[]
    monkeypatch.setattr(cache,'_reuse_from_run',lambda component,run:calls.append(run) or run=='3')
    assert cache.reuse('studio','1, 2,1,3') and calls==['1','2','3']
    calls.clear()
    for invalid in ['1,unsafe','1,,2','1,2,3,4,5']:
        with pytest.raises(ValueError):cache.reuse('studio',invalid)
    assert calls==[]


def test_foreign_workflow_cannot_reach_git_or_registry(monkeypatch):
    monkeypatch.setattr(cache.subprocess,'check_output',lambda *a,**k:json.dumps({
        'head_sha':'a'*40,'path':'.github/workflows/untrusted.yml'}))
    monkeypatch.setattr(cache,'git',lambda *a:pytest.fail('不应访问 Git'))
    with pytest.raises(ValueError,match='本仓库'):cache.reuse('studio','1')


def test_probe_for_other_component_does_not_pull_nonexistent_image(monkeypatch):
    monkeypatch.setattr(cache.subprocess,'check_output',lambda *a,**k:json.dumps({
        'head_sha':'a'*40,'path':'.github/workflows/component.yml','display_title':'组件源码验证 · langfuse-web'}))
    monkeypatch.setattr(cache,'git',lambda *a:pytest.fail('不应访问 Git'))
    monkeypatch.setattr(cache.subprocess,'run',lambda *a,**k:pytest.fail('不应拉取其他组件镜像'))
    assert cache.reuse('studio','1') is False


@pytest.mark.parametrize('workflow',['platform','component'])
@pytest.mark.parametrize('source,recipe,accepted',[
    ('b'*40,'current',False),('a'*40,'old',False),('a'*40,'current',True)])
def test_only_matching_source_and_recipe_may_be_tagged(monkeypatch,workflow,source,recipe,accepted):
    info={'head_sha':'c'*40,'path':f'.github/workflows/{workflow}.yml','display_title':'组件源码验证 · studio'}
    image={'Config':{'Labels':{'org.opencontainers.image.revision':source,'dev.anp.build.recipe':recipe}}}
    monkeypatch.setattr(cache.subprocess,'check_output',lambda args,**kwargs:
        json.dumps(info) if args[0]=='gh' else json.dumps([image]))
    monkeypatch.setattr(cache,'git',lambda *args:'a'*40)
    monkeypatch.setattr(cache,'fingerprint',lambda component:'current')
    commands=[]
    monkeypatch.setattr(cache.subprocess,'run',lambda args,**kwargs:commands.append(args) or SimpleNamespace(returncode=0))
    assert cache.reuse('studio','1') is accepted
    assert any(args[:2]==['docker','tag'] for args in commands) is accepted


@pytest.mark.parametrize('platform,actions,runner,ephemeral',[
    ('linux','true','github-hosted',True),('win32','true','github-hosted',False),
    ('linux','false','github-hosted',False),('linux','true','self-hosted',False)])
@pytest.mark.parametrize('source_matches',[True,False])
def test_only_missed_ephemeral_candidate_is_reclaimed(monkeypatch,platform,actions,runner,ephemeral,source_matches):
    monkeypatch.setattr(cache.sys,'platform',platform)
    monkeypatch.setenv('GITHUB_ACTIONS',actions)
    monkeypatch.setenv('RUNNER_ENVIRONMENT',runner)
    info={'head_sha':'c'*40,'path':'.github/workflows/platform.yml'}
    image={'Config':{'Labels':{'org.opencontainers.image.revision':('a' if source_matches else 'b')*40,
        'dev.anp.build.recipe':'current'}}}
    monkeypatch.setattr(cache.subprocess,'check_output',lambda args,**kwargs:
        json.dumps(info) if args[0]=='gh' else json.dumps([image]))
    monkeypatch.setattr(cache,'git',lambda *args:'a'*40)
    monkeypatch.setattr(cache,'fingerprint',lambda component:'current')
    commands=[]
    monkeypatch.setattr(cache.subprocess,'run',lambda args,**kwargs:commands.append(args) or SimpleNamespace(returncode=0))
    assert cache.reuse('dbx','1') is source_matches
    assert (['docker','image','rm','ghcr.io/100apps/anp-build-dbx:run-1'] in commands) is (ephemeral and not source_matches)
    assert all(args[:3]!=['docker','system','prune'] for args in commands)


def test_candidate_cleanup_cannot_target_other_images(monkeypatch):
    monkeypatch.setattr(cache.sys,'platform','linux')
    monkeypatch.setenv('GITHUB_ACTIONS','true')
    monkeypatch.setenv('RUNNER_ENVIRONMENT','github-hosted')
    monkeypatch.setattr(cache.subprocess,'run',lambda *a,**k:pytest.fail('不能访问其他镜像'))
    with pytest.raises(ValueError,match='身份不匹配'):
        cache.discard_missed_candidate('redis:8-alpine')
