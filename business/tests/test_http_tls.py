"""TLS trust is retained while avoiding per-request certificate loading."""
import asyncio
import ssl
from anp_business.shared.telemetry import http_client


def test_http_clients_reuse_verified_tls_context_without_sharing_sessions():
    async def check():
        async with http_client(trust_env=False) as first, http_client(trust_env=False) as second:
            left = first._transport._pool._ssl_context
            right = second._transport._pool._ssl_context
            assert left is right
            assert left.verify_mode == ssl.CERT_REQUIRED and left.check_hostname
            assert first.cookies is not second.cookies
            assert first._transport is not second._transport
    asyncio.run(check())
