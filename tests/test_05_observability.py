"""真实服务验收：身份关联、节点评分以及网关正文落库。"""
import asyncio
import json
import time
from uuid import uuid4
import httpx
import pytest
from anp.config import secret,value
from anp.sdk.tracing import get_client
from apps.demo.evaluators import score_node
from tests.conftest import cli


@pytest.mark.parametrize('transport',['local','http'])
def test_task_identity_and_node_scores(web_services,identities,transport):
    identity=identities['developer']
    created=cli(['demo','tasks','create','--title','可观测性验收 '+transport,
        '--text','  AI   可观测性。Hello AI! '],identity,'observability-'+transport,transport)
    assert created.returncode==0,created.stdout
    task=json.loads(created.stdout)
    run=cli(['demo','tasks','run',task['id']],identity,'observability-'+transport,transport)
    assert run.returncode==0,run.stdout
    assert json.loads(run.stdout)['status']=='succeeded'
    trace_id=task['id'].replace('-','')
    until=time.monotonic()+45
    while time.monotonic()<until:
        response=httpx.get(value('langfuse','url')+'/api/public/v2/observations',trust_env=False,
            auth=(secret('LANGFUSE_PUBLIC_KEY'),secret('LANGFUSE_SECRET_KEY')),params={'traceId':trace_id,'limit':100})
        response.raise_for_status()
        observations=response.json()['data']
        scores=get_client().api.scores_v3.get_many_v3(trace_id=trace_id,limit=100,fields='core,subject,details').data
        if len(scores)==6 and len(observations)>=(3 if transport=='local' else 6):break
        time.sleep(1)
    else:pytest.fail('观测或评分未完整入库')
    assert all(o['userId']==identity['id'] and o['sessionId']==task['id'] for o in observations)
    assert {s.name for s in scores}=={'node_success','normalization_valid','analysis_consistency','artifact_saved'}
    assert all(s.value==1 for s in scores)
    assert {s.subject.id for s in scores}=={o['id'] for o in observations if o['name'].startswith('业务节点/')}


def test_scorer_rejects_bad_output():
    result=asyncio.run(score_node('analyze',{'normalized':{'text':'Hello AI.'}},
        {'character_count':999,'token_count':0},None))
    assert result=={'analysis_consistency':False}


def test_gateway_request_and_response_persisted():
    marker='日志验收-'+str(uuid4())
    with httpx.Client(base_url=value('litellm','url').removesuffix('/v1'),trust_env=False,timeout=30,
        headers={'Authorization':'Bearer '+secret('LITELLM_KEY')}) as client:
        response=client.post('/v1/chat/completions',json={'model':'demo-mock',
            'user':'anp-observability-check','messages':[{'role':'user','content':marker}]})
        response.raise_for_status()
        completion=response.json()
        until=time.monotonic()+45
        while time.monotonic()<until:
            response=client.get('/spend/logs',params={'request_id':completion['id']})
            response.raise_for_status()
            rows=response.json()
            if rows:
                record=rows[0]
                request=record['proxy_server_request'];output=record['response']
                if isinstance(request,str):request=json.loads(request)
                if isinstance(output,str):output=json.loads(output)
                assert request['messages'][0]['content']==marker
                assert output['choices'][0]['message']['content']==completion['choices'][0]['message']['content']
                return
            time.sleep(1)
    pytest.fail('LiteLLM 未保存新请求日志')
