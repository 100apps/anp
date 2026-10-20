"""真实HTTP/WebSocket上游的代理契约测试；不冒充四个平台的产品验收。"""
import asyncio
import gzip
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.testclient import TestClient

from anp_business.server.platforms import PLATFORMS, router, target_url


@pytest.fixture(scope="module")
def upstream():
    app = FastAPI()

    @app.websocket("/up/ws")
    async def echo(websocket: WebSocket):
        await websocket.accept(subprotocol="echo")
        while True:
            value = await websocket.receive()
            if value["type"] == "websocket.disconnect":
                return
            if value.get("bytes") is not None:
                await websocket.send_bytes(value["bytes"])
            else:
                await websocket.send_text(value["text"])

    @app.api_route("/up", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    @app.api_route("/up/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def endpoint(request: Request, path: str = ""):
        if path == "redirect":
            response = RedirectResponse("/up/login?next=%2Fup%2Fhome", status_code=307)
            response.set_cookie("session", "one", path="/", httponly=True, samesite="lax")
            response.set_cookie("other", "two", path="/up/api", domain="127.0.0.1")
            return response
        if path == "external":
            return RedirectResponse("https://login.example.org/authorize")
        if path == "host-cookie":
            response = Response("ok")
            response.set_cookie("__Host-csrf", "proof", secure=True, path="/")
            return response
        if path == "gzip":
            raw = gzip.compress(b"compressed static asset")
            return Response(raw, headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"}, media_type="text/javascript")
        if path == "events":
            async def events():
                yield "event: update\ndata: first\n\n"
                await asyncio.sleep(.01)
                yield "event: done\ndata: complete\n\n"
            return StreamingResponse(events(), media_type="text/event-stream")
        if path == "range":
            return Response(b"2345", status_code=206, headers={"Content-Range": "bytes 2-5/10", "Accept-Ranges": "bytes"})
        return {"path": path, "method": request.method, "query": request.scope["query_string"].decode(),
                "body": (await request.body()).decode(), "headers": dict(request.headers)}

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    yield f"http://127.0.0.1:{port}/up"
    server.should_exit = True
    thread.join(5)
    sock.close()


@pytest.fixture
def client(monkeypatch, upstream):
    for name in ("DBX", "LANGFUSE", "LITELLM", "SUPABASE", "SUPABASE_API"):
        monkeypatch.setenv(f"ANP_PLATFORM_{name}_URL", upstream)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("name", ["dbx", "langfuse", "litellm", "supabase"])
def test_canonical_entry_and_query(client, name):
    response = client.get(f"/{name}?next=%2Fhome", follow_redirects=False)
    if name in ("langfuse", "supabase"):
        assert response.status_code == 200 and response.json()["query"] == "next=%2Fhome"
        return
    assert response.status_code == 307
    expected = "/litellm/ui/" if name == "litellm" else f"/{name}/"
    assert response.headers["location"] == expected + "?next=%2Fhome"


def test_request_body_query_auth_and_untrusted_forwarding(client):
    response = client.post("/dbx/echo?tag=a&tag=b&value=%E4%B8%AD%E6%96%87", content="中文正文".encode(),
        headers={"Authorization": "Bearer client-token", "Content-Type": "application/json", "Connection": "x-remove",
                 "x-remove": "secret-hop", "X-Forwarded-Host": "attacker.example"})
    value = response.json()
    assert value["method"] == "POST" and value["path"] == "echo"
    assert value["body"] == "中文正文" and value["query"] == "tag=a&tag=b&value=%E4%B8%AD%E6%96%87"
    assert value["headers"]["authorization"] == "Bearer client-token"
    assert value["headers"]["x-forwarded-host"] == "testserver"
    assert "x-remove" not in value["headers"]


def test_encoded_path_characters_remain_path_not_query_or_fragment(client):
    value = client.get("/dbx/%E4%B8%AD%E6%96%87%3Ffile%23part%25?real=query").json()
    assert value["path"] == "中文?file#part%" and value["query"] == "real=query"


def test_redirect_and_multiple_cookie_scope(client):
    response = client.get("/langfuse/redirect", follow_redirects=False)
    assert response.headers["location"] == "/langfuse/login?next=%2Fup%2Fhome"
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 2
    assert "Path=/langfuse;" in cookies[0] and "HttpOnly" in cookies[0]
    assert "Path=/langfuse/api" in cookies[1] and "Domain=" not in cookies[1]
    external = client.get("/langfuse/external", follow_redirects=False)
    assert external.headers["location"] == "https://login.example.org/authorize"
    assert "Path=/;" in client.get("/dbx/host-cookie").headers["set-cookie"]


def test_compressed_assets_sse_and_range(client):
    compressed = client.get("/supabase/gzip")
    assert compressed.headers["content-encoding"] == "gzip"
    assert compressed.text == "compressed static asset"
    events = client.get("/dbx/events")
    assert "text/event-stream" in events.headers["content-type"] and "event: done" in events.text
    ranged = client.get("/dbx/range", headers={"Range": "bytes=2-5"})
    assert ranged.status_code == 206 and ranged.content == b"2345"
    assert ranged.headers["content-range"] == "bytes 2-5/10"


@pytest.mark.parametrize("branch", ["auth", "rest", "storage"])
def test_supabase_apis_do_not_hit_studio(client, branch):
    assert client.get(f"/supabase/{branch}/v1/items").json()["path"] == f"{branch}/v1/items"


def test_websocket_text_binary_and_subprotocol(client):
    with client.websocket_connect("/dbx/ws", subprotocols=["echo"]) as websocket:
        assert websocket.accepted_subprotocol == "echo"
        websocket.send_text("实时消息")
        assert websocket.receive_text() == "实时消息"
        websocket.send_bytes(b"binary-payload")
        assert websocket.receive_bytes() == b"binary-payload"


def test_unavailable_is_explicit_without_private_upstream(client, monkeypatch):
    monkeypatch.setenv("ANP_PLATFORM_DBX_URL", "http://127.0.0.1:1/private")
    response = client.get("/dbx/", headers={"Accept": "text/html"})
    assert response.status_code == 503 and "上游服务尚未启动" in response.text
    assert ":1/private" not in response.text
    assert response.headers["retry-after"] == "5"


def test_targets_and_escaped_paths_are_not_user_controlled(client):
    platform = PLATFORMS["dbx"]
    assert target_url(platform, "//attacker.example/path").host == "127.0.0.1"
    for path in ("%252e%252e/private", "foo/../private", "foo\\private"):
        with pytest.raises(ValueError):
            target_url(platform, path)


def test_platform_list_only_exposes_public_links(client):
    result = client.get("/api/platforms").json()
    assert len(result) == 4 and all(item["status"] == "available" for item in result)
    assert all(set(item) == {"id", "name", "path", "status"} for item in result)
