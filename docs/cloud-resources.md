# 连接配置与云资源

## 三类配置

- `settings.local.toml`：应用使用的 URL、端口、驱动参数，参考 `settings.example.toml`。
- `secrets.local.env`：密码与 Token；不提交，值持久化，首次 init 后不会随意重建。
- `deployment`：第三方容器环境变量覆盖与外置服务清单，参考 `cloud-bindings.example.toml`。修改后 `uv run anp app sync`、`uv run anp platform up`。

应用连接可由 `ANP_<SECTION>_<KEY>` 环境变量覆盖，例如 `ANP_SUPABASE_URL`、`ANP_LITELLM_URL`。容器内 URL 与 Windows 本机 URL 分开，容器不能使用本机 localhost 访问其它容器。

## 外置数据库和对象存储

`[deployment.environment.<service>]` 原样覆盖组件所需连接变量，`${NAME}` 从私有 env 展开，生成文件不提交。`external_services` 从 Compose 移除所列本地服务，并移除其 depends_on。现成组件仍使用官方支持的 DSN/配置，不修改第三方代码。

例如 S3 外置：同时配置 Supabase Storage、Langfuse Web/Worker、storage-init 的 endpoint、region、bucket 和访问凭据；`[storage]` 设置宿主可访问的 endpoint 与容器内部 endpoint。Langfuse 媒体下载还要配置浏览器可访问的公共 endpoint。云厂商 CORS、TLS 和 bucket 策略按其要求配置。

PostgreSQL 外置需要同时配置 Supabase 组件的数据库 host/port/password，PostgREST 的 URI、Langfuse/LiteLLM 的 DATABASE_URL、DBX 连接，以及本机 CLI checkpointer。普通托管 PG 必须具备 Supabase 所需扩展、角色与 schema；它不是一个任意新空库就能直接替换的服务。也可以整体使用托管 Supabase，通过 URL/anon key/service role 连接，并在目标项目执行本项目迁移。

本机迁移和 checkpoint 支持完整 DSN，避免在代码中拼接云密码：

```toml
[postgres]
agent_dsn_env = "CLOUD_AGENT_DATABASE_URL"
migration_dsn_env = "CLOUD_MIGRATION_DATABASE_URL"
```

相应变量放入 `secrets.local.env`。migration DSN 是有迁移权限的目标 Supabase 项目数据库账号。外置 PG 时同时禁用本地 `postgres` 与 `database-init`；由云端预先创建 `agent_local`、`litellm`、`langfuse` 库和对应账号。Supabase 的必要角色/扩展初始化由平台供应方负责。

## DBX 配置位置

公开模板为 `platform/config/dbx/connections.yaml`，实际含密码配置为 `.local/generated/dbx-connections.private.json`，`anp platform up/check` 登录 DBX 后按固定 ID upsert，不删除用户自建连接。外置资源可配置 `deployment.dbx_connections`；完整字段以锁定 DBX 版本为准。

本地预置：Supabase/业务 postgres 库（supabase_admin 管理账号）、agent_local、litellm、langfuse、Redis、ClickHouse。RustFS 使用 http://localhost:9001 控制台。

云绑定生成和本地连接均有验收；本次没有云账号，未声称云端网络、扩展、权限或迁移已实测。
