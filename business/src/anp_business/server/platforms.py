"""固定平台的反向代理：HTTP 流、WebSocket、重定向与 Cookie 路径。"""
import asyncio
import os
from dataclasses import dataclass
from http.cookies import SimpleCookie
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

router = APIRouter()
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
               "transfer-encoding", "upgrade", "host"}


@dataclass(frozen=True)
class Platform:
    key: str
    label: str
    port: int
    upstream_path: str
    entry: str = ""
    env_name: str | None = None
    root_slash: bool = True

    @property
    def prefix(self):
        return "/" + self.key

    @property
    def target(self):
        name = self.env_name or "ANP_PLATFORM_" + self.key.upper() + "_URL"
        target = os.getenv(name, f"http://127.0.0.1:{self.port}{self.upstream_path}").rstrip("/") + "/"
        parsed = urlsplit(target)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"{name} 必须是无凭据、查询参数及片段的 HTTP(S) 地址")
        return target


PLATFORMS = {
    "dbx": Platform("dbx", "DBX 数据管理", 54424, "/dbx"),
    "langfuse": Platform("langfuse", "Langfuse 可观测性", 53400, "/langfuse", root_slash=False),
    "litellm": Platform("litellm", "LiteLLM 模型网关", 54400, "/litellm", "ui/"),
    "supabase": Platform("supabase", "Supabase 管理台", 53401, "/supabase", root_slash=False),
}


def target_url(platform, path, query=b""):
    # 目的地址只来自后台白名单配置，用户路径不能变成另一台主机或逃逸既定前缀。
    decoded = path
    for _ in range(3):
        decoded = unquote(decoded)
    if "\\" in decoded or any(part in (".", "..") for part in decoded.split("/")):
        raise ValueError("平台路径包含无效片段")
    encoded = quote(path.lstrip("/"), safe="/:@!$&'()*+,;=-._~")
    base = platform.target.rstrip("/") if not path and not platform.root_slash else platform.target + encoded
    return httpx.URL(base).copy_with(query=query)


