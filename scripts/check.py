"""四存储真实读写、可选重启恢复、HTTP 图执行及旧环境保护检查。"""
import argparse
import asyncio
import json
import os
import time
import urllib.request
import uuid

import boto3
import psycopg
import redis
from botocore.config import Config
from langgraph_sdk import get_client
from manage import PROJECT, ROOT, compose, read_env

ENV = read_env(ROOT / ".env")
TOKEN = uuid.uuid4().hex


def postgres():
    if url := os.getenv("ANP_CHECK_PG_URL") or ENV.get("ANP_CHECK_PG_URL"):
        return psycopg.connect(url, connect_timeout=5)
    return psycopg.connect(host="127.0.0.1", port=int(ENV["PG_PORT"]), user="postgres",
                            password=ENV["POSTGRES_PASSWORD"], dbname="postgres", connect_timeout=5)


def ch(query):
    request = urllib.request.Request("http://127.0.0.1:" + ENV["CH_PORT"] + "/", data=query.encode())
    request.add_header("X-ClickHouse-User", "anp")
    request.add_header("X-ClickHouse-Key", ENV["CLICKHOUSE_PASSWORD"])
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode().strip()


def resources():
    cache_url = os.getenv("ANP_CHECK_REDIS_URL") or ENV.get("ANP_CHECK_REDIS_URL")
    cache = (redis.Redis.from_url(cache_url, decode_responses=True, socket_timeout=5) if cache_url else
             redis.Redis(host="127.0.0.1", port=int(ENV["REDIS_PORT"]), password=ENV["REDIS_PASSWORD"],
                         decode_responses=True, socket_timeout=5))
    endpoint = os.getenv("ANP_CHECK_S3_ENDPOINT") or ENV.get("ANP_CHECK_S3_ENDPOINT")
    s3 = boto3.client("s3", endpoint_url=endpoint or "http://127.0.0.1:" + ENV["S3_PORT"],
                      aws_access_key_id=ENV["S3_ACCESS_KEY"], aws_secret_access_key=ENV["S3_SECRET_KEY"],
                      region_name="us-east-1", config=Config(s3={"addressing_style": "path"}))
    return cache, s3


def write_storage():
    cache, s3 = resources()
    with postgres() as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS anp2_smoke")
        conn.execute("CREATE TABLE IF NOT EXISTS anp2_smoke.probe (id integer PRIMARY KEY, token text NOT NULL)")
        conn.execute("INSERT INTO anp2_smoke.probe VALUES (1,%s) ON CONFLICT (id) DO UPDATE SET token=EXCLUDED.token", (TOKEN,))
        version = conn.execute("SHOW server_version").fetchone()[0]
    cache.set("anp2-next:smoke", TOKEN)
    ch("CREATE DATABASE IF NOT EXISTS anp2_smoke")
    ch("CREATE TABLE IF NOT EXISTS anp2_smoke.probe (token String) ENGINE=MergeTree ORDER BY token")
    ch("INSERT INTO anp2_smoke.probe VALUES ('" + TOKEN + "')")
    if "anp2-smoke" not in {b["Name"] for b in s3.list_buckets()["Buckets"]}:
        s3.create_bucket(Bucket="anp2-smoke")
    s3.put_object(Bucket="anp2-smoke", Key="probe.txt", Body=TOKEN.encode())
    return {"postgres": version, "redis": cache.info("server")["redis_version"],
            "clickhouse": ch("SELECT version()"), "rustfs_image": ENV["RUSTFS_IMAGE"]}


def read_storage():
    cache, s3 = resources()
    with postgres() as conn:
        assert conn.execute("SELECT token FROM anp2_smoke.probe WHERE id=1").fetchone()[0] == TOKEN
    assert cache.get("anp2-next:smoke") == TOKEN
    assert ch("SELECT count() FROM anp2_smoke.probe WHERE token='" + TOKEN + "'") == "1"
    assert s3.get_object(Bucket="anp2-smoke", Key="probe.txt")["Body"].read().decode() == TOKEN
    url = s3.generate_presigned_url("get_object", Params={"Bucket": "anp2-smoke", "Key": "probe.txt"}, ExpiresIn=60)
    with urllib.request.urlopen(url, timeout=10) as response:
        assert response.read().decode() == TOKEN


async def demos(api_url=None):
    client = get_client(url=api_url or "http://127.0.0.1:" + ENV["BUSINESS_PORT"])
    result = {}
    for graph, value in {
        "workflow_demo": {"text": "  分层架构  支持独立更新与外部存储。 "},
        "deepagent_demo": {"messages": [{"role": "user", "content": "阅读资料并生成架构报告"}]}
    }.items():
        thread = await client.threads.create()
        events = []
        final = None
        async for item in client.runs.stream(thread["thread_id"], graph, input=value, stream_mode=["updates", "values"]):
            if item.event == "error":
                raise RuntimeError(str(item.data))
            events.append(item.event)
            if item.event == "values":
                final = item.data
        assert final and "updates" in events
        state = await client.threads.get_state(thread["thread_id"])
        if graph == "workflow_demo":
            assert "分层" in final["summary"]
            assert state["values"]["summary"] == final["summary"]
        else:
            assert any(m.get("type") == "tool" and m.get("name") == "read_material" for m in final["messages"])
            assert "报告" in final["messages"][-1]["content"]
        result[graph] = {"thread_id": thread["thread_id"], "events": len(events), "passed": True}
    return result


def protected():
    import subprocess
    path = ROOT / "var/evidence/protected-containers-before.json"
    if not path.exists():
        return {"status": "没有初始基线"}
    baseline = json.loads(path.read_text(encoding="utf-8-sig"))
    changed = []
    for item in baseline:
        completed = subprocess.run(["docker", "inspect", "--format", "{{.State.StartedAt}}", item["Id"]],
                                   capture_output=True, text=True, check=False)
        if completed.returncode or completed.stdout.strip() != item["StartedAt"]:
            changed.append(item["Name"])
    assert not changed, "旧容器基线发生变化：" + str(changed)
    return {"passed": True, "unchanged_containers": len(baseline)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--storage-only", action="store_true")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--api-url")
    options = parser.parse_args()
    if options.health:
        cache, s3 = resources()
        with postgres() as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        assert cache.ping() and ch("SELECT 1") == "1"
        s3.list_buckets()
        print(json.dumps({"storage_health": True, "protected": protected()}, ensure_ascii=False))
        return
    result = {"project": PROJECT, "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "storage_versions": write_storage()}
    read_storage()
    result["storage_read_write"] = True
    if options.restart:
        compose("restart", "postgres", "redis", "clickhouse", "rustfs")
        compose("up", "-d", "--wait", "postgres", "redis", "clickhouse", "rustfs")
        read_storage()
        result["storage_after_restart"] = True
    if not options.storage_only:
        result["demos"] = asyncio.run(demos(options.api_url))
    result["protected"] = protected()
    out = ROOT / ("var/evidence/docker-smoke.json" if options.api_url else "var/evidence/local-smoke.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
