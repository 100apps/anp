"""DBX 通过业务代理登录、加载静态资源并执行真实数据库查询。"""
import json
from http.cookiejar import CookieJar
from urllib.request import Request, build_opener, ProxyHandler, HTTPCookieProcessor
import re
import uuid


def check(origin, postgres_port, private, postgres=None, admin_email="admin@anp.local"):
    client = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    def request(path, value=None):
        req = Request(origin + path, data=json.dumps(value).encode() if value is not None else None,
                      headers={"Content-Type": "application/json"})
        with client.open(req, timeout=30) as response:
            assert response.status == 200, "DBX 请求失败"
            return response.read()
    request("/api/auth/login", {"email": admin_email, "password": private["business-user-password"]})
    page = request("/dbx/").decode()
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', page)
    assert scripts, "DBX 页面缺少前端脚本"
    for script in scripts:
        path = script if script.startswith("/") else "/dbx/" + script.lstrip("./")
        assert len(request(path)) > 100
    request("/dbx/api/auth/login", {"password": private["dbx-password"]})
    connection = {"id": str(uuid.uuid4()), "name": "ANP distribution acceptance", "db_type": "postgres",
                  "host": "127.0.0.1", "port": postgres_port, "username": "anp", "password": private["postgres"], "database": "postgres"}
    if postgres:
        connection.update(postgres)
    request("/dbx/api/connection/connect", {"config": connection})
    try:
        result = json.loads(request("/dbx/api/query/execute", {"connectionId": connection["id"], "database": connection["database"], "sql": "SELECT 42 AS answer"}))
        assert result["columns"] == ["answer"] and result["rows"] == [[42]], "DBX 查询结果错误"
    finally:
        request("/dbx/api/connection/disconnect", {"connectionId": connection["id"]})
    return {"business_admin_login": True, "dbx_password_login_through_proxy": True,
            "embedded_frontend_assets": True, "postgres_real_query_result_42": True}
