"""Agent Server 内的原生统一入口；固定上游，管理页面仍通过管理员鉴权。"""
from fastapi import Request
from fastapi.responses import JSONResponse

from anp_business.shared.config import configuration
from anp_business.server.platforms import Platform, proxy_http


def route(scope):
    if scope.get("type") != "http" or not configuration()["ingress"]["enabled"]:
        return None
    path = scope.get("path", "")
    for branch, port in (("auth", 59999), ("rest", 53300), ("storage", 55000)):
        prefix = "/" + branch + "/v1"
        if path == prefix or path.startswith(prefix + "/"):
            return Platform(branch + "/v1", "Supabase " + branch, port, "", config_key=branch,
                            config_group="ingress", vendor_auth=True), path[len(prefix):].lstrip("/"), False
    # 官方 Studio 是 Next.js 根路径产物；为其保留原始资源/API路径，保护所有这些入口。
    if any(path == prefix or path.startswith(prefix + "/") for prefix in ("/project", "/projects", "/api/platform", "/_next", "/img", "/images", "/icons", "/fonts", "/favicon.ico", "/manifest.json")):
        return Platform("", "Supabase Studio", 53200, "", config_key="studio", config_group="ingress"), path.lstrip("/"), True
    if path == "/litellm/v1" or path.startswith("/litellm/v1/"):
        return Platform("litellm", "LiteLLM API", 54000, "", vendor_auth=True), path[len("/litellm/"):], False
    return None


class NativeIngress:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        match = route(scope)
        if match:
            platform, path, _ = match
            response = await proxy_http(Request(scope, receive), platform, path)
            return await response(scope, receive, send)
        if configuration()["ingress"]["enabled"] and scope.get("type") == "http" and scope.get("path", "").startswith("/realtime/"):
            return await JSONResponse({"detail": "Realtime 未包含在此版本"}, status_code=404)(scope, receive, send)
        return await self.app(scope, receive, send)
