"""Lens 真实 OTLP 入库/查询，以及只读账号隔离验收。"""
import time
from uuid import uuid4
import httpx
from anp.config import secret,value


def test_lens_otlp_roundtrip():
    trace_id=uuid4().hex
    span_id=uuid4().hex[:16]
    now=time.time_ns()
    payload={'resourceSpans':[{'resource':{'attributes':[
        {'key':'service.name','value':{'stringValue':'anp-lens-验收'}}]},
        'scopeSpans':[{'scope':{'name':'anp.integration'},'spans':[{
            'traceId':trace_id,'spanId':span_id,'name':'Lens 追踪验收','kind':1,
            'startTimeUnixNano':str(now),'endTimeUnixNano':str(now+1000000),
            'status':{'code':1},'attributes':[
                {'key':'gen_ai.operation.name','value':{'stringValue':'invoke_agent'}},
                {'key':'gen_ai.agent.name','value':{'stringValue':'ANP 验收 Agent'}}]}]}]}]}
    with httpx.Client(base_url=value('litellm','url').removesuffix('/v1'),trust_env=False,timeout=30,
        headers={'Authorization':'Bearer '+secret('LITELLM_KEY')}) as client:
        response=client.post('/v1/traces',json=payload)
        assert response.status_code==200,response.text
        response=client.get('/v1/traces/'+trace_id)
        assert response.status_code==200,response.text
        assert trace_id in response.text and span_id in response.text
        response=client.get('/v1/traces')
        assert response.status_code==200,response.text
        assert trace_id in response.text
    assert httpx.get(value('litellm','url').removesuffix('/v1')+'/v1/traces',trust_env=False).status_code==401


def test_lens_reader_database_scope():
    with httpx.Client(base_url=value('clickhouse','url'),trust_env=False,
        auth=('litellm_reader',secret('LITELLM_CH_READ_PASSWORD'))) as client:
        response=client.post('/',content='SELECT count() FROM litellm.otel_traces')
        assert response.status_code==200,response.text
        # 检查实际授权范围，不尝试写入业务数据。
        response=client.post('/',content='SHOW GRANTS')
        assert response.status_code==200,response.text
        assert 'SELECT ON litellm.*' in response.text
        assert 'INSERT' not in response.text and 'ALL ON' not in response.text
        response=client.post('/',content='SELECT count() FROM default.analytics_observations')
        assert response.status_code!=200
        assert 'ACCESS_DENIED' in response.text
