"""Supabase 验证登录身份；LangGraph 负责线程访问范围。"""
from anp_business.shared.telemetry import http_client
from http.cookies import SimpleCookie

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from langgraph_sdk import Auth

from anp_business.shared.config import configuration

COOKIE = "anp_session"
auth = Auth()
router = APIRouter(prefix="/api/auth")


def access_token(headers):
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("cookie", ""))
    except Exception:
        return ""
    if COOKIE in cookie:
        return cookie[COOKIE].value
    authorization = headers.get("authorization", "")
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""


async def supabase(method, path, *, token="", data=None):
    config = configuration()["supabase"]
    headers = {"apikey": config["anon_key"]} if config["anon_key"] else {}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        async with http_client(timeout=10, trust_env=False) as client:
            return await client.request(method, config["url"].rstrip("/") + "/auth/v1/" + path, headers=headers, json=data)
    except httpx.HTTPError:
        raise Auth.exceptions.HTTPException(status_code=503, detail="Supabase 认证服务不可达") from None


async def current_user(headers):
    token = access_token(headers)
    if not token:
        raise Auth.exceptions.HTTPException(status_code=401, detail="请先登录")
    response = await supabase("GET", "user", token=token)
    if response.status_code != 200:
        raise Auth.exceptions.HTTPException(status_code=401 if response.status_code < 500 else 503, detail="登录已失效" if response.status_code < 500 else "认证服务暂不可用")
    value = response.json()
    if not value.get("id"):
        raise Auth.exceptions.HTTPException(status_code=401, detail="认证响应无效")
    # 用户可编辑 user_metadata；只信任由管理员维护的 app_metadata。
    return {"id": value["id"], "email": value.get("user_metadata", {}).get("display_email") or value.get("user_metadata", {}).get("display_name") or value.get("email", ""),
            "role": "admin" if value.get("app_metadata", {}).get("anp_role") == "admin" else "user"}


@auth.authenticate
async def authenticate(headers: dict) -> Auth.types.MinimalUserDict:
    normalized = {(k.decode() if isinstance(k, bytes) else k).lower(): v.decode() if isinstance(v, bytes) else v for k, v in headers.items()}
    user = await current_user(normalized)
    return {"identity": user["id"], "display_name": user["email"], "permissions": [user["role"]]}


@auth.on.threads
async def own_threads(ctx: Auth.types.AuthContext, value: dict):
    filters = {"owner": ctx.user.identity}
    value.setdefault("metadata", {}).update(filters)
    return filters


@auth.on.assistants
async def assistant_access(ctx: Auth.types.AuthContext, value: dict):
    if ctx.action not in ("read", "search") and "admin" not in ctx.permissions:
        raise Auth.exceptions.HTTPException(status_code=403, detail="只有管理员可以修改助手配置")
    return {}


@auth.on.store
async def own_store(ctx: Auth.types.AuthContext, value: dict):
    namespace = value.get("namespace", ())
    if not namespace or namespace[0] != ctx.user.identity:
        raise Auth.exceptions.HTTPException(status_code=403, detail="不可访问其他用户的数据空间")
    return {}


def error_response(error):
    return JSONResponse({"detail": error.detail}, status_code=error.status_code)


def same_origin(request):
    origin = request.headers.get("origin")
    base = str(request.base_url).replace("wss://", "https://", 1).replace("ws://", "http://", 1).rstrip("/")
    return not origin or origin.rstrip("/") in {base, "http://127.0.0.1:51760", "http://localhost:51760"}


@router.post("/{action}")
async def login_action(action: str, request: Request):
    if action not in {"login", "signup", "logout"}:
        return JSONResponse({"detail": "未知认证操作"}, status_code=404)
    if not same_origin(request):
        return JSONResponse({"detail": "请求来源不被允许"}, status_code=403)
    if action == "logout":
        token = access_token(request.headers)
        if token:
            try:
                await supabase("POST", "logout", token=token)
            except Auth.exceptions.HTTPException:
                pass
        result = JSONResponse({"ok": True})
        result.delete_cookie(COOKIE, path="/")
        return result
    try:
        value = await request.json()
    except ValueError:
        return JSONResponse({"detail": "请输入邮箱和密码"}, status_code=400)
    if not isinstance(value, dict):
        return JSONResponse({"detail": "请输入邮箱和密码"}, status_code=400)
    email, password = value.get("email"), value.get("password")
    if not isinstance(email, str) or not isinstance(password, str) or not email or len(password) < 8 or len(email) > 254 or len(password) > 1024:
        return JSONResponse({"detail": "请输入有效邮箱和至少 8 位密码"}, status_code=400)
    try:
        response = await supabase("POST", "token?grant_type=password" if action == "login" else "signup", data={"email": email, "password": password})
    except Auth.exceptions.HTTPException as error:
        return error_response(error)
    if response.status_code not in (200, 201):
        return JSONResponse({"detail": "登录或注册失败，请检查账号、密码及邮箱确认状态"}, status_code=400)
    payload = response.json()
    result = JSONResponse({"ok": True, "confirmation_required": not bool(payload.get("access_token"))})
    if payload.get("access_token"):
        result.set_cookie(COOKIE, payload["access_token"], max_age=int(payload.get("expires_in", 3600)),
                          httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/")
    return result


@router.get("/session")
async def session(request: Request):
    try:
        return await current_user(request.headers)
    except Auth.exceptions.HTTPException as error:
        return error_response(error)
