"""模型契约只判断可登记候选，不将生成响应当作质量审核。"""
import json
import asyncio
import httpx

import pytest

from anp.errors import BusinessError
from apps.deep_dub.nodes.translate import candidate


def response(**change):
    return {'model':'reported-engine','choices':[{'finish_reason':'stop','message':{'content':json.dumps({'segment_id':'segment','chinese_text':'并非全部十二个样本均已完成。'})}}],
        'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30,'secret':'不能保留'},**change}


def test_exact_segment_and_reported_identity_with_usage_allowlist():
    result=candidate(response(),'segment')
    assert result['reported_model']=='reported-engine' and result['chinese_text']=='并非全部十二个样本均已完成。'
    assert result['usage']=={'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}
    assert candidate(response(usage=None),'segment')['usage']=={}


@pytest.mark.parametrize('content',[
    {'segment_id':'different','chinese_text':'译文'},
    {'segment_id':'segment','chinese_text':' '},
    {'segment_id':'segment','chinese_text':'English echo'},
    {'segment_id':'segment','chinese_text':'中文','score':1},
    {'segment_id':'segment','chinese_text':12},
    [],None,
])
def test_invalid_or_wrong_segment_is_not_registered(content):
    raw=response();raw['choices'][0]['message']['content']=json.dumps(content)
    with pytest.raises(BusinessError) as error:candidate(raw,'segment')
    assert error.value.code=='TRANSLATION_RESPONSE_INVALID'


@pytest.mark.parametrize('reason',['length','content_filter','tool_calls',None])
def test_incomplete_response_cannot_be_a_candidate(reason):
    raw=response();raw['choices'][0]['finish_reason']=reason
    with pytest.raises(BusinessError):candidate(raw,'segment')


@pytest.mark.parametrize('model',['',None,[],{'name':'made-up'}])
def test_missing_identity_is_not_replaced_with_an_invented_model(model):
    with pytest.raises(BusinessError):candidate(response(model=model),'segment')


def transport(monkeypatch,handler):
    import apps.deep_dub.nodes.translate as node
    monkeypatch.setattr(node,'value',lambda *args:'https://gateway.invalid/v1')
    monkeypatch.setattr(node,'secret',lambda name:'opaque-test-key')
    monkeypatch.setattr(node,'async_client',lambda **kwargs:httpx.AsyncClient(transport=httpx.MockTransport(handler),**kwargs))
    return node


def test_gateway_uses_supplied_identity_not_source_text_and_records_only_allowlist(monkeypatch):
    requests=[]
    def handler(request):
        requests.append(request);return httpx.Response(200,json=response())
    node=transport(monkeypatch,handler)
    result=asyncio.run(node.translate({'target':{'id':'segment'},'user_id':'forged-source-text'},
        model='auto',user_id='actor',run_id='run-id',attempt_id='attempt'))
    sent=json.loads(requests[0].content)
    assert sent['user']=='actor' and sent['metadata']['user_id']=='actor' and sent['metadata']['session_id']=='run-id'
    assert requests[0].headers['Authorization']=='Bearer opaque-test-key'
    assert result['requested_model']=='auto' and result['reported_model']=='reported-engine'
    assert result['quality_status']=='not_measured' and result['independent_review'] is False


@pytest.mark.parametrize('kind,expected',[('rate','MODEL_RATE_LIMITED'),('timeout','MODEL_TIMEOUT'),
    ('disconnect','MODEL_CONNECTION_FAILED'),('large','MODEL_RESPONSE_TOO_LARGE')])
def test_gateway_failure_is_sanitized_and_never_automatically_retried(monkeypatch,kind,expected):
    calls=[]
    def handler(request):
        calls.append(True)
        if kind=='rate':return httpx.Response(429,text='private upstream detail')
        if kind=='timeout':raise httpx.ReadTimeout('private upstream detail')
        if kind=='disconnect':raise httpx.ConnectError('private upstream detail')
        return httpx.Response(200,content=b'x'*(2*1024**2+1))
    node=transport(monkeypatch,handler)
    with pytest.raises(BusinessError) as error:asyncio.run(node.translate({'target':{'id':'segment'}},
        model='auto',user_id='actor',run_id='run',attempt_id='attempt'))
    assert error.value.code==expected and 'private upstream detail' not in str(error.value)
    assert len(calls)==1
