"""认证边界契约；真实 Supabase 验收另有运行证据。"""
import httpx
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from langgraph_sdk import Auth
from starlette.websockets import WebSocketDisconnect

from anp_business.server import auth, security
from anp_business.server.platforms import PLATFORMS, forwarded_headers
from starlette.requests import Request


@pytest.mark.asyncio
async def test_only_trusted_app_metadata_grants_admin(monkeypatch):
    async def response(*args, **kwargs):
        return httpx.Response(200, json={"id": "u1", "user_metadata": {"anp_role": "admin"}, "app_metadata": {}})
    monkeypatch.setattr(auth, "supabase", response)
    assert (await auth.current_user({"authorization": "Bearer test"}))["role"] == "user"


@pytest.mark.asyncio
async def test_missing_or_rejected_identity_does_not_authenticate(monkeypatch):
    async def rejected(*args, **kwargs):
        return httpx.Response(401)
    monkeypatch.setattr(auth, "supabase", rejected)
    for headers in ({}, {"authorization": "Bearer expired"}):
        with pytest.raises(Auth.exceptions.HTTPException) as error:
            await auth.current_user(headers)
        assert error.value.status_code == 401


@pytest.fixture
def client(monkeypatch):
    async def user(headers):
        token = auth.access_token(headers)
        if not token:
            raise Auth.exceptions.HTTPException(status_code=401, detail="请先登录")
        return {"id": "u", "role": "admin" if token == "admin" else "user"}
    monkeypatch.setattr(security, "current_user", user)
    app = FastAPI()
    app.include_router(auth.router)
    @app.get("/api/platforms")
    def platforms():
        return {"ok": True}
    @app.get("/langfuse/api/public/projects")
    def machine_api():
        return {"fixture_only": True}
    @app.post("/dbx/write")
    def write():
        return {"ok": True}
    @app.websocket("/dbx/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        await ws.send_text("ok")
        await ws.close()
    app.add_middleware(security.PlatformAccess)
    with TestClient(app) as result:
        yield result


def test_anonymous_and_non_admin_cannot_access_platforms(client):
    assert client.get("/api/platforms").status_code == 401
    assert client.get("/api/platforms", headers={"authorization": "Bearer user"}).status_code == 403
    assert client.get("/api/platforms", headers={"authorization": "Bearer admin"}).status_code == 200


def test_cross_origin_writes_and_websockets_are_denied(client):
    headers = {"authorization": "Bearer admin", "origin": "https://foreign.invalid"}
    assert client.post("/dbx/write", headers=headers).status_code == 403
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/dbx/ws", headers=headers):
            pass
    assert error.value.code == 4403
    with client.websocket_connect("/dbx/ws", headers={**headers, "origin": "http://testserver"}) as websocket:
        assert websocket.receive_text() == "ok"


def test_proxy_strips_business_identity_but_preserves_vendor_token():
    def headers(token):
        scope = {"type": "http", "method": "GET", "path": "/dbx/", "scheme": "http", "server": ("localhost", 80),
                 "headers": [(b"cookie", b"anp_session=business; dbx_session=vendor"), (b"authorization", token)],
                 "anp_auth_token": "business"}
        return dict(forwarded_headers(Request(scope), PLATFORMS["dbx"]))
    own = headers(b"Bearer business")
    assert "authorization" not in own and own["cookie"] == "dbx_session=vendor"
    vendor = headers(b"Bearer vendor")
    assert vendor["authorization"] == "Bearer vendor"
    assert auth.access_token({"cookie": "anp_session=business", "authorization": "Bearer vendor"}) == "business"


def test_proxy_preserves_specific_cookie_before_legacy_root_cookie():
    scope = {"type": "http", "method": "GET", "path": "/langfuse/api/auth/csrf", "scheme": "http",
             "server": ("localhost", 80), "headers": [(b"cookie", b"next-auth.csrf-token=specific; anp_session=secret; next-auth.csrf-token=legacy-root")]}
    headers = dict(forwarded_headers(Request(scope), PLATFORMS["langfuse"]))
    assert headers["cookie"] == "next-auth.csrf-token=specific; next-auth.csrf-token=legacy-root"


def test_login_rejects_malformed_json_and_foreign_origin(client):
    assert client.post("/api/auth/login", json=[]).status_code == 400
    assert client.post("/api/auth/login", json={}, headers={"origin": "https://foreign.invalid"}).status_code == 403


def test_langfuse_machine_api_delegates_key_validation_without_opening_ui(client):
    # 这里只检查路由边界；真实项目密钥与错误密钥另做上游验收。
    assert client.get("/langfuse/api/public/projects").status_code == 401
    assert client.get("/langfuse/api/public/projects",headers={"authorization":"Basic fixture"}).status_code == 200
    assert client.get("/langfuse/",headers={"authorization":"Basic fixture"}).status_code == 401


@pytest.mark.parametrize("suffix", ["", "?X-Amz-Algorithm=AWS4-HMAC-SHA256"])
def test_rustfs_root_s3_requires_console_admin(client, suffix):
    signed = {"authorization":"AWS4-HMAC-SHA256 fixture-signature"}
    assert client.get("/" + suffix, headers=signed).status_code == 401
    assert client.get("/" + suffix, headers={**signed,"cookie":"anp_session=user"}).status_code == 403


def test_unrelated_unicode_query_is_not_intercepted_as_s3(client):
    assert client.get("/?name=%E4%B8%AD%E6%96%87").status_code == 404
