"""HTTP 扩展的管理员边界；Agent Server 内置 API 使用 Auth 插件。"""
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from langgraph_sdk import Auth
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection

from anp_business.server.auth import access_token, current_user, same_origin

ADMIN_PREFIXES = ("/dbx", "/langfuse", "/litellm", "/supabase", "/api/platforms")


class PlatformAccess:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        protected = any(path == prefix or path.startswith(prefix + "/") for prefix in ADMIN_PREFIXES)
        if scope["type"] not in ("http", "websocket") or not protected:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        try:
            user = await current_user(headers)
            if user["role"] != "admin":
                raise Auth.exceptions.HTTPException(status_code=403, detail="仅管理员可以访问平台管理功能")
            if (scope["type"] == "websocket" or scope["method"] not in ("GET", "HEAD", "OPTIONS")) and not same_origin(HTTPConnection(scope)):
                raise Auth.exceptions.HTTPException(status_code=403, detail="请求来源不被允许")
            scope["anp_auth_token"] = access_token(headers)
        except Auth.exceptions.HTTPException as error:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4401 if error.status_code == 401 else 4403})
                return
            result = RedirectResponse("/workbench/?login=required") if error.status_code == 401 and "text/html" in headers.get("accept", "") else JSONResponse({"detail": error.detail}, status_code=error.status_code)
            return await result(scope, receive, send)
        return await self.app(scope, receive, send)
