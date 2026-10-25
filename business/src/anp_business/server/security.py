"""HTTP 扩展的管理员边界；Agent Server 内置 API 使用 Auth 插件。"""
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from langgraph_sdk import Auth
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection

from anp_business.server.platforms import rustfs_signed
from anp_business.server.auth import access_token, current_user, same_origin
from anp_business.server.ingress import route as ingress_route

ADMIN_PREFIXES = ("/rustfs","/dbx", "/langfuse", "/litellm", "/supabase", "/grafana", "/api/platforms", "/api/console")


class PlatformAccess:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        ingress = ingress_route(scope)
        protected = rustfs_signed(scope) or bool(ingress and ingress[2]) or any(path == prefix or path.startswith(prefix + "/") for prefix in ADMIN_PREFIXES)
        if scope["type"] not in ("http", "websocket") or not protected:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        # Langfuse SDK 的 public API 使用项目密钥，由上游校验；不依赖人的控制台 Cookie。
        # UI、trpc 与平台凭据接口仍必须经过下面的 Supabase 管理员验证。
        machine_api = (path.startswith("/langfuse/api/public/") or bool(ingress and ingress[0].key == "litellm")) and scope["type"] == "http"
        if machine_api and headers.get("authorization", "").lower().startswith(("basic ", "bearer ")):
            return await self.app(scope, receive, send)
        try:
            user = await current_user(headers)
            if user["role"] != "admin":
                raise Auth.exceptions.HTTPException(status_code=403, detail="仅管理员可以访问平台管理功能")
            if (scope["type"] == "websocket" or scope["method"] not in ("GET", "HEAD", "OPTIONS")) and not same_origin(HTTPConnection(scope)):
                raise Auth.exceptions.HTTPException(status_code=403, detail="请求来源不被允许")
            scope["anp_user"] = user
            scope["anp_auth_token"] = access_token(headers)
        except Auth.exceptions.HTTPException as error:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4401 if error.status_code == 401 else 4403})
                return
            result = RedirectResponse("/workbench/?login=required") if error.status_code == 401 and "text/html" in headers.get("accept", "") else JSONResponse({"detail": error.detail}, status_code=error.status_code)
            return await result(scope, receive, send)
        return await self.app(scope, receive, send)
