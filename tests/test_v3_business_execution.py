"""业务独立执行不可落入通用服务角色路径或被通用协调器提前标记完成。"""
import asyncio

import httpx

from anp.sdk.http import async_client
from anp.applications import registry
from anp.server import api,worker


def test_business_tasks_are_never_reconciled_by_generic_host(monkeypatch):
    monkeypatch.setattr(registry,'applications',lambda:{'private':{'execution':{'owner':'business'}},'demo':{}})
    visited=[]
    async def reconcile(objects,task):visited.append(task['id'])
    monkeypatch.setattr(api,'reconcile_remote',reconcile)
    asyncio.run(api.reconcile_batch(None,[{'id':'protected','app_id':'private'},
        {'id':'ordinary','app_id':'demo'}],2))
    assert visited==['ordinary']


def test_business_studio_and_node_routes_reject_shared_worker_identity(monkeypatch):
    monkeypatch.setattr(registry,'applications',lambda:{'private':{'execution':{'owner':'business'}}})
    monkeypatch.setattr(worker,'secret',lambda name:'fixture-shared-worker-secret')
    async def run():
        async with async_client(transport=httpx.ASGITransport(app=worker.app),base_url='http://fixture',
            headers={'Authorization':'Bearer fixture-shared-worker-secret'}) as client:
            for path,body in [('/internal/apps/private/studio/tasks',{'payload':{},'request_key':'fixture'}),
                ('/internal/apps/private/nodes/normalize',{'state':{}})]:
                response=await client.post(path,json=body)
                assert response.status_code==403 and response.json()['error']['code']=='BUSINESS_EXECUTION_REQUIRED'
    asyncio.run(run())
