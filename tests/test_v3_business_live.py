"""隔离 V3 的默认本地 CLI、HTTP CLI、远程节点与 Studio SSE 真实验收。"""
import json
import os
import subprocess
import sys
import time
import httpx
import pytest
from dotenv import dotenv_values
from anp import ROOT
from anp.config import secret

pytestmark=pytest.mark.skipif(os.environ.get('ANP_V3_BUSINESS_SMOKE')!='1',reason='需显式开启 V3 业务验收')
BASE=os.environ.get('ANP_V3_TEST_URL','http://127.0.0.1:8089')


@pytest.fixture(scope='module')
def business():
    env={**os.environ,**dotenv_values(ROOT/'.local/generated/platform-v3/business.env'),
        'ANP_PROFILE':'v3-e2e','ANP_PASSWORD':secret('ADMIN_PASSWORD'),'PYTHONUTF8':'1'}
    assert env['ANP_SUPABASE_URL']==BASE+'/supabase'
    def cli(arguments,without_business_web=False):
        runtime=env
        if without_business_web:
            runtime={**env,'ANP_WORKBENCH_API_URL':'http://127.0.0.1:1','ANP_WORKBENCH_WORKER_URL':'http://127.0.0.1:1'}
        result=subprocess.run([sys.executable,'-m','anp.cli.entry',*arguments],cwd=ROOT,env=runtime,
            capture_output=True,text=True,encoding='utf8',timeout=180)
        assert result.returncode==0,(arguments[0],result.returncode)
        return json.loads(result.stdout)
    cli(['auth','login','--email',secret('ADMIN_EMAIL')])
    token=json.loads((ROOT/'.local/auth/v3-e2e.json').read_text(encoding='utf8'))['access_token']
    return cli,{'Authorization':'Bearer '+token}


def test_default_local_cli_and_http_cli_save_identical_results(business):
    cli,headers=business
    payload=['--title','V3 双模式一致性','--text','你好，LangGraph。 一个平台，两种执行。']
    reports=[];tasks=[]
    for prefix,mode in [([], 'local'),(['--transport','http'],'http')]:
        created=cli([*prefix,'demo','tasks','create',*payload],without_business_web=mode=='local')
        task=cli([*prefix,'demo','tasks','run',created['id']],without_business_web=mode=='local')
        assert task['status']=='succeeded' and task['mode']==mode
        report=cli([*prefix,'demo','tasks','result',task['id']],without_business_web=mode=='local');reports.append(report['report'])
        detail=cli([*prefix,'demo','tasks','get',task['id']],without_business_web=mode=='local')
        assert [item['node'] for item in detail['nodes']]==['normalize','analyze','save']
        with httpx.Client(base_url=BASE,headers=headers,trust_env=False,timeout=20) as client:
            artifact=client.get('/supabase/storage/v1/object/authenticated/'+report['object_path'])
            assert artifact.status_code==200
            assert artifact.json()==report['report']
        tasks.append({key:task.get(key) for key in ['id','mode','status','thread_id','run_id']})
    assert reports[0]==reports[1]
    (ROOT/'.local/v3-business-e2e.json').write_text(json.dumps({'tasks':tasks},ensure_ascii=False),encoding='utf8')
    user_id=json.loads((ROOT/'.local/auth/v3-e2e.json').read_text(encoding='utf8'))['user']['id']
    with httpx.Client(base_url=BASE+'/langfuse',auth=(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY')),
        trust_env=False,timeout=20) as client:
        for task in tasks:
            deadline=time.monotonic()+45
            while True:
                trace=task['id'].replace('-','')
                observations=client.get('/api/public/v2/observations',params={'traceId':trace,'limit':100})
                observations.raise_for_status();observations=observations.json()['data']
                scores=client.get('/api/public/v3/scores',params={'traceId':trace,'limit':100,'fields':'core,subject,details'})
                scores.raise_for_status();scores=scores.json()['data']
                if len(scores)==6 and len(observations)>=(3 if task['mode']=='local' else 6):break
                assert time.monotonic()<deadline,'新任务观测或评分未完整入库'
                time.sleep(1)
            assert all(item['userId']==user_id and item['sessionId']==task['id'] for item in observations)
            assert {item['name'] for item in scores}=={'node_success','normalization_valid','analysis_consistency','artifact_saved'}
            assert all(item['value']==1 for item in scores)
            assert {item['subject']['id'] for item in scores}=={item['id'] for item in observations if item['name'].startswith('业务节点/')}


def test_management_links_follow_current_deployment_not_legacy_ports(business):
    _,headers=business
    environment=dotenv_values(ROOT/'.local/generated/platform-v3/business.env')
    with httpx.Client(base_url=BASE,trust_env=False,timeout=20) as client:
        assert client.get('/api/connections').status_code==401
        response=client.get('/api/connections',headers=headers);response.raise_for_status()
        connections={item['id']:item for item in response.json()}
        assert connections['rustfs']['url']==environment['ANP_STORAGE_CONSOLE_URL']
        assert connections['rustfs']['status']=='ok'
        assert client.get(connections['rustfs']['url'],follow_redirects=True).status_code==200
        assert connections['lens']['url']==BASE+'/lens/ui/' and connections['lens']['status']=='ok'
        assert connections['litellm']['url']==BASE+'/litellm/ui/'
        assert connections['agent']['url']==BASE+'/agent/docs'
        assert client.get('/agent/docs',headers={'Authorization':'Bearer '+secret('ENGINE_TOKEN')}).status_code==200
        assert all('localhost' not in item['url'] for item in connections.values())


def test_agent_studio_sse_executes_worker_and_saves_result(business):
    _,headers=business
    engine={'Authorization':'Bearer '+secret('ENGINE_TOKEN')}
    with httpx.Client(base_url=BASE,trust_env=False,timeout=90) as client:
        assert client.post('/agent/assistants/search',json={}).status_code==401
        assert httpx.post('http://127.0.0.1:8282/internal/apps/demo/nodes/normalize',json={'state':{}},trust_env=False).status_code==401
        thread=client.post('/agent/threads',headers=engine,json={});assert thread.status_code==200
        thread_id=thread.json()['thread_id']
        events=[]
        with client.stream('POST',f'/agent/threads/{thread_id}/runs/stream',headers=engine,
            json={'assistant_id':'demo','input':{'text':'Studio SSE 真实业务验收','title':'V3 Studio 流式调试'},'stream_mode':['values','updates']}) as stream:
            assert stream.status_code==200
            assert stream.headers['content-type'].startswith('text/event-stream')
            for line in stream.iter_lines():
                if line.startswith('event:'):events.append(line.removeprefix('event:').strip())
        assert 'error' not in events and 'values' in events
        state=client.get(f'/agent/threads/{thread_id}/state',headers=engine);assert state.status_code==200
        task_id=state.json()['values']['task_id']
        task=client.get('/api/apps/demo/tasks/'+task_id,headers=headers);assert task.status_code==200
        assert task.json()['status']=='succeeded' and task.json()['mode']=='studio'
        assert [item['node'] for item in task.json()['nodes']]==['normalize','analyze','save']
        assert client.get('/api/apps/demo/tasks/'+task_id+'/result',headers=headers).status_code==200
        (ROOT/'.local/v3-studio-e2e.json').write_text(json.dumps({'task_id':task_id,'thread_id':thread_id,'events':events}),encoding='utf8')
