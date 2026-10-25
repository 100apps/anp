"""固定平台的反向代理：HTTP 流、WebSocket、重定向与 Cookie 路径。"""
from anp_business.shared.telemetry import http_client
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
from anp_business.shared.config import configuration

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
    config_key: str | None = None
    config_group: str = "platforms"
    vendor_auth: bool = False

    @property
    def prefix(self):
        return "/" + self.key if self.key else ""

    @property
    def target(self):
        name = self.env_name or "ANP_PLATFORM_" + self.key.upper() + "_URL"
        fallback = configuration()["supabase"]["url"] if self.env_name == "ANP_PLATFORM_SUPABASE_API_URL" else configuration()[self.config_group].get(self.config_key or self.key, f"http://127.0.0.1:{self.port}{self.upstream_path}")
        target = os.getenv(name, fallback).rstrip("/") + "/"
        parsed = urlsplit(target)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(f"{name} 必须是无凭据、查询参数及片段的 HTTP(S) 地址")
        return target


PLATFORMS = {
    "rustfs": Platform("rustfs", "RustFS 对象存储后台", 59003, "/rustfs", "console/"),
    "dbx": Platform("dbx", "DBX 数据管理", 54424, "/dbx"),
    "langfuse": Platform("langfuse", "Langfuse 可观测性", 53400, "/langfuse", root_slash=False),
    "litellm": Platform("litellm", "LiteLLM 模型网关", 54400, "/litellm", "ui/"),
    "supabase": Platform("supabase", "Supabase 管理台", 53401, "/supabase", root_slash=False),
    "grafana": Platform("grafana", "Grafana 日志与调用链", 53000, "/grafana"),
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
    excluded = HOP_HEADERS | connection | {"forwarded", "x-forwarded-host", "x-forwarded-for", "x-forwarded-proto", "x-forwarded-prefix", "x-anp-user"}
    if websocket:
        excluded |= {"sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol"}
    headers = []
    for key, value in request.headers.items():
        if key.lower() in excluded:
            continue
        if not platform.vendor_auth and key.lower() == "authorization" and value == "Bearer " + request.scope.get("anp_auth_token", ""):
            continue
        if key.lower() == "cookie":
            # 浏览器按路径从具体到通用排列同名 Cookie；不可用字典折叠成最后一个，
            # 否则旧的根路径 NextAuth Cookie 会覆盖 /langfuse 的 CSRF Cookie。
            value = "; ".join(part.strip() for part in value.split(";") if part.strip() and part.partition("=")[0].strip() != "anp_session")
            if not value:
                continue
        headers.append((key, value))
    headers.extend([("x-forwarded-host", request.headers.get("host", "")),
                    ("x-forwarded-proto", "https" if request.url.scheme in ("https", "wss") else "http"),
                    ("x-forwarded-prefix", platform.prefix)])
    if platform.key == "grafana" and request.scope.get("anp_user", {}).get("role") == "admin" and os.getenv("ANP_NATIVE_ROOT"):
        headers.append(("x-anp-user", "admin"))
    if platform.config_key == "rustfs_api":
        headers.append(("host", request.headers.get("host", "")))
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
        morsel["path"] = "/" if morsel.key.startswith("__Host-") else (platform.prefix + ("/" + path.lstrip("/") if path.strip("/") else "") if platform.prefix else path)
        morsel["domain"] = ""
        result.append(morsel.OutputString())
    return result


def response_headers(response, platform, request=None):
    connection = {part.strip().lower() for part in response.headers.get("connection", "").split(",")}
    result = []
    for key, value in response.headers.multi_items():
        if key.lower() in HOP_HEADERS | connection:
            continue
        if key.lower() == "location":
            if platform.key == "storage/v1" and request is not None and urlsplit(value).path.startswith(("/upload/resumable/", "/storage/v1/upload/resumable/")):
                # 官方 Storage 在 production 强制 HTTPS；入口负责恢复实际协议、端口和前缀。
                location = urlsplit(value)
                path = location.path.removeprefix("/storage/v1")
                value = str(request.base_url).rstrip("/") + "/storage/v1" + path + ("?" + location.query if location.query else "")
            else:
                value = rewrite_location(value, platform, response.url)
        if key.lower() == "set-cookie":
            result.extend((b"set-cookie", item.encode("latin-1")) for item in rewrite_cookie(value, platform))
        else:
            result.append((key.encode("latin-1"), value.encode("latin-1")))
    if platform.key == "storage/v1":
        result.append((b"access-control-allow-headers", b"X-Source, X-Client-Info"))
    return result


def unavailable(platform, request):
    headers = {"Retry-After": "5", "Cache-Control": "no-store"}
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{platform.label}暂不可用</title>
<body style="font:16px/1.8 system-ui;margin:0;padding:24px"><h1>{platform.label}暂不可用</h1>
<p>统一入口已配置，上游服务尚未启动或当前不可达。</p><p>启动本项目对应平台后刷新即可。连接外部平台时，请配置后台的平台上游地址。</p>
<p><a href="/workbench/">返回工作台</a></p></body></html>""", status_code=503, headers=headers)
    return JSONResponse({"platform": platform.key, "status": "unavailable", "detail": "上游平台尚未启动或不可达"}, status_code=503, headers=headers)


async def proxy_http(request, platform, path):
    try:
        url = target_url(platform, path, request.scope.get("query_string", b""))
    except ValueError as error:
        return JSONResponse({"detail": str(error)}, status_code=400)
    client = http_client(timeout=httpx.Timeout(connect=5, read=None, write=60, pool=5), follow_redirects=False, trust_env=False)
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
    result.raw_headers = response_headers(response, platform, request)
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
        # 官方 Studio 的 Next.js 产物使用根路径，无法仅靠反向代理变成子路径。
        # 管理入口跳转至配置的独立 Studio 地址，避免资源和 API 路径损坏。
        if configuration()["ingress"]["enabled"] and platform.key in {"supabase", "litellm"}:
            return RedirectResponse("/project/default" if platform.key == "supabase" else "/litellm/ui/", status_code=307)
        if platform.key in {"supabase", "litellm"}:
            return RedirectResponse(platform.target + platform.entry, status_code=307)
        if not platform.root_slash and not platform.entry:
            return await proxy_http(request, platform, "")
        query = request.url.query
        return RedirectResponse(platform.prefix + "/" + platform.entry + ("?" + query if query else ""), status_code=307)
    async def endpoint(request: Request, path: str):
        return await proxy_http(request, platform, path)
    async def socket(websocket: WebSocket, path: str):
        await proxy_websocket(websocket, platform, path)
    router.add_api_route(platform.prefix, index, methods=["GET", "HEAD"], include_in_schema=False)
    if platform.entry or platform.key == "supabase":
        router.add_api_route(platform.prefix + "/", index, methods=["GET", "HEAD"], include_in_schema=False)
    router.add_api_route(platform.prefix + "/{path:path}", endpoint,
                         methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
    router.add_api_websocket_route(platform.prefix + "/{path:path}", socket)


# RustFS 管理 API 与静态后台分端口；保持签名请求的路径及 Host。
@router.api_route("/rustfs/admin/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)
async def rustfs_admin(request: Request, path: str):
    platform = Platform("rustfs", "RustFS S3 管理 API", 59002, "", config_key="rustfs_api", env_name="ANP_PLATFORM_RUSTFS_API_URL")
    return await proxy_http(request, platform, "rustfs/admin/" + path)


# Supabase 控制台与 REST/Auth/Storage 共用一个目录，API 分支先匹配。
for branch in ("rest", "auth", "storage", "realtime"):
    def api_endpoint(branch):
        async def supabase_api(request: Request, path: str):
            return await proxy_http(request, api_gateway(), f"{branch}/v1/{path}")
        return supabase_api
    router.add_api_route(f"/supabase/{branch}/v1/{{path:path}}", api_endpoint(branch),
                         methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], include_in_schema=False)


def api_gateway():
    # 与Studio的上游配置独立，避免API请求递归进入管理台。
    return Platform("supabase", "Supabase API", 58400, "", env_name="ANP_PLATFORM_SUPABASE_API_URL")


@router.websocket('/supabase/realtime/v1/{path:path}')
async def supabase_realtime_socket(websocket: WebSocket, path: str):
    await proxy_websocket(websocket, api_gateway(), 'realtime/v1/' + path)


for platform in PLATFORMS.values():
    register(platform)


@router.get("/api/platforms")
async def list_platforms():
    async def probe(platform):
        try:
            async with http_client(timeout=2, trust_env=False, follow_redirects=False) as client:
                response = await client.get(platform.target + platform.entry)
            status = "available" if response.status_code < 500 else "unavailable"
        except (ValueError, httpx.HTTPError):
            status = "unavailable"
        return {"id": platform.key, "name": platform.label, "path": platform.prefix + ("" if platform.key in {"supabase", "litellm"} else "/"), "status": status}
    return await asyncio.gather(*(probe(platform) for platform in PLATFORMS.values()))


def rustfs_signed(scope):
    if scope.get("type") != "http":
        return False
    headers = {k.lower():v for k,v in scope.get("headers", [])}
    from urllib.parse import parse_qs
    return headers.get(b"authorization", b"").startswith(b"AWS4-HMAC-SHA256 ") or parse_qs(scope.get("query_string", b"").decode("utf-8", errors="replace")).get("X-Amz-Algorithm") == ["AWS4-HMAC-SHA256"]


class RustFSS3Proxy:
    """控制台的 S3 请求保留根路径与 Host，避免改写导致 SigV4 验签失败。"""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not rustfs_signed(scope) or scope.get("path", "").startswith("/rustfs/"):
            return await self.app(scope, receive, send)
        platform = Platform("rustfs", "RustFS S3 API", 59002, "", config_key="rustfs_api", env_name="ANP_PLATFORM_RUSTFS_API_URL")
        response = await proxy_http(Request(scope, receive), platform, scope.get("path", "/").lstrip("/"))
        return await response(scope, receive, send)
