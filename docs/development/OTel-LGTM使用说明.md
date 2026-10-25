# OTel / LGTM 与 Mac 官方 Redis

本轮源码实现日期：2026-10-25。用户要求先实现、不运行验证，因此没有新构建或验收结果。

## 包含范围

完整平台包新增 Grafana OSS 13.2.4、Collector Contrib 0.163.1、Prometheus 3.15.1、Loki 3.7.9、Tempo 3.1.1，默认关闭。两个目标的官方 URL、SHA256 和许可证位于 native/locks/otel-macos-arm64.json、native/locks/otel-linux-amd64.json。下载由原有摘要校验逻辑完成，不编译这五个上游项目，不抽取 Docker 镜像。

使用步骤见 [随包说明](../../native/defaults/otel/README.md)。`observability.enabled=true` 控制默认启动组和包内 Business 的导出；`--services observability` 用于单独管理观测进程。所有主端口和辅助 TCP 端口均登记到管理器，Tempo memberlist 的 UDP 端口另做占用预检。

## Grafana 的 PostgreSQL

Grafana 不使用 SQLite。内置模式为 grafana 数据库及 anp_grafana 账号；随机数据库密码保存在私有 runtime.json 的 grafana-db。管理器使用底层 SQL 做初始化，业务对象仍由 Supabase 管理。

云模式在现有 0600 外部资源配置中增加：

```json
{
  "postgres": {"grafana_database": "agentloop_grafana"},
  "roles": {"grafana": "agentloop_grafana"}
}
```

以上是需要合并的字段，不是完整资源文件，不要覆盖原有连接参数。目标库与角色须已存在，连接账号须有对应角色成员资格，该角色须能在库内创建 schema。实例使用 `<namespace转换为下划线>_grafana`，已有同名 schema 属于其他角色时拒绝接管。原 sslmode 保留，不自动建云数据库或云角色。

Grafana 登录密码字段为 grafana-password，和数据库密码分开。现有 Business 代理已支持 HTTP/WebSocket；新增 /grafana/ 同样经过管理员鉴权、来源检查和凭据剥离，并保留 Grafana 独立登录。数据源与一个 ANP 概览仪表盘自动 provision，不在线下载插件。

## Business 遥测

业务配置增加：

```json
{
  "observability": {
    "enabled": true,
    "endpoint": "http://127.0.0.1:4318",
    "service_name": "anp-business"
  },
  "platforms": {"grafana": "http://127.0.0.1:53000/grafana/"}
}
```

独立复制 Business 时自行合并配置，可指向 WSL 或外部 Collector/Grafana。也支持 ANP_OTEL_ENABLED、ANP_OTEL_ENDPOINT、ANP_PLATFORM_GRAFANA_URL。默认禁用，不会主动连接未配置的遥测服务。

SDK 通过 uv 显式冻结依赖。复用现有 LangGraph Agent Server 的自定义中间件，不另起 HTTP 服务器；增加请求 span、HTTP 请求计数与耗时，以及 Workflow 节点、Deep Agent read_material 工具 span。项目自身的上游 HTTP 调用传播 traceparent。只导出本模块固定格式的日志，不自动导出上游全量日志，不记录请求正文、认证头、URL 查询参数或异常文本。Langfuse 仍保持原有模型观测逻辑，本轮没有将全部通用 span 转发给 Langfuse。

## Mac Redis 官方包

移除 Mac 原生构建的 Redis 源码编译步骤，改用官方 redis-oss-8.10.2-arm64.zip。只装 redis-server、redis-cli 和核心 Vector Set 功能，不加载附带的 Search/JSON 等模块。复用官方 PostgreSQL 包中的 OpenSSL 库并修正相对路径，修改后的 Mach-O 在最终打包阶段重签；有效的上游签名保留，自编译 Mac DBX 单独处理。

版本来源、摘要及许可证进入清单。make native-sources 不再为 Mac 初始化 Redis 源码；Docker 原有 Redis 源码方案保持不变。之前的独立 Redis PoC 与本轮正式构建接线分开记录，不能当作本轮整包通过。

## 尚未执行

本轮没有执行 make build、make dist、make test、native-check，没有启动新增服务，也没有运行数据读回、数据库迁移或浏览器验收。当前运行中的本机/远程旧实例未升级。后续需完成三类信号读回、Grafana PostgreSQL 初始化、权限/WebSocket、重启搬移和两个架构的解压验收。

Tempo 3.x 使用 `live_store` 和 `backend_scheduler` / `backend_worker` 配置；WAL、关闭标记与调度缓存均写入本实例 `var/`。Grafana 使用 PostgreSQL 与代理认证，经过 Agent Server 进入时不再输入第二次密码。

Loki WAL 默认磁盘水位在本原生开发包设为 `0.98`，可通过 `observability.loki_wal_disk_full_threshold` 调整（范围 0.5..0.99，不允许关闭保护）。上游默认 90% 会在本机仍有数十 GiB 空间时拒绝写入，并返回误导性的 “Ingester is shutting down”。该调整保留磁盘保护，配合 24 小时保留策略使用；完整磁盘验收还需要执行真实日志写入读取，不能只看 `/ready`。
