"""验收同源平台入口、静态资源与状态；不创建账号或写平台业务数据。"""
import argparse
import asyncio
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.urls.append(values["src"])
        elif tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
            self.urls.append(values["href"])


async def check(base_url, require_ready=False):
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=True, max_redirects=10) as client:
        response = await client.get(base_url + "/api/platforms")
        response.raise_for_status()
        result = []
        for platform in response.json():
            prefix = platform["path"].rstrip("/")
            page = await client.get(base_url + platform["path"], headers={"Accept": "text/html"})
            record = {"platform": platform["id"], "path": platform["path"], "status": page.status_code,
                      "redirects": len(page.history), "assets": []}
            if platform["status"] == "unavailable":
                assert page.status_code == 503, "不可用平台必须有明确的503说明"
                record["result"] = "unavailable"
                if require_ready:
                    raise AssertionError(platform["id"] + " 实际平台尚未就绪")
            else:
                assert page.status_code == 200, f"{platform['id']} 页面返回 {page.status_code}"
                assert "text/html" in page.headers.get("content-type", ""), "平台主页应为真实HTML"
                parser = Resources()
                parser.feed(page.text)
                local_assets = [urljoin(str(page.url), value) for value in parser.urls
                                if urlsplit(urljoin(str(page.url), value)).netloc == urlsplit(base_url).netloc]
                assert local_assets, "平台主页缺少实际前端资源"
                for asset in dict.fromkeys(local_assets):
                    assert urlsplit(asset).path.startswith(prefix + "/"), "资源逃逸平台目录：" + urlsplit(asset).path
                    resource = await client.get(asset)
                    assert resource.status_code == 200 and len(resource.content) > 0, "静态资源不可达"
                    assert "text/html" not in resource.headers.get("content-type", ""), "JS/CSS请求不能被主页兜底"
                    record["assets"].append({"path": urlsplit(asset).path, "status": resource.status_code})
                record["result"] = "passed"
                probes = {
                    "langfuse": ["/api/public/health"],
                    "litellm": ["/health/liveliness", "/health/readiness"],
                    "supabase": ["/api/platform/projects/default", "/auth/v1/health", "/rest/v1/", "/storage/v1/status"],
                }
                record["api_checks"] = []
                for endpoint in probes.get(platform["id"], []):
                    api = await client.get(base_url + prefix + endpoint)
                    assert api.status_code == 200, f"{platform['id']} API {endpoint} 返回 {api.status_code}"
                    if endpoint != "/storage/v1/status":
                        assert "json" in api.headers.get("content-type", ""), "API请求不能返回主页"
                        payload = api.json()
                        if endpoint == "/api/platform/projects/default":
                            assert payload.get("status") == "ACTIVE_HEALTHY", "Studio默认项目未就绪"
                        if endpoint == "/health/readiness":
                            assert payload.get("status") == "healthy", "LiteLLM尚未就绪"
                    record["api_checks"].append({"path": prefix + endpoint, "status": api.status_code})
            result.append(record)
            print(platform["id"], record["result"], "资源", len(record["assets"]), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:20240")
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--output", default="var/evidence/platform-access.json")
    options = parser.parse_args()
    checks = asyncio.run(check(options.url.rstrip("/"), options.require_ready))
    output = ROOT / options.output
    if not output.resolve().is_relative_to(ROOT):
        raise ValueError("验收输出须在本工作区")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"base_url": options.url, "checks": checks}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
