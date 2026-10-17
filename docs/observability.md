# 日志与评分

## 身份和执行关联

- `user_id`：任务记录中的 `created_by`，即 Supabase 用户 UUID，不使用邮箱或凭据。
- `session_id`：任务 UUID，同一个任务的多次执行归在同一 session。
- `trace_id`：首次执行为任务 UUID 去掉连字符；后续 attempt 使用确定性 UUID 单独追踪。
- 任务名通过 `trace_name` 传播，metadata 包含应用、工作空间、attempt 和执行模式。
- 业务节点记录输入、输出；失败时记录 ERROR 和错误消息。

本地 CLI 与 HTTP/Web 通过公共执行器使用相同约定。HTTP 引擎从 API 的运行 metadata 获取上下文，Worker 则从已授权的任务记录重新读取身份，不信任节点输入中自报的用户身份。

在 Langfuse Sessions / Users 中查看新执行产生的记录。既有缺少身份的历史观测未做回填。

## 默认评分器

平台初始化会幂等创建 BOOLEAN 类型的 Score Config。业务节点提交成功后执行规则评分，分数绑定具体业务节点 observation，可在 Scores 面板查看。

| 名称 | 含义 |
| --- | --- |
| `node_success` | 节点执行并提交幂等记录成功为 1；失败为 0 |
| `normalization_valid` | 文本归一化及 SHA256 校验 |
| `analysis_consistency` | 字符数、词数、句数、词频与预览一致性 |
| `artifact_saved` | 查询结果表，并从私有对象存储读回 JSON，与结果报告和前序节点输出比较 |

这些是确定性规则评分，尚未配置 LLM 主观质量评审。每次完整 demo 执行产生六条评分。评分 ID 根据 trace、节点和评分名确定，避免重复写入。评分器或遥测服务异常写入应用错误日志，不撤销已提交的业务结果。

业务在 `app.toml` 中注册：

```toml
[evaluation]
entry = "apps.demo.evaluators:score_node"
```

异步函数签名为 `score_node(name, state, output, context)`，返回 `{评分名: bool}`。新增评分名称时同步在 `src/anp/sdk/tracing.py` 的 `SCORE_DESCRIPTIONS` 中注册说明；下次初始化或首次使用时创建配置。规则变更应更新 `evaluator_version`。

## LiteLLM 日志

`platform/config/litellm/models.yaml` 开启：

```yaml
general_settings:
  store_model_in_db: true
  store_prompts_in_spend_logs: true
  disable_spend_logs: false
```

当前实例也已通过管理 API 保存 `store_prompts_in_spend_logs=true`。管理数据库中的设置参与运行时配置，以后若在 UI 修改应同步部署配置。

打开 http://localhost:4000/ui 的 Logs，选择新请求，在 Request & Response 查看正文。本版本请求存于 `proxy_server_request`，响应存于 `response`，不能仅根据 `messages` 列判断是否记录。日志批量写入，页面可能稍后才刷新。开启前未记录的正文无法恢复。

## 本次验收

2026-10-17 执行：

```powershell
uv run pytest tests/test_05_observability.py -q
```

结果：**4 passed in 34.84s**。

覆盖本地 CLI、HTTP CLI 的真实任务执行、所有业务/引擎观测的用户与 session、六条评分及节点关联、错误统计被评分器拒绝，以及 LiteLLM 新请求与响应的数据库持久化。另行验证了两种执行模式结果一致。

Agent 镜像已重新构建。验收期间 Agent 保留 5678 调试端口并直接启动；原 IDE 附加连接若因容器重启断开，重新 F5 会恢复项目既定的完整调试启动配置。

## LiteLLM Lens

Lens OTLP 追踪已启用，复用现有 ClickHouse 的 litellm 独立库，配置及 Investigations 前置条件见 [LiteLLM Lens](litellm-lens.md)。
