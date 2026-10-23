"""仅初始化本实例的 Supabase 管理员；不把用户元数据当作权限。"""
import json
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError


def ensure_admin(origin, secret, var):
    marker = var / "data/business-admin.json"
    if marker.exists():
        return
    http = build_opener(ProxyHandler({}))
    def request(path, data=None):
        req = Request(origin + "/admin/" + path,
                      data=json.dumps(data).encode() if data is not None else None,
                      headers={"Authorization": "Bearer " + secret["service-key"], "Content-Type": "application/json"})
        try:
            with http.open(req, timeout=15) as result:
                return json.load(result)
        except HTTPError:
            raise RuntimeError("Business 管理员初始化失败；请检查本实例 Auth 日志") from None
    # 逐页检查，绝不提升已有同邮箱账号。
    page = 1
    while True:
        users = request(f"users?page={page}&per_page=100").get("users", [])
        if any(user.get("email") == "admin@anp.local" for user in users):
            raise RuntimeError("Supabase 已存在 admin@anp.local；拒绝覆盖或自动提升权限，请手动配置 Business 管理员")
        if len(users) < 100:
            break
        page += 1
    user = request("users", {"email": "admin@anp.local", "password": secret["business-user-password"],
                              "email_confirm": True, "app_metadata": {"anp_role": "admin", "anp_bootstrap": True}})
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"id": user["id"], "email": "admin@anp.local"}) + "\n")
    marker.chmod(0o600)
