"""真实 Supabase + 内置 Agent Server 验收；演示模型不冒充供应商调用。"""
import argparse
import json
from pathlib import Path
import secrets
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler, HTTPCookieProcessor
from http.cookiejar import CookieJar
import uuid


def check(origin, supabase, runtime_secret, receipt):
    private = json.loads(Path(runtime_secret).read_text())
    clients = []
    users = []
    threads = []
    checks = {}
    service = build_opener(ProxyHandler({}))
    def call(client, base, method, path, value=None, headers=None):
        request = Request(base.rstrip("/") + path, data=json.dumps(value).encode() if value is not None else None,
                          method=method, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with client.open(request, timeout=90) as response:
                body = response.read()
                return response.status, json.loads(body) if body else None
        except HTTPError as error:
            return error.code, None
    def admin(method, path, value=None):
        return call(service, supabase, method, "/auth/v1/admin/" + path, value,
                    {"Authorization": "Bearer " + private["service-key"]})
    def passed(name, condition):
        if not condition:
            raise RuntimeError("Business 验收失败：" + name)
        checks[name] = True
    try:
        passed("anonymous_threads_denied", call(service, origin, "POST", "/threads/search", {})[0] == 401)
        for number in range(3):
            email = f"anp-check-{uuid.uuid4().hex}@example.test"
            password = secrets.token_urlsafe(32)
            status, user = admin("POST", "users", {"email": email, "password": password, "email_confirm": True,
                "user_metadata": {"anp_role": "admin"}, "app_metadata": {"anp_role": "admin" if number == 2 else "user"}})
            passed("create_supabase_user_" + str(number), status in (200, 201) and bool(user.get("id")))
            users.append(user["id"])
            client = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
            clients.append(client)
            passed("supabase_login_" + str(number), call(client, origin, "POST", "/api/auth/login", {"email": email, "password": password})[0] == 200)
            status, identity = call(client, origin, "GET", "/api/auth/session")
            passed("trusted_role_" + str(number), status == 200 and identity["role"] == ("admin" if number == 2 else "user"))
        first, second, administrator = clients
        passed("platform_admin_only", call(first, origin, "GET", "/api/platforms")[0] == 403 and call(administrator, origin, "GET", "/api/platforms")[0] == 200)
        passed("cross_origin_write_denied", call(administrator, origin, "POST", "/dbx/write", {}, {"Origin": "https://foreign.invalid"})[0] == 403)
        status, thread = call(first, origin, "POST", "/threads", {"metadata": {"owner": users[1]}})
        passed("thread_owner_cannot_be_forged", status == 200 and thread["metadata"]["owner"] == users[0])
        thread_id = thread["thread_id"]
        threads.append((first, thread_id))
        for suffix in ("", "/state", "/runs"):
            passed("cross_user_read_denied" + suffix.replace("/", "_"), call(second, origin, "GET", f"/threads/{thread_id}" + suffix)[0] in (403, 404))
        passed("cross_user_run_denied", call(second, origin, "POST", f"/threads/{thread_id}/runs/wait", {"assistant_id": "workflow_demo", "input": {"text": "无权限"}})[0] in (403, 404))
        status, found = call(second, origin, "POST", "/threads/search", {})
        passed("thread_search_isolation", status == 200 and all(item["thread_id"] != thread_id for item in found))
        status, result = call(first, origin, "POST", f"/threads/{thread_id}/runs/wait", {"assistant_id": "workflow_demo", "input": {"text": "  组件  独立部署\n可复制。 "}})
        passed("workflow_real_server_offline_model", status == 200 and result.get("normalized") == "组件 独立部署 可复制。" and bool(result.get("summary")))
        status, deep_thread = call(first, origin, "POST", "/threads", {})
        passed("deepagent_thread", status == 200)
        threads.append((first, deep_thread["thread_id"]))
        status, result = call(first, origin, "POST", f"/threads/{deep_thread['thread_id']}/runs/wait", {"assistant_id": "deepagent_demo", "input": {"messages": [{"role": "user", "content": "请调研架构"}]}})
        passed("deepagent_real_tool_execution_offline_model", status == 200 and any(message.get("type") == "tool" and message.get("name") == "read_material" for message in result.get("messages", [])))
        for client, thread in threads:
            passed("cleanup_thread_" + thread, call(client, origin, "DELETE", "/threads/" + thread)[0] in (200, 204))
        threads.clear()
        passed("logout_revokes_browser_session", call(first, origin, "POST", "/api/auth/logout", {})[0] == 200 and call(first, origin, "GET", "/api/auth/session")[0] == 401)
    finally:
        # 验收只删除本轮创建的线程和用户。
        for client, thread in threads:
            call(client, origin, "DELETE", "/threads/" + thread)
        for user in users:
            admin("DELETE", "users/" + user)
    value = {"passed": True, "checks": checks, "model": "offline-demo", "external_supplier_call_verified": False}
    Path(receipt).parent.mkdir(parents=True, exist_ok=True)
    Path(receipt).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": True, "checks": len(checks), "receipt": str(receipt)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", required=True)
    parser.add_argument("--supabase", required=True)
    parser.add_argument("--runtime-secret", required=True)
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args()
    check(args.origin, args.supabase, args.runtime_secret, args.receipt)
