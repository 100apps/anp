import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from anp.errors import BusinessError
from apps.deep_dub import cli


def test_http_cli_reports_validation_reason_without_echoing_request_or_context(monkeypatch):
    async def actor():return SimpleNamespace(token='fixture-only',workspace_id='workspace')
    async def request(*args,**kwargs):
        return httpx.Response(422,json={'detail':[{'msg':'正文通过需要人工或独立模型复核',
            'input':{'secret':'do-not-echo-input'},'ctx':{'error':'do-not-echo-context'}}]})
    @asynccontextmanager
    async def client(**kwargs):yield SimpleNamespace(request=request)
    monkeypatch.setattr(cli,'current_actor',actor);monkeypatch.setattr(cli,'async_client',client)
    with pytest.raises(BusinessError) as raised:
        asyncio.run(cli.invoke(SimpleNamespace(obj={'transport':'http','endpoint':'http://fixture.invalid'}),
                               'POST','/audits',None,{'fixture_only':True}))
    assert raised.value.code=='VALIDATION_ERROR'
    assert raised.value.message=='正文通过需要人工或独立模型复核'
    assert 'do-not-echo' not in raised.value.message
