"""F5 前置启动的工作区隔离与重复执行保护。"""
import base64
import json
from unittest.mock import Mock

import httpx
import pytest

from anp import ROOT
from anp.dev import frontend


@pytest.mark.parametrize('source,allowed',[
    (str(ROOT/'console/src/main.tsx'),True),('/another-project/src/main.tsx',False)])
def test_only_matching_workspace_may_reuse_vite(monkeypatch,source,allowed):
    factory=httpx.Client
    payload=base64.b64encode(json.dumps({'file':source}).encode()).decode()
    transport=httpx.MockTransport(lambda _:httpx.Response(200,text='//# sourceMappingURL=data:application/json;base64,'+payload))
    monkeypatch.setattr(frontend.httpx,'Client',lambda **kwargs:factory(transport=transport,**kwargs))
    if allowed:assert frontend.ready()
    else:
        with pytest.raises(RuntimeError,match='其他工作区'):frontend.ready()


def test_two_prelaunch_invocations_create_only_one_server(monkeypatch,tmp_path):
    states=iter([False,True,True])
    monkeypatch.setattr(frontend,'ready',lambda:next(states))
    monkeypatch.setattr(frontend,'ROOT',tmp_path)
    monkeypatch.setattr(frontend,'node_command',lambda:['node','vite'])
    launch=Mock(return_value=Mock(poll=lambda:None))
    monkeypatch.setattr(frontend.subprocess,'Popen',launch)
    frontend.ensure()
    frontend.ensure()
    assert launch.call_count==1
