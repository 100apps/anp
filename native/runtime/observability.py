"""五个官方原生观测组件；所有配置与可写状态限定到当前实例。"""
import json
import re
from pathlib import Path
from urllib.parse import quote, urlencode

SERVICES = ["loki", "tempo", "prometheus", "otelcol", "grafana"]
PORTS = {"loki": 3100, "loki-grpc": 19095, "tempo": 3200, "tempo-grpc": 19096,
         "tempo-otlp-grpc": 4417, "tempo-otlp-http": 4418, "tempo-memberlist": 17947,
         "prometheus": 9090, "otelcol": 4318, "otelcol-grpc": 4317,
         "otelcol-health": 13133, "otelcol-metrics": 18888, "grafana": 53000}
EXTRA_PORTS = {key: ("otelcol" if key.startswith("otelcol-") else "tempo" if key.startswith("tempo-") else "loki")
               for key in PORTS if key not in SERVICES}
DEFAULTS = {"enabled": False, "retention_hours": 24, "prometheus_retention_size": "512MB", "collector_memory_mib": 256, "loki_wal_disk_full_threshold": 0.98}
HEALTH = {"loki": "/ready", "tempo": "/ready", "prometheus": "/-/ready",
          "otelcol": "/ready", "grafana": "/grafana/api/health"}


def grafana_database(c, config, secret):
    """内置 PG 自动初始化；外部 PG 只在既有数据库中创建本实例 schema。"""
    import external
    resources = external.load(config)
    if resources:
        pg = resources["postgres"]
        database = pg.get("grafana_database")
        role = resources.get("roles", {}).get("grafana")
        if not database or not role:
            raise ValueError("外部 Grafana 需要 postgres.grafana_database 和 roles.grafana，数据库及角色须预先准备")
        schema = resources["namespace"].replace("-", "_") + "_grafana"
        environment = {**c.env(), "PGPASSWORD": pg["password"], "PGOPTIONS": "-c role=" + role,
                       "PGSSLMODE": pg.get("sslmode", "prefer")}
        sql = [c.ROOT / "storage/postgres/bin/psql", "-h", pg["host"], "-p", str(pg.get("port", 5432)),
               "-U", pg["username"], "-d", database, "-v", "ON_ERROR_STOP=1", "-At"]
        owner = c.execute(sql, env=environment, input="SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='" + schema + "';").stdout.strip()
        if owner and owner != role:
            raise RuntimeError("Grafana schema 已属于其他角色，拒绝接管")
        if not owner:
            c.execute(sql, env=environment, input='CREATE SCHEMA "' + schema + '" AUTHORIZATION "' + role + '";')
        query = urlencode({"sslmode": pg.get("sslmode", "prefer"), "search_path": schema, "options": "-c role=" + role})
        return "postgres://" + quote(pg["username"], safe="") + ":" + quote(pg["password"], safe="") + "@" + pg["host"] + ":" + str(pg.get("port", 5432)) + "/" + quote(database, safe="") + "?" + query
    if not c.sql(config, secret, "SELECT 1 FROM pg_roles WHERE rolname='anp_grafana';").strip():
        c.sql(config, secret, "CREATE ROLE anp_grafana LOGIN PASSWORD '" + secret["grafana-db"] + "';")
    if not c.sql(config, secret, "SELECT 1 FROM pg_database WHERE datname='grafana';").strip():
        c.sql(config, secret, "CREATE DATABASE grafana OWNER anp_grafana;")
    return "postgres://anp_grafana:" + quote(secret["grafana-db"], safe="") + "@127.0.0.1:" + str(config["ports"]["postgres"]) + "/grafana?sslmode=disable"


def settings(config):
    value = {**DEFAULTS, **config.get("observability", {})}
    if set(value) - set(DEFAULTS) or not isinstance(value["enabled"], bool):
        raise ValueError("observability 配置无效")
    if not isinstance(value["retention_hours"], int) or not 24 <= value["retention_hours"] <= 8760:
        raise ValueError("观测保留时间必须为 24..8760 小时")
    if not isinstance(value["collector_memory_mib"], int) or not 128 <= value["collector_memory_mib"] <= 4096:
        raise ValueError("Collector 内存限制必须为 128..4096 MiB")
    threshold = value["loki_wal_disk_full_threshold"]
    if type(threshold) not in (int, float) or not 0.5 <= threshold <= 0.99:
        raise ValueError("Loki WAL 磁盘阈值必须介于 0.5 与 0.99 之间")
    if not re.fullmatch(r"[1-9][0-9]*(?:MB|GB)", value["prometheus_retention_size"]):
        raise ValueError("Prometheus 容量必须使用 MB 或 GB")
    return value


