# ANP 原生观测模块

包含官方预构建 Grafana、OTel Collector Contrib、Prometheus、Loki、Tempo；不包含 Pyroscope、eBPF。默认随完整平台包安装，但不启动。

在 config/anp.json 中保留现有配置，增加：

```json
{
  "observability": {
    "enabled": true,
    "retention_hours": 24,
    "prometheus_retention_size": "512MB",
    "collector_memory_mib": 256
  }
}
```

停止并重新启动后，默认服务会增加这五个组件，Business 同时启用 OTLP HTTP 导出。已有配置缺少新增端口时，管理器会补齐观测默认端口；存在重复端口仍拒绝启动。

```sh
./anp start
./anp status --services observability
./anp stop --services observability
```

也可以只执行 `./anp start --services observability`，启动模块供其他已接入 OTel 的应用使用；这条命令本身不修改 Business 的配置。停止观测组不会连带停止共享 PostgreSQL。

Grafana：统一入口 `http://127.0.0.1:58000/grafana/`，需要 Business 管理员权限，随后使用 Grafana 自己的 admin 账号登录，密码保存在当前实例私有 var/secrets/runtime.json 的 grafana-password。不是 admin/admin，也不使用匿名访问或未经配置的 SSO。

OTLP HTTP：`http://127.0.0.1:4318`，对应 /v1/traces、/v1/metrics、/v1/logs；gRPC 使用 4317。监听均为 loopback。

Grafana **仅使用 PostgreSQL**。内置存储模式自动创建独立 grafana 数据库和 anp_grafana 账号；云资源模式必须在外部资源文件中提供 postgres.grafana_database 和 roles.grafana，目标数据库与角色由操作员提前准备，启动器仅初始化当前 namespace 对应的独立 schema。不会回退 SQLite。

Prometheus、Loki、Tempo 自己的数据仍位于 var/data/otel。启用业务云资源模式不意味着观测数据也自动存到云端。

配置生成目录为 var/run/otel，数据目录为 var/data/otel，日志沿用 var/logs。搬移前先停止服务并保留 var 和 config；重新启动时按新路径生成配置。

此功能已接入源码，当前这轮按用户要求未进行构建、启动或功能验收。
