"""真实时间点在上海默认时区与平台内部编码之间保持一致。"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def module(monkeypatch, name):
    monkeypatch.syspath_prepend(str(ROOT / "native/runtime"))
    spec = importlib.util.spec_from_file_location("anp_tz_" + name, ROOT / "native/runtime" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_runtime_defaults_ignore_host_timezone_without_importing_secrets(monkeypatch, tmp_path):
    c = module(monkeypatch, "control")
    monkeypatch.setattr(c, "VAR", tmp_path)
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    environment = c.env()
    assert environment["TZ"] == environment["PGTZ"] == "Asia/Shanghai"
    assert environment["PGOPTIONS"] == "-c timezone=Asia/Shanghai"
    assert "PGPASSWORD" not in environment
    monkeypatch.setattr(c, "prepare_postgres", lambda value: None)
    command, _ = c.command("postgres", {"ports": {"postgres": 5432}}, {})
    assert "timezone=Asia/Shanghai" in command
    assert "log_timezone=Asia/Shanghai" in command


def test_langfuse_schema_explicit_utc_preserves_precision_and_other_timezones(monkeypatch, tmp_path):
    tz = module(monkeypatch, "timezones")
    sql = "a DateTime64(6), b Nullable(DateTime64(3)), c DateTime, d DateTime64(6, 'Asia/Shanghai'), e DateTime('UTC'), f DEFAULT toDateTime64(0, 3)"
    expected = "a DateTime64(6, 'UTC'), b Nullable(DateTime64(3, 'UTC')), c DateTime('UTC'), d DateTime64(6, 'Asia/Shanghai'), e DateTime('UTC'), f DEFAULT toDateTime64(0, 3)"
    assert tz.explicit_langfuse_utc(sql) == expected
    assert tz.explicit_langfuse_utc(expected) == expected
    folder = tmp_path / "web/packages/shared/clickhouse/migrations"
    folder.mkdir(parents=True)
    migration = folder / "0001.up.sql"
    migration.write_text(sql)
    assert tz.prepare_langfuse_migrations(tmp_path) == [str(migration.relative_to(tmp_path))]
    assert migration.read_text() == expected
    assert tz.prepare_langfuse_migrations(tmp_path) == []


def test_grafana_uses_shanghai_and_private_postgres_url(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlsplit
    ob = module(monkeypatch, "observability")
    c = SimpleNamespace(ROOT=tmp_path / "bundle", VAR=tmp_path / "instance", env=lambda: {})
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    c.write = write
    monkeypatch.setattr(ob, "grafana_database", lambda *args: "postgres://private")
    config = {"ports": {**ob.PORTS, "business": 58000}}
    command, environment = ob.command(c, "grafana", config, {"grafana-password": "test", "grafana-secret": "test"})
    ini = (c.VAR / "run/otel/grafana.ini").read_text()
    assert "default_timezone = Asia/Shanghai" in ini
    assert "postgres://private" not in ini
    assert (c.VAR / "secrets/grafana-database-url").read_text() == "postgres://private"
    external = module(monkeypatch, "external")
    resources = {"namespace": "test", "postgres": {"host": "localhost", "username": "test", "password": "test"}, "roles": {"langfuse": "test_role"}}
    query = parse_qs(urlsplit(external.pgurl(resources, "langfuse", "test_role")).query)
    assert query["options"] == ["-c timezone=Asia/Shanghai -c role=test_role"]
