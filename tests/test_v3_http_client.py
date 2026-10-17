"""证书缓存须保留 TLS 校验和客户端之间的身份/Cookie 隔离。"""
import asyncio
import ssl
from anp.sdk.http import async_client,trust_context


def test_cached_trust_keeps_certificate_and_hostname_verification():
    context=trust_context()
    assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
    assert context.cert_store_stats()['x509_ca']>0
    assert trust_context() is context


def test_clients_keep_headers_cookies_and_lifetime_separate():
    async def scenario():
        async with async_client(headers={'Authorization':'Bearer alice'},timeout=10) as first:
            async with async_client(headers={'Authorization':'Bearer bob'},timeout=30) as second:
                first.cookies.set('session','alice-only')
                assert 'session' not in second.cookies
                assert first.headers['Authorization']=='Bearer alice'
                assert second.headers['Authorization']=='Bearer bob'
                assert first.timeout.read==10 and second.timeout.read==30
            assert second.is_closed and not first.is_closed
        assert first.is_closed
    asyncio.run(scenario())
