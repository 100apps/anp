"""Range边界和独立HTTP资源收尾，避免超长输入或取消泄漏连接。"""
import asyncio

import pytest

from anp.errors import BusinessError
from apps.deep_dub.services.media import byte_range,MediaStream


def test_single_range_boundaries_and_if_invalid():
    assert byte_range(None,100) is None
    assert byte_range('bytes=0-9',100)==(0,9)
    assert byte_range('bytes=90-',100)==(90,99)
    assert byte_range('bytes=-10',100)==(90,99)
    assert byte_range('bytes=-200',100)==(0,99)
    assert byte_range('bytes=90-200',100)==(90,99)
    for value in ('bytes=100-','bytes=20-10','bytes=-0','bytes=-','bytes=0-1,4-6','abc','bytes='+'9'*5000+'-'):
        assert byte_range(value,100) is False
    assert byte_range('bytes=0-',0) is False


def test_stream_closes_owned_response_and_client_on_consumer_stop_and_invalid_length():
    async def run():
        class Response:
            closed=False
            async def aiter_raw(self,**kwargs):
                yield b'a';yield b'b'
            async def aclose(self):self.closed=True
        class Client:
            closed=False
            async def aclose(self):self.closed=True
        response,client=Response(),Client()
        stream=MediaStream(200,{'Content-Length':'2'},response,client)
        iterator=stream.chunks()
        assert await anext(iterator)==b'a'
        await iterator.aclose()
        assert response.closed and client.closed
        response,client=Response(),Client()
        stream=MediaStream(200,{'Content-Length':'1'},response,client)
        with pytest.raises(BusinessError) as error:
            async for _ in stream.chunks():pass
        assert error.value.code=='MEDIA_UPSTREAM_INVALID' and response.closed and client.closed
    asyncio.run(run())