def command(c, name, config, secret):
    root, var, ports = c.ROOT, c.VAR, config["ports"]
    options = settings(config)
    program = root / "platform/otel" / name
    data = var / "data/otel" / name
    run = var / "run/otel"
    data.mkdir(parents=True, exist_ok=True)
    run.mkdir(parents=True, exist_ok=True)
    environment = c.env()
    environment.update({"GOMEMLIMIT": "512MiB", "GOMAXPROCS": "2"})
    address = lambda key: "127.0.0.1:" + str(ports[key])
    url = lambda key: "http://" + address(key)
    def document(filename, value):
        path = run / filename
        # JSON 是 YAML 子集，配置生成无需引入宿主 PyYAML。
        c.write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        return path
    retention = str(options["retention_hours"]) + "h"
    if name == "prometheus":
        conf = document("prometheus.yaml", {"global": {"scrape_interval": "15s"}, "scrape_configs": [
            {"job_name": "otelcol", "static_configs": [{"targets": [address("otelcol-metrics")]}]},
            {"job_name": "prometheus", "static_configs": [{"targets": [address("prometheus")]}]}],
            "otlp": {"promote_resource_attributes": ["service.name", "service.namespace", "service.instance.id", "service.version"]}})
        return [program / "prometheus", "--config.file=" + str(conf), "--web.listen-address=" + address(name),
                "--storage.tsdb.path=" + str(data), "--storage.tsdb.retention.time=" + retention,
                "--storage.tsdb.retention.size=" + options["prometheus_retention_size"],
                "--web.enable-otlp-receiver", "--web.enable-remote-write-receiver"], environment
    if name == "loki":
        conf = document("loki.yaml", {"auth_enabled": False,
            "server": {"http_listen_address": "127.0.0.1", "http_listen_port": ports[name],
                       "grpc_listen_address": "127.0.0.1", "grpc_listen_port": ports["loki-grpc"]},
            "common": {"instance_addr": "127.0.0.1", "path_prefix": str(data), "replication_factor": 1,
                       "storage": {"filesystem": {"chunks_directory": str(data / "chunks"), "rules_directory": str(data / "rules")}},
                       "ring": {"kvstore": {"store": "inmemory"}}},
            "schema_config": {"configs": [{"from": "2020-10-24", "store": "tsdb", "object_store": "filesystem", "schema": "v13", "index": {"prefix": "index_", "period": "24h"}}]},
            "ingester": {"lifecycler": {"min_ready_duration": "1s"}, "wal": {"dir": str(data / "wal"), "disk_full_threshold": options["loki_wal_disk_full_threshold"]}},
            "compactor": {"working_directory": str(data / "compactor"), "retention_enabled": True, "delete_request_store": "filesystem"},
            "limits_config": {"retention_period": retention, "allow_structured_metadata": True},
            "analytics": {"reporting_enabled": False}})
        return [program / "loki", "-config.file=" + str(conf)], environment
    if name == "tempo":
        conf = document("tempo.yaml", {
            "server": {"http_listen_address": "127.0.0.1", "http_listen_port": ports[name],
                       "grpc_listen_address": "127.0.0.1", "grpc_listen_port": ports["tempo-grpc"]},
            "distributor": {"receivers": {"otlp": {"protocols": {"grpc": {"endpoint": address("tempo-otlp-grpc")}, "http": {"endpoint": address("tempo-otlp-http")}}}}},
            "memberlist": {"bind_addr": ["127.0.0.1"], "bind_port": ports["tempo-memberlist"]},
            "querier": {"frontend_worker": {"frontend_address": address("tempo-grpc")}},
            "storage": {"trace": {"backend": "local", "wal": {"path": str(data / "wal")}, "local": {"path": str(data / "blocks")}}},
            "live_store": {"wal": {"path": str(data / "live-wal")}, "shutdown_marker_dir": str(data / "shutdown")},
            "backend_scheduler": {"local_work_path": str(data / "scheduler"), "provider": {"compaction": {"compaction": {"block_retention": retention}}}},
            "backend_worker": {"compaction": {"block_retention": retention}},
            "usage_report": {"reporting_enabled": False}})
        return [program / "tempo", "-config.file=" + str(conf)], environment
    if name == "otelcol":
        memory = options["collector_memory_mib"]
        conf = document("collector.yaml", {"receivers": {"otlp": {"protocols": {
            "grpc": {"endpoint": address("otelcol-grpc")}, "http": {"endpoint": address(name)}}}},
            "processors": {"memory_limiter": {"check_interval": "1s", "limit_mib": memory, "spike_limit_mib": max(32, memory // 4)}, "batch": {"timeout": "1s", "send_batch_size": 512}},
            "extensions": {"health_check": {"endpoint": address("otelcol-health"), "path": "/ready"}},
            "exporters": {"otlp_http/traces": {"endpoint": url("tempo-otlp-http")},
                          "otlp_http/metrics": {"endpoint": url("prometheus") + "/api/v1/otlp"},
                          "otlp_http/logs": {"endpoint": url("loki") + "/otlp"}},
            "service": {"extensions": ["health_check"], "telemetry": {"metrics": {"readers": [{"pull": {"exporter": {"prometheus": {"host": "127.0.0.1", "port": ports["otelcol-metrics"]}}}}]}},
                        "pipelines": {signal: {"receivers": ["otlp"], "processors": ["memory_limiter", "batch"], "exporters": ["otlp_http/" + signal]} for signal in ("traces", "metrics", "logs")}}})
        environment["GOMEMLIMIT"] = str(memory) + "MiB"
        return [program / "otelcol-contrib", "--config=" + str(conf)], environment
    if name == "grafana":
        database_url = grafana_database(c, config, secret)
        # 使用 Grafana 官方 file provider，启动日志不经环境变量回显连接 URI。
        private = var / "secrets"
        database_file = private / "grafana-database-url"
        password_file = private / "grafana-admin-password"
        key_file = private / "grafana-secret-key"
        c.write(database_file, database_url)
        c.write(password_file, secret["grafana-password"])
        c.write(key_file, secret["grafana-secret"])
        provisioning = run / "provisioning"
        sources = []
        for key, kind in (("prometheus", "prometheus"), ("loki", "loki"), ("tempo", "tempo")):
            sources.append({"name": key.title(), "type": kind, "uid": "anp-" + key, "access": "proxy", "url": url(key), "editable": False,
                            **({"isDefault": True} if key == "prometheus" else {})})
        sources[1]["jsonData"] = {"derivedFields": [{"name": "trace_id", "matcherType": "label", "matcherRegex": "trace_id", "url": "$${__value.raw}", "datasourceUid": "anp-tempo"}]}
        sources[2]["jsonData"] = {"tracesToLogsV2": {"datasourceUid": "anp-loki", "customQuery": True,
            "query": '{$${__tags}} | trace_id = "$${__trace.traceId}"', "tags": [{"key": "service.name", "value": "service_name"}]}}
        document("provisioning/datasources/anp.yaml", {"apiVersion": 1, "datasources": sources})
        document("provisioning/dashboards/anp.yaml", {"apiVersion": 1, "providers": [{"name": "ANP", "folder": "ANP", "type": "file", "options": {"path": str(root / "defaults/otel/dashboards")}}]})
        conf = run / "grafana.ini"
        c.write(conf, "\n".join(["[server]", "http_addr = 127.0.0.1", "http_port = " + str(ports[name]),
            "root_url = " + url("business") + "/grafana/", "serve_from_sub_path = true", "[paths]",
            "data = " + str(data), "logs = " + str(var / "logs/otel-grafana"),
            "plugins = " + str(var / "cache/otel/grafana/plugins"), "provisioning = " + str(provisioning),
            "[auth.anonymous]", "enabled = false", "[auth.proxy]", "enabled = true", "header_name = X-ANP-User", "header_property = username", "auto_sign_up = true", "whitelist = 127.0.0.1", "enable_login_token = false", "[auth]", "disable_login_form = true", "[users]", "allow_sign_up = false",
            "[analytics]", "reporting_enabled = false", "check_for_updates = false", "check_for_plugin_updates = false",
            "[plugins]", "preinstall_disabled = true", "[database]", "type = postgres",
            "url = $__file{" + str(database_file) + "}", "max_open_conn = 5", "max_idle_conn = 2",
            "[security]", "admin_user = admin", "admin_password = $__file{" + str(password_file) + "}",
            "secret_key = $__file{" + str(key_file) + "}", "[log]", "mode = console", ""]))
        environment["GF_PLUGINS_PREINSTALL_DISABLED"] = "true"
        return [program / "bin/grafana", "server", "--homepath", program, "--config", conf], environment
    raise ValueError("未知观测组件：" + name)