def forwarded_headers(request, platform, websocket=False):
    connection = {part.strip().lower() for part in request.headers.get("connection", "").split(",")}
    excluded = HOP_HEADERS | connection | {"forwarded", "x-forwarded-host", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-prefix"}
    if websocket:
        excluded |= {"sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol"}
    headers = [(key, value) for key, value in request.headers.items() if key.lower() not in excluded]
    headers.extend([("x-forwarded-host", request.headers.get("host", "")),
                    ("x-forwarded-proto", "https" if request.url.scheme in ("https", "wss") else "http"),
                    ("x-forwarded-prefix", platform.prefix)])
    if request.client:
        headers.append(("x-forwarded-for", request.client.host))
    return headers


def rewrite_location(location, platform, upstream_url):
    absolute = urlsplit(urljoin(str(upstream_url), location))
    target = urlsplit(platform.target)
    if (absolute.scheme, absolute.netloc) != (target.scheme, target.netloc):
        return location  # 保留真实的外部 OAuth 等跳转。
    base = target.path.rstrip("/")
    path = absolute.path
    if base and (path == base or path.startswith(base + "/")):
        path = path[len(base):]
    result = platform.prefix + ("/" + path.lstrip("/") if path else "")
    return result + ("?" + absolute.query if absolute.query else "") + ("#" + absolute.fragment if absolute.fragment else "")


def rewrite_cookie(value, platform):
    cookie = SimpleCookie()
    cookie.load(value)
    if not cookie:
        return []
    result = []
    base = urlsplit(platform.target).path.rstrip("/")
    for morsel in cookie.values():
        path = morsel["path"] or "/"
        if base and (path == base or path.startswith(base + "/")):
            path = path[len(base):] or "/"
        morsel["path"] = "/" if morsel.key.startswith("__Host-") else platform.prefix + ("/" + path.lstrip("/") if path.strip("/") else "")
        morsel["domain"] = ""
        result.append(morsel.OutputString())
    return result


def response_headers(response, platform):
    connection = {part.strip().lower() for part in response.headers.get("connection", "").split(",")}
    result = []
    for key, value in response.headers.multi_items():
        if key.lower() in HOP_HEADERS | connection:
            continue
        if key.lower() == "location":
            value = rewrite_location(value, platform, response.url)
        if key.lower() == "set-cookie":
            result.extend((b"set-cookie", item.encode("latin-1")) for item in rewrite_cookie(value, platform))
        else:
            result.append((key.encode("latin-1"), value.encode("latin-1")))
    return result


def unavailable(platform, request):
    headers = {"Retry-After": "5", "Cache-Control": "no-store"}
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{platform.label}暂不可用</title>
<body style="font:16px/1.8 system-ui;max-width:680px;margin:12vh auto;padding:24px"><h1>{platform.label}暂不可用</h1>
<p>统一入口已配置，上游服务尚未启动或当前不可达。</p><p>启动本项目对应平台后刷新即可。连接外部平台时，请配置后台的平台上游地址。</p>
<p><a href="/workbench/">返回工作台</a></p></body></html>""", status_code=503, headers=headers)
    return JSONResponse({"platform": platform.key, "status": "unavailable", "detail": "上游平台尚未启动或不可达"}, status_code=503, headers=headers)


async def proxy_http(request, platform, path):
    try:
        url = target_url(platform, path, request.scope.get("query_string", b""))
    except ValueError as error:
        return JSONResponse({"detail": str(error)}, status_code=400)
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=None, write=60, pool=5), follow_redirects=False, trust_env=False)
    try:
        content = request.stream() if "content-length" in request.headers or "transfer-encoding" in request.headers else None
        upstream = client.build_request(request.method, url, headers=forwarded_headers(request, platform), content=content)
        response = await client.send(upstream, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        return unavailable(platform, request)
    async def stream():
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()
    result = StreamingResponse(stream(), status_code=response.status_code)
    result.raw_headers = response_headers(response, platform)
    return result


async def proxy_websocket(websocket, platform, path):
    try:
        url = target_url(platform, path, websocket.scope.get("query_string", b""))
        url = str(url.copy_with(scheme="wss" if url.scheme == "https" else "ws"))
        protocols = websocket.scope.get("subprotocols", [])
        async with connect(url, additional_headers=forwarded_headers(websocket, platform, websocket=True),
                           subprotocols=protocols or None, proxy=None, open_timeout=5, max_size=16 * 1024 ** 2) as upstream:
            await websocket.accept(subprotocol=upstream.subprotocol)
            async def to_upstream():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    await upstream.send(message.get("bytes") if message.get("bytes") is not None else message["text"])
            async def to_client():
                async for message in upstream:
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)
            tasks = [asyncio.create_task(to_upstream()), asyncio.create_task(to_client())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await websocket.close()
                except RuntimeError:
                    pass
    except (OSError, ValueError, httpx.HTTPError, WebSocketException, WebSocketDisconnect):
        if websocket.application_state.name != "DISCONNECTED":
            await websocket.close(code=1013)


def register(platform):
    async def index(request: Request):
        if not platform.root_slash and not platform.entry:
            return await proxy_http(request, platform, "")
        query = request.url.query
        return RedirectResponse(platform.prefix + "/" + platform.entry + ("?" + query if query else ""), status_code=307)
    async def endpoint(request: Request, path: str):
        return await proxy_http(request, platform, path)
    async def socket(websocket: WebSocket, path: str):
        await proxy_websocket(websocket, platform, path)
    router.add_api_route(platform.prefix, index, methods=["GET", "HEAD"], include_in_schema=False)
    if platform.entry:
        router.add_api_route(platform.prefix + "/", index, methods=["GET", "HEAD"], include_in_schema=False)
    router.add_api_route(platform.prefix + "/{path:path}", endpoint,
                         methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    router.add_api_websocket_route(platform.prefix + "/{path:path}", socket)


# Supabase 控制台与 REST/Auth/Storage 共用一个目录，API 分支先匹配。
for branch in ("rest", "auth", "storage"):
    def api_endpoint(branch):
        async def supabase_api(request: Request, path: str):
            return await proxy_http(request, api_gateway(), f"{branch}/v1/{path}")
        return supabase_api
    router.add_api_route(f"/supabase/{branch}/v1/{{path:path}}", api_endpoint(branch),
                         methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)


def api_gateway():
    # 与Studio的上游配置独立，避免API请求递归进入管理台。
    return Platform("supabase", "Supabase API", 58400, "", env_name="ANP_PLATFORM_SUPABASE_API_URL")


for platform in PLATFORMS.values():
    register(platform)


@router.get("/api/platforms")
async def list_platforms():
    async def probe(platform):
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False, follow_redirects=False) as client:
                response = await client.get(platform.target + platform.entry)
            status = "available" if response.status_code < 500 else "unavailable"
        except (ValueError, httpx.HTTPError):
            status = "unavailable"
        return {"id": platform.key, "name": platform.label, "path": platform.prefix + "/", "status": status}
    return await asyncio.gather(*(probe(platform) for platform in PLATFORMS.values()))
