import asyncio
import json
import time
from uuid import uuid4
import httpx
from anp.config import secret,value
from anp.auth.identity import actor_from_token
from apps.demo.services.tasks import service
from tests.conftest import cli


def headers(identity):
    return {'Authorization':'Bearer '+identity['token']}


def wait_task(client,task_id):
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        response=client.get('/api/apps/demo/tasks/'+task_id)
        assert response.status_code==200,response.text
        task=response.json()
        if task['status'] in ('succeeded','failed','cancelled'):
            assert task['status']=='succeeded',task.get('error')
            return task
        time.sleep(.4)
    raise AssertionError('HTTP 图执行超时')


def test_http_cli_and_local_results_match(web_services,identities):
    payload={'title':'双模式一致性','text':'  Hello   AI. 同一业务，两种执行！ '}
    async def local():
        actor=await actor_from_token(identities['developer']['token'])
        task=await service.create(actor,payload)
        task=await service.run(actor,task['id'],'local')
        assert task['status']=='succeeded',task.get('error')
        return await service.result(actor,task['id'])
    local_result=asyncio.run(local())
    created=cli(['demo','tasks','create','--title',payload['title'],'--text',payload['text']],
        identities['developer'],'http-developer','http')
    assert created.returncode==0,created.stdout
    task_id=json.loads(created.stdout)['id']
    run=cli(['demo','tasks','run',task_id],identities['developer'],'http-developer','http')
    assert run.returncode==0,run.stdout
    result=cli(['demo','tasks','result',task_id],identities['developer'],'http-developer','http')
    assert result.returncode==0,result.stdout
    assert json.loads(result.stdout)['report']==local_result['report']


def test_http_permissions_and_cross_workspace_rls(web_services,identities):
    with httpx.Client(base_url='http://127.0.0.1:8180',headers=headers(identities['developer']),trust_env=False) as client:
        response=client.post('/api/apps/demo/tasks',json={'text':'仅授权工作空间可见','title':'权限隔离验证'})
        assert response.status_code==201,response.text
        task=response.json()
    with httpx.Client(base_url='http://127.0.0.1:8180',trust_env=False) as client:
        assert client.get('/api/apps/demo/tasks').status_code==401
        assert client.get('/api/connections',headers=headers(identities['developer'])).status_code==403
        assert client.get('/api/members',headers=headers(identities['viewer'])).status_code==403
        assert client.post('/api/apps/demo/tasks',headers=headers(identities['viewer']),json={'text':'禁止写入'}).status_code==403
        assert client.post('/api/apps/demo/tasks/'+task['id']+'/run',headers=headers(identities['viewer'])).status_code==403
        assert client.get('/api/apps/demo/tasks/'+task['id'],headers=headers(identities['viewer'])).status_code==200
        assert client.get('/api/apps/demo/tasks/'+task['id'],headers=headers(identities['outside'])).status_code==404
    # 直接调用 Supabase 也不能绕过 RLS。
    for role,expected in [('viewer',1),('outside',0)]:
        response=httpx.get(value('supabase','url')+'/rest/v1/tasks',headers={**headers(identities[role]),
            'apikey':secret('ANON_KEY'),'Accept-Profile':'platform'},params={'id':'eq.'+task['id']},trust_env=False)
        assert response.status_code==200,response.text
        assert len(response.json())==expected


def test_task_idempotency_cancel_and_node_auth(web_services,identities):
    with httpx.Client(base_url='http://127.0.0.1:8180',headers=headers(identities['admin']),trust_env=False) as client:
        key=str(uuid4()); payload={'text':'重复请求只创建一个任务。'}
        first=client.post('/api/apps/demo/tasks',json=payload,headers={'Idempotency-Key':key})
        second=client.post('/api/apps/demo/tasks',json=payload,headers={'Idempotency-Key':key})
        assert first.status_code==second.status_code==201
        task_id=first.json()['id']
        assert second.json()['id']==task_id
        assert client.post('/api/apps/demo/tasks',json={'text':'不同输入'},headers={'Idempotency-Key':key}).status_code==409
        cancelled=client.post('/api/apps/demo/tasks/'+task_id+'/cancel')
        assert cancelled.json()['status']=='cancelled'
        assert client.post('/api/apps/demo/tasks',json={'text':'  '}).status_code==422
    response=httpx.post('http://127.0.0.1:8281/internal/apps/demo/nodes/normalize',json={'state':{}},trust_env=False)
    assert response.status_code==401


def test_member_roles_only_admin_can_change(web_services,identities):
    with httpx.Client(base_url='http://127.0.0.1:8180',trust_env=False) as client:
        target=identities['developer']['id']
        assert client.patch('/api/members/'+target,headers=headers(identities['viewer']),json={'role':'admin'}).status_code==403
        response=client.patch('/api/members/'+target,headers=headers(identities['admin']),json={'role':'viewer'})
        assert response.status_code==200,response.text
        assert client.post('/api/apps/demo/tasks',headers=headers(identities['developer']),json={'text':'角色已降级'}).status_code==403
        assert client.patch('/api/members/'+target,headers=headers(identities['admin']),json={'role':'developer'}).status_code==200
