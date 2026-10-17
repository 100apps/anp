import asyncio
import httpx
import pytest
from anp.execution.agent_server import engine_client
from anp.auth.identity import actor_from_token
from apps.demo.services.tasks import service
from anp.config import value,secret
from anp.management.bootstrap import wait_url


def test_studio_text_creates_and_saves_real_task(web_services,identities):
    async def run():
        await wait_url(value('agent','url')+'/ok',headers={'Authorization':'Bearer '+secret('ENGINE_TOKEN')})
        async with engine_client() as client:
            assistants=await client.post('/assistants/search',json={'graph_id':'demo'})
            assistants.raise_for_status()
            assistant_id=assistants.json()[0]['assistant_id']
            schemas=await client.get('/assistants/'+assistant_id+'/schemas')
            schemas.raise_for_status()
            assert 'text' in schemas.json()['input_schema']['properties']
            thread=await client.post('/threads',json={})
            thread.raise_for_status()
            response=await client.post('/threads/'+thread.json()['thread_id']+'/runs/wait',
                json={'assistant_id':'demo','input':{'text':'Studio 直接输入文本，自动保存！','title':'Studio 输入验收'}},timeout=60)
            response.raise_for_status()
            state=response.json()
            assert '__error__' not in state,state
            assert state['result']['saved'] is True
            actor=await actor_from_token(identities['admin']['token'])
            task=await service.get(actor,state['task_id'])
            assert task['mode']=='studio' and task['status']=='succeeded'
            assert len(task['nodes'])==3
            result=await service.result(actor,state['task_id'])
            assert result['report']['normalized']['text']=='Studio 直接输入文本，自动保存！'
            changed=await client.post('/threads/'+thread.json()['thread_id']+'/runs/wait',
                json={'assistant_id':'demo','input':{'text':'同一线程的新文本。'}},timeout=60)
            changed.raise_for_status()
            assert '__error__' not in changed.json(),changed.json()
            assert changed.json()['task_id']!=state['task_id']
    asyncio.run(run())


def test_studio_prepare_requires_worker_token(web_services):
    response=httpx.post('http://127.0.0.1:8281/internal/apps/demo/studio/tasks',trust_env=False,
        json={'payload':{'text':'禁止匿名创建'},'request_key':'anonymous'})
    assert response.status_code==401


def test_empty_input_has_helpful_message():
    from anp.runtime.remote_node import build_remote_graph
    with pytest.raises(ValueError,match='请在 Studio 的 text 输入框填写非空文本'):
        asyncio.run(build_remote_graph('demo').ainvoke({}))
