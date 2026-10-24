"""外部依赖模式必须经真实启动入口选服，且不初始化本地存储。"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def runtime():
    name = "native_external_test_control"
    spec = importlib.util.spec_from_file_location(name, ROOT / "native/runtime/control.py")
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    value.PLATFORM = True
    value.ORDER = value.platforms.ORDER
    return value


def test_external_gateway_never_pulls_local_storage():
    c = runtime()
    config = {"services": ["gateway"], "external_resources_file": "/private/resources.json"}
    selected = c.selected(config)
    assert not {"postgres", "redis", "clickhouse", "rustfs"} & set(selected)
    assert {"auth", "postgrest", "storage-api", "dbx", "business", "gateway"} <= set(selected)


def test_local_gateway_retains_all_storage_dependencies():
    c = runtime()
    assert {"postgres", "redis", "clickhouse", "rustfs"} <= set(c.selected({"services": ["gateway"]}))


def test_external_mode_rejects_explicit_local_storage():
    import pytest
    c = runtime()
    with pytest.raises(RuntimeError, match="外部"):
        c.selected({"services": ["postgres"], "external_resources_file": "/private/resources.json"})


def profile():
    return {"namespace": "anp-test-036", "postgres": {"host": "db.test", "username": "tester", "password": "fixture/pw@",
            "supabase_database": "supabase", "langfuse_database": "langfuse", "litellm_database": "litellm"},
            "redis": {"host": "redis.test", "password": "redis-fixture", "cluster": True},
            "clickhouse": {"endpoint": "https://clickhouse.test", "migration_url": "clickhouse://clickhouse.test:9000", "username": "tester", "password": "ch-fixture", "database": "anp_test_036"},
            "s3": {"endpoint": "https://s3.test", "bucket": "test", "region": "test", "access_key": "fixture-ak", "secret_key": "fixture-sk"},
            "roles": {"anon": "custom_anon", "authenticated": "custom_user", "service": "custom_service", "auth": "custom_auth", "storage": "custom_storage", "langfuse": "custom_lf", "studio_read_only": "custom_anon"}}


def test_external_config_requires_private_file(tmp_path):
    import pytest
    ext = runtime().external
    p = tmp_path / "resources.json"
    p.write_text(json.dumps(profile()))
    p.chmod(0o644)
    with pytest.raises(RuntimeError, match="0600"):
        ext.load({"external_resources_file": str(p)})
    p.chmod(0o600)
    assert ext.load({"external_resources_file": str(p)})["namespace"] == "anp-test-036"


def test_external_roles_redis_cluster_and_schema_isolation():
    from urllib.parse import urlparse, parse_qs
    ext = runtime().external
    data = profile()
    auth = ext.environment("auth", data, "http://127.0.0.1:58000")
    assert parse_qs(urlparse(auth["GOTRUE_DB_DATABASE_URL"]).query)["options"] == ["-c role=custom_auth -c search_path=auth,public,extensions"]
    lf = ext.environment("langfuse-worker", data, "http://127.0.0.1:58000")
    assert lf["REDIS_CLUSTER_ENABLED"] == "true" and lf["REDIS_KEY_PREFIX"] == "{anp-test-036}:"
    assert parse_qs(urlparse(lf["DATABASE_URL"]).query)["schema"] == ["anp_test_036"]
    assert lf["LANGFUSE_S3_EVENT_UPLOAD_PREFIX"].startswith("anp-test-036/")
    assert not ext.environment("dbx", data, "http://127.0.0.1:58000")
    text = auth["GOTRUE_DB_DATABASE_URL"] + data["postgres"]["password"]
    assert "fixture/pw@" not in ext.redact(text, data) and "fixture%2Fpw%40" not in ext.redact(text, data)
