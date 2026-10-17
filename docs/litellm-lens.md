# LiteLLM Lens

## 已启用

入口：http://localhost:4000/ui/lens/?tab=traces

本机固定版本 v1.105.1 使用网关内置 Lens。`platform/config/litellm/models.yaml` 已配置：

```yaml
general_settings:
  tracing:
    store: clickhouse
```

复用平台唯一 ClickHouse 实例，独立数据库为 `litellm`。`litellm_writer` 只有该库范围的权限；`litellm_reader` 只有该库的 SELECT 权限。不能读取 Langfuse 所用的 `default` 库。凭据写入本机私有配置和 `config/local-credentials.md`，不放入公开配置。

Compose 的 `litellm-tracing-init` 是一次性初始化容器，跟随 `llm` profile 启动；创建数据库和账号后退出，不是第二个数据库实例。LiteLLM 在初始化完成后启动并自动建 Lens 表。现有对象、业务任务和 Langfuse 数据保留。

## 追踪接入

Lens 接收 OTLP/HTTP（JSON 或 protobuf）：

- 地址：`http://localhost:4000/v1/traces`
- 请求头：`Authorization: Bearer <LiteLLM virtual key>`
- 容器网络内使用 `http://litellm:4000/v1/traces`。

此前 LiteLLM 的 Logs 是网关请求日志；Lens 的 Traces 是 Agent 主动上报的执行追踪。开启接收端不会自动把既有 Langfuse 观测或旧网关日志转换为 Agent trace。现有业务追踪仍发往本机 Langfuse；需要双写的业务应另行配置 OTLP exporter，并使用专用虚拟 Key。

页面内置的 **Send a test trace** 已成功执行，能查看 `weather_agent`、LLM 和工具三条 span；这是 LiteLLM 的测试样本，不是真实业务调用。

## Investigations

已部署同版本镜像运行的 `lens-worker`，模型为用户指定的 **auto**。页面显示 Worker connected，真实诊断任务已完成并保存 1 条分析结论与 1 条执行评估。

示例“ANP 手动诊断”只分析 `litellm-sample-agent` 测试追踪，每次抽取 1 条，点击 **Run now** 运行；`enabled=false` 表示关闭定时扫描。创建新的诊断时可选择自己的 Agent 与检查标准。

Worker 使用独立 Token，分析虚拟 Key 仅允许 `auto`，限制 10 RPM / 20000 TPM。上游聚合路由没有固定价格表，本网关内部记账价显式设为 0，以满足 Lens 的模型价格字段要求；这不代表上游永远免费，页面 $0 及 $1 预算不能当作上游实际费用或硬性消费上限。当前不运行周期诊断。

幂等配置命令：

```powershell
uv run python -m anp.management.lens
uv run anp init
docker compose -p anp2 -f platform/compose.yaml --profile llm up -d --no-deps lens-worker
```

命令复用已保存的分析 Key 和 Worker；注册信息持久化在 LiteLLM PostgreSQL，Token 留在本机私有 env。

## 上游模型

- `auto` → FreeLLMAPI `auto`。
- `auto:fast` → FreeLLMAPI `auto:fast`。
- 基础地址：`http://24.199.104.17:3001/v1`，可通过 `[litellm].upstream_url` 改动。
- 密钥：`config/secrets.local.env` 的 `FREELLM_API_KEY`，通过环境变量注入 LiteLLM，不写进公开模型 YAML。

两个模型均已通过 LiteLLM 真实调用并返回“连接成功”；`auto` 另外完成了 Lens 的结构化分析。业务继续调用本机 `http://localhost:4000/v1`，请求中指定 `model: "auto"` 或 `model: "auto:fast"`，不直接持有上游密钥。

## 启动和云资源替换

```powershell
uv run anp init
uv run anp platform up --llm
```

只重启网关时：

```powershell
docker compose -p anp2 -f platform/compose.yaml --profile llm up -d --no-deps litellm
```

云端 ClickHouse 预建数据库及账号后，通过 `[deployment.environment.litellm]` 替换 `CLICKHOUSE_URL`、`CLICKHOUSE_READER_URL`、`CLICKHOUSE_DATABASE`；同时从本地服务中移除 `clickhouse` 与 `litellm-tracing-init`，参见 `config/cloud-bindings.example.toml`。不要直接套用新版独立 Lens 的配置，本机版本对应内置实现。

## 验收

`tests/test_07_lens.py` 验证真实 OTLP 写入、追踪详情与列表读取、未登录拒绝访问、只读账号授权及跨库隔离。另验证原有 mock 模型网关调用与云配置绑定未受影响。
