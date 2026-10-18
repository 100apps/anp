"""真实业务宿主、根CLI与工作台查询；只读现有验收数据，不创建生产运行。"""
import asyncio
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from anp import ROOT
from anp.auth.identity import actor_from_token
from anp.auth.session import login,session_path
from anp.config import secret,value
from anp.sdk.http import async_client
from apps.deep_dub.services.workbench import RECORD_KINDS,service
from tests.test_deep_dub_contracts_live import context,pytestmark


def test_registered_host_queries_workers_and_custom_root_cli(context,monkeypatch):
    _,tokens=context
    monkeypatch.setenv('ANP_PROFILE','deep-dub-workbench-'+uuid4().hex)
    monkeypatch.setenv('PYTHONIOENCODING','utf-8')
    endpoint=value('workbench','api_url').rstrip('/')
    def cli(*args):
        result=subprocess.run([sys.executable,'-c','from anp.cli.main import app;app()',*args],
            cwd=ROOT,capture_output=True,text=True,encoding='utf8',timeout=30)
        assert result.returncode==0,'根CLI失败，原始输出不在异常中公开'
        return json.loads(result.stdout)
    async def run():
        actor=await actor_from_token(tokens['admin'])
        runs=await actor.objects.select('deep_dub_runs',workspace_id='eq.'+actor.workspace_id,
            order='created_at.desc',limit=1)
        assert runs
        selected=runs[0]
        projects=await actor.objects.select('deep_dub_projects',workspace_id='eq.'+actor.workspace_id,
            id='eq.'+selected['project_id'])
        assert projects
        project=projects[0]
        tested=[]
        async with async_client(timeout=20,trust_env=False,headers={'Authorization':'Bearer '+tokens['admin']}) as client:
            discovered=await client.get(endpoint+'/api/applications')
            assert discovered.status_code==200 and any(a['id']=='deep_dub' for a in discovered.json())
            for path in ['/tasks','/tasks/summary','/tasks/'+selected['task_id'],'/runs/'+selected['id']]:
                response=await client.get(endpoint+'/api/apps/deep_dub'+path)
                assert response.status_code==200, '宿主查询失败：'+path
            assert response.json()==await service.run(actor,selected['id'])
            for kind in sorted(RECORD_KINDS):
                path=f'/api/apps/deep_dub/projects/{project["id"]}/records/{kind}'
                response=await client.get(endpoint+path,params={'offset':0,'limit':1})
                assert response.status_code==200,'记录分页失败：'+kind
                page=response.json();assert len(page['items'])<=1 and page['limit']==1
                if kind=='artifacts':assert page['items'], '需核对已有真实产物，不能只验收空列表'
                assert all(item['project_id']==project['id'] and item['workspace_id']==actor.workspace_id for item in page['items'])
                tested.append(kind)
            invalid=await client.get(endpoint+f'/api/apps/deep_dub/projects/{project["id"]}/records/device_leases')
            assert invalid.status_code==422
            absent=await client.get(endpoint+f'/api/apps/deep_dub/projects/{uuid4()}/records/scores')
            assert absent.status_code==404
            anonymous=await client.get(endpoint+'/api/apps/deep_dub/tasks',headers={'Authorization':''})
            assert anonymous.status_code==401
            viewer=await client.get(endpoint+'/api/apps/deep_dub/tasks/summary',headers={'Authorization':'Bearer '+tokens['viewer']})
            assert viewer.status_code==200
            for path,body in [('/internal/apps/deep_dub/studio/tasks',{'payload':{},'request_key':'workbench-read-only-check'}),
                ('/internal/apps/deep_dub/nodes/normalize',{'state':{}})]:
                rejected=await client.post(value('workbench','worker_url')+path,json=body,
                    headers={'Authorization':'Bearer '+secret('WORKER_TOKEN')})
                assert rejected.status_code==403 and rejected.json()['error']['code']=='BUSINESS_EXECUTION_REQUIRED'
            own=await client.post(value('workbench','worker_url')+f'/internal/deep_dub/jobs/{uuid4()}/decode',
                json={'run_id':selected['id']},headers={'Authorization':'Bearer fixture-invalid'})
            assert own.status_code==401
        return selected,tested
    try:
        selected,tested=asyncio.run(run())
        asyncio.run(login(secret('ADMIN_EMAIL'),secret('ADMIN_PASSWORD')))
        local=cli('--endpoint','http://127.0.0.1:1','deep_dub','projects','list')
        remote=cli('--transport','http','--endpoint',endpoint,'deep_dub','projects','list')
        assert local==remote
        detail_local=cli('deep_dub','runs','get',selected['id'])
        detail_http=cli('--transport','http','--endpoint',endpoint,'deep_dub','runs','get',selected['id'])
        assert detail_local==detail_http
        # 子命令显式选择local可以覆盖根HTTP，不偷偷沿用根HTTP失败地址。
        assert local==cli('--transport','http','--endpoint','http://127.0.0.1:1','deep_dub','--transport','local','projects','list')
        Path('.local/deep-dub-workbench-live.json').write_bytes((json.dumps({
            'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'真实8181 API/8282 Worker与根CLI；现有验收记录只读，不代表完整UI/生产流程',
            'registered_application_discovered':True,'record_types_checked':tested,
            'authenticated_summary_and_task_and_run':True,'anonymous_denied':True,'viewer_summary_read':True,
            'legacy_worker_paths_denied':True,'own_worker_router_registered':True,
            'root_cli_default_local_equals_http':True,'root_cli_run_detail_equals_http':True,
            'parent_http_options_inherited':True,'child_explicit_local_override':True,
            'sample_run_id':selected['id'],
        },ensure_ascii=False,indent=2)+'\n').encode())
    finally:session_path().unlink(missing_ok=True)
