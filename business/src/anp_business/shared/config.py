"""可复制业务目录的唯一配置入口，不读取仓库父目录。"""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

BUSINESS_ROOT = Path(__file__).resolve().parents[3]
DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 20260},
    "ingress": {"enabled": False, "auth": "http://127.0.0.1:59999/", "rest": "http://127.0.0.1:53300/",
                "storage": "http://127.0.0.1:55000/", "studio": "http://127.0.0.1:53200/"},
    "supabase": {"url": "http://127.0.0.1:58000", "anon_key": ""},
    "platforms": {
        "rustfs": "http://127.0.0.1:59003/rustfs/",
        "rustfs_api": "http://127.0.0.1:59002/",
        "dbx": "http://127.0.0.1:54424/dbx/",
        "langfuse": "http://127.0.0.1:53100/langfuse/",
        "litellm": "http://127.0.0.1:54000/",
        "supabase": "http://127.0.0.1:53200/",
        "grafana": "http://127.0.0.1:53000/grafana/",
    },
    "observability": {"enabled": False, "endpoint": "http://127.0.0.1:4318", "service_name": "anp-business"},
    "model": {"mode": "demo", "name": "gpt-4.1-mini", "base_url": "http://127.0.0.1:54000/v1"},
}


def configuration():
    result = json.loads(json.dumps(DEFAULTS))
    path = Path(os.getenv("ANP_BUSINESS_CONFIG", str(BUSINESS_ROOT / "config.json")))
    if path.exists():
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or set(data) - set(result):
            raise ValueError("业务配置包含未知分组")
        for group, values in data.items():
            if not isinstance(values, dict) or set(values) - set(result[group]):
                raise ValueError("业务配置分组无效：" + group)
            result[group].update(values)
    override = os.getenv("ANP_BUSINESS_OVERRIDES", str(BUSINESS_ROOT / "var/console-platforms.json"))
    if override and Path(override).exists():
        values = json.loads(Path(override).read_text())
        if set(values) - {"platforms", "server"} or not isinstance(values.get("platforms", {}), dict) or set(values.get("platforms", {})) - set(result["platforms"]):
            raise ValueError("控制台代理配置无效")
        result["platforms"].update(values.get("platforms", {}))
        server = values.get("server", {})
        if not isinstance(server, dict) or set(server) - {"host"} or server.get("host", "127.0.0.1") not in ("127.0.0.1", "0.0.0.0"):
            raise ValueError("入口监听配置无效")
        result["server"].update(server)
    result["supabase"]["url"] = os.getenv("ANP_PLATFORM_SUPABASE_API_URL", result["supabase"]["url"])
    result["supabase"]["anon_key"] = os.getenv("ANP_SUPABASE_ANON_KEY", result["supabase"]["anon_key"])
    if os.getenv("ANP_BUSINESS_PORT"):
        result["server"]["port"] = int(os.environ["ANP_BUSINESS_PORT"])
    for name in result["platforms"]:
        result["platforms"][name] = os.getenv("ANP_PLATFORM_" + name.upper() + "_URL", result["platforms"][name])
    if os.getenv("ANP_OTEL_ENABLED"):
        result["observability"]["enabled"] = os.environ["ANP_OTEL_ENABLED"].lower() in ("1", "true", "yes")
    result["observability"]["endpoint"] = os.getenv("ANP_OTEL_ENDPOINT", result["observability"]["endpoint"])
    if not isinstance(result["observability"]["enabled"], bool) or not isinstance(result["observability"]["service_name"], str) or not result["observability"]["service_name"].strip():
        raise ValueError("observability 配置无效")
    if type(result["ingress"]["enabled"]) is not bool:
        raise ValueError("ingress.enabled 必须为布尔值")
    for address in [result["supabase"]["url"], *result["platforms"].values(), *[v for k,v in result["ingress"].items() if k != "enabled"], result["model"]["base_url"], result["observability"]["endpoint"]]:
        parsed = urlsplit(address)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("服务地址必须为不带凭据、查询参数和片段的 HTTP(S) URL")
    if not isinstance(result["server"]["port"], int) or not 1024 <= result["server"]["port"] <= 65535:
        raise ValueError("业务端口无效")
    return result
