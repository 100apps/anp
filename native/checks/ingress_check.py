"""原生单入口鉴权与平台路由验收；不输出凭据，仅清理自己的临时用户。"""
import argparse
from http.cookiejar import CookieJar
import json
from pathlib import Path
import secrets
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler, HTTPCookieProcessor
import uuid


def check(root, receipt):
    root, receipt = Path(root), Path(receipt)
    config = json.loads((root / "config/anp.json").read_text())
    origin = "http://127.0.0.1:" + str(config["ports"]["business"])
    private = json.loads((root / "var/secrets/runtime.json").read_text())
    machine = build_opener(ProxyHandler({}))
    admin = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    member = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
    checks = {}

    def call(client, path, data=None, headers=None, method=None):
        body = json.dumps(data).encode() if data is not None else None
        try:
            with client.open(Request(origin + path, data=body, method=method,
                    headers={"Content-Type": "application/json", **(headers or {})}), timeout=60) as response:
                body = response.read()
                try:
                    body = json.loads(body)
                except ValueError:
                    pass
                return response.status, body
        except HTTPError as error:
            return error.code, None

    def passed(name, value):
        assert value, name
        checks[name] = True
        print(name + " PASS", flush=True)

    passed("admin_login", call(admin, "/api/auth/login", {"email": "admin@anp.local", "password": private["business-user-password"]})[0] == 200)
    for path in ("/project/default", "/_next/probe.js", "/api/platform/profile", "/dbx/", "/grafana/", "/rustfs/console/", "/litellm/ui/", "/langfuse/"):
        passed("anonymous_denied:" + path, call(machine, path)[0] == 401)
    status, state = call(admin, "/api/console/state")
    passed("single_agent_server_no_gateway", status == 200 and all(s["id"] != "gateway" for s in state["services"]) and next(s for s in state["services"] if s["id"] == "business")["port"] == config["ports"]["business"])
    failed = [{"id": s["id"], "state": s["state"]} for s in state["services"] if s["state"] != "ready"]
    assert not failed, "services not ready: " + json.dumps(failed)
    passed("all_managed_services_ready", True)
    status, body = call(admin, "/api/platform/pg-meta/default/query", {"query": "SELECT 42 AS value"})
    passed("studio_real_query", status == 200 and body == [{"value": 42}])
    status, body = call(admin, "/project/default")
    passed("studio_page_same_origin", status == 200 and b"/_next/" in body)
    passed("litellm_bad_key_denied", call(machine, "/litellm/v1/models", headers={"Authorization": "Bearer invalid-acceptance-key"})[0] in (401, 403))
    passed("litellm_good_key", call(machine, "/litellm/v1/models", headers={"Authorization": "Bearer " + private["litellm"]})[0] == 200)
    passed("litellm_ui_login_via_proxy", call(admin, "/litellm/v2/login", {"username": "admin", "password": private["litellm"]})[0] == 200)
    passed("grafana_admin_proxy", call(admin, "/grafana/api/user")[1].get("login") == "admin")
    passed("cross_origin_studio_write_denied", call(admin, "/api/platform/pg-meta/default/query", {"query": "SELECT 42"}, {"Origin": "https://foreign.invalid"})[0] == 403)
    service_headers = {"Authorization": "Bearer " + private["service-key"]}
    email = "anp-ingress-" + uuid.uuid4().hex + "@example.test"
    password = secrets.token_urlsafe(32)
    status, user = call(machine, "/auth/v1/admin/users", {"email": email, "password": password, "email_confirm": True}, service_headers)
    assert status in (200, 201)
    try:
        assert call(member, "/api/auth/login", {"email": email, "password": password})[0] == 200
        passed("member_cannot_access_studio_or_credentials", all(call(member, path)[0] == 403 for path in ("/project/default", "/api/platform/profile", "/api/console/state")))
    finally:
        assert call(machine, "/auth/v1/admin/users/" + user["id"], headers=service_headers, method="DELETE")[0] in (200, 204)
    passed("own_member_cleaned", True)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"passed": True, "origin": origin, "checks": checks}, ensure_ascii=False, indent=2) + "\n")
    return checks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    check(args.root, args.receipt)
