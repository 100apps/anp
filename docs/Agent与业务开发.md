# Agent 引擎与本机业务开发

## 运行边界

存储由四个官方容器提供；平台容器内包含原来的十个第三方组件，以及使用平台 Python 环境运行的 Agent Server。Agent 的 LangGraph 包从真实 `third_party/langgraph` 源码构建，并检查安装依赖一致性。

平台镜像不复制 `apps/`。运行时将业务目录只读挂载到 `/opt/anp/extensions/apps`，作为图部署输入。业务 API、Worker、前端在本机运行；图编排代码由平台引擎加载，节点通过 HTTP 回调本机 Worker。这些职责不能混为“所有业务代码都在平台内执行”。

引擎采用已锁定的官方 LangGraph 本地开发 API，提供 assistants、threads、runs、状态与 SSE；不是自行仿造的同名接口。当前引擎是 local-dev/inmem 运行版，开发线程与检查点使用独立 `agent_state` 命名卷保存；默认本地 CLI 的检查点使用 PostgreSQL `agent_local`。不把这个配置称为生产版分布式队列。业务任务、权限、结果始终经 Supabase API/RLS 保存。

LangSmith 云追踪保持关闭；本机 Langfuse 记录用户、会话、节点和评分。新版 Langfuse 的 events_only 模式使用 `/api/public/v2/observations` 与 `/api/public/v3/scores` 查询，旧 traces API 的 404 不能直接判定观测丢失。

## 业务目录

```text
apps/demo/
├── app.toml                  # 菜单、权限、服务与图入口
├── api/router.py             # HTTP 适配，调用业务服务
├── services/tasks.py         # 本地 CLI、HTTP、Web 共用的用例
├── nodes/                    # 本机 Worker 执行的节点逻辑
├── flow/
│   ├── state.py              # 图状态与 Studio 输入
│   ├── graph.py              # 自由使用 LangGraph 定义边、条件与子图
│   └── deployment.py         # 把节点适配器绑定为远程 Worker 调用
├── evaluators.py             # 业务质量判断
├── migrations/               # 业务自己管理的数据库模型
└── ui/index.tsx              # 工作台菜单中的业务页面
```

图的默认部署入口为 `apps.<id>.flow.deployment:graph`，可通过 `[flow].deployment_entry` 替换。`build_graph(node_factory, checkpointer=None)` 使用节点工厂：本地执行适配器直接调用节点，平台执行适配器通过 Worker API 调用节点。图不是只允许串行节点的 JSON 描述；条件分支与自定义 Studio 字段有实际契约测试。

Studio 自动建任务时，业务声明 `studio_entry_node` 与 `studio_payload_fields`。demo 使用 normalize 和 text/title；其他业务可以使用自己的节点与输入字段。输入校验由业务服务负责。

## 隔离验证的启动方式

当前验证保留旧 anp2，平台使用 8089、本机 API 8181、Worker 8282、Vite 5174；数据库使用新命名卷。以下不是最终 F5 配置，F5 原生平台模式仍需单独验收。

```powershell
uv run python scripts/configure_platform.py --public-url http://localhost:8089 --smoke --deployment .local/generated/platform-v3/smoke-bindings.toml
uv run --env-file .local/generated/platform-v3/business.env python -m anp.management.applications

# 三个本机进程，各自在终端或调试配置启动
uv run --env-file .local/generated/platform-v3/business.env python -m anp.dev.supervisor api
uv run --env-file .local/generated/platform-v3/business.env python -m anp.dev.supervisor worker
uv run --env-file .local/generated/platform-v3/business.env python -m anp.dev.frontend
```

图目录挂载到平台 Compose，平台引擎从已启用的 app.toml 发现图。新增应用后重新生成平台配置、执行业务迁移并重启平台加载图。API 和 Worker 代码由本机热重载；前端的 HMR WebSocket 经同一个平台端口转发。`/demo/` 会跳转到 `/workbench/apps/demo`，工作台、业务 API、Agent API 分别通过 `/workbench/`、`/api/`、`/agent/` 访问。

客户端使用同一个私有 profile：

```powershell
uv run --env-file .local/generated/platform-v3/business.env anp auth login
# 默认本地执行，不启动业务 Web 服务也可使用
uv run --env-file .local/generated/platform-v3/business.env anp demo tasks create --text '你好，AI'
uv run --env-file .local/generated/platform-v3/business.env anp demo tasks run <任务ID>
# HTTP CLI 经平台入口调用本机业务 API
uv run --env-file .local/generated/platform-v3/business.env anp --transport http demo tasks list
```

本地 CLI 仍需要平台的对象、权限、文件和 PostgreSQL 服务。“不需要 Web 服务”指不必启动本机业务 API、Worker、前端，而不是离线绕过认证和基础设施。

## 当前实际验证

- 本地与 HTTP CLI 同输入、同标题的报告完全一致；两者均保存三条节点记录、结果表和可读取的 JSON 对象。
- 默认本地 CLI 的验收还把业务 API/Worker 地址设置为不可连接地址，确认没有依赖这些业务 Web 进程；平台存储与认证保持可用。
- HTTP 任务确实由容器内 Agent 经 HTTP 调用本机 Worker，没有退回本机 CLI 执行。
- Studio SSE 通过 `/agent/` 完成三个节点并保存任务结果。
- Web 已实际登录、创建并完成任务，截图在 `docs/evidence/v3-workbench-completed.png`。
- 修改 Studio 执行方式标签后，浏览器不刷新即更新，验证了平台代理下的前端 HMR。
- 本地/HTTP 任务分别有 3/6 条 Langfuse 观测，各有 6 条通过评分；user 与 session 正确。

两种 F5 的真实 IDE 断点、原生平台启动、GHCR 发布拉取、整体性能及 deep_dub 仍未完成。这些检查通过前，不将当前阶段标为整体交付。

## 局部性能记录

将图执行依赖推迟到执行命令、避免查询命令为了 flush 加载整个观测 SDK 后，同机三次默认 `demo tasks list` 的中位耗时从 1.863 秒降到 1.495 秒，约降低 20%。样本分别为 `[1.844, 1.863, 1.864]` 与 `[1.495, 1.507, 1.482]` 秒，均包含进程冷启动和真实权限/对象 API 查询。

这只是 CLI 列表命令的局部测量，不代表整个平台吞吐或配音 RTF。前端 `/workbench/` 生产构建通过，主 JS 约 700 KB（gzip 约 211 KB），仍有后续拆包空间。
