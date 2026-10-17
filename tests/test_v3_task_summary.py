"""完整任务统计须超过列表上限，并保留业务 API 与数据库权限边界。"""
import asyncio
import os
import json
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest

from anp import ROOT
from tests.conftest import cli
from anp.auth.identity import actor_from_token
from anp.errors import BusinessError
from anp.server.api import app
from anp.management.bootstrap import DEFAULT_WORKSPACE
from apps.demo.services.tasks import service

pytestmark=pytest.mark.skipif(os.environ.get('ANP_V3_PLATFORM_SMOKE')!='1',reason='需显式开启真实 V3 平台验收')


def test_full_summary_exceeds_list_limit_and_rejects_cross_workspace(identities):
    async def verify():
        owner=identities['outside']
        actor=await actor_from_token(owner['token'])
        rows=await actor.objects.insert('tasks',[{'workspace_id':actor.workspace_id,
            'created_by':owner['id'],'app_id':'demo','title':'完整统计边界',
            'input':{'text':'统计验收'},'request_key':str(uuid4())} for _ in range(105)])
        for status,selected in [('running',rows[:2]),('succeeded',rows[2:5])]:
            await actor.objects.patch('tasks',{'status':status},id='in.('+','.join(r['id'] for r in selected)+')')
        assert len(await service.list(actor))==100
        expected={'total':105,'pending':100,'running':2,'succeeded':3,'failed':0,'cancelled':0}
        assert await service.summary(actor)==expected
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            path='/api/apps/demo/tasks/summary'
            response=await client.get(path,headers={'Authorization':'Bearer '+owner['token']})
            assert response.status_code==200 and response.json()==expected
            assert (await client.get(path)).status_code==401
            response=await client.get(path,headers={'Authorization':'Bearer '+owner['token'],'X-Workspace-ID':DEFAULT_WORKSPACE})
            assert response.status_code==403
            response=await client.get(path,headers={'Authorization':'Bearer '+identities['admin']['token'],
                'X-Workspace-ID':actor.workspace_id})
            assert response.status_code==403
            response=await client.get(path,headers={'Authorization':'Bearer '+identities['viewer']['token']})
            assert response.status_code==200
        # 默认本地 CLI 在所有业务服务地址不可达时仍可统计。
        profile='summary-'+actor.workspace_id
        disabled={'ANP_WORKBENCH_API_URL':'http://127.0.0.1:1',
            'ANP_WORKBENCH_WORKER_URL':'http://127.0.0.1:1','ANP_AGENT_URL':'http://127.0.0.1:1'}
        local=cli(['demo','tasks','summary'],owner,profile,environment=disabled)
        assert local.returncode==0 and json.loads(local.stdout)==expected
        # 用自己的临时端口启动真实 API；关闭 lifespan 协调器，只做只读统计，不改变现有 IDE 服务。
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
        endpoint='http://127.0.0.1:'+str(port)
        log_path=ROOT/'.local/logs/task-summary-cli.log';log_path.parent.mkdir(parents=True,exist_ok=True)
        with log_path.open('ab') as log:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','anp.server.api:app',
                '--host','127.0.0.1','--port',str(port),'--lifespan','off'],cwd=ROOT,stdout=log,stderr=log,
                **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
            try:
                deadline=time.monotonic()+20
                with httpx.Client(trust_env=False,timeout=1) as client:
                    while True:
                        assert process.poll() is None,'临时验收 API 意外退出'
                        try:
                            if client.get(endpoint+'/health').status_code==200:break
                        except httpx.HTTPError:pass
                        assert time.monotonic()<deadline,'临时验收 API 启动超时'
                        time.sleep(.1)
                remote=cli(['--endpoint',endpoint,'demo','tasks','summary'],owner,profile,'http')
                assert remote.returncode==0 and json.loads(remote.stdout)==expected
            finally:
                if process.poll() is None:process.terminate()
                process.wait(timeout=15)
        # 即使绕过 API 直接调用 RPC，也不能统计其他工作空间。
        with pytest.raises(BusinessError) as denied:
            await actor.objects.rpc('task_summary',p_workspace=DEFAULT_WORKSPACE,p_app='demo')
        assert denied.value.status==403
        with pytest.raises(BusinessError) as denied:
            await actor.objects.rpc('task_summary',p_workspace=actor.workspace_id,p_app='unregistered')
        assert denied.value.status==403
    asyncio.run(verify())
