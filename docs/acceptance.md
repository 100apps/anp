# 本机验收记录

验收日期：2026-10-17。平台项目：`anp2`，数据卷前缀：`anp_v2_`。以下均来自本机实际运行，未使用 Windows PostgreSQL。

## 已通过

| 项目 | 结果与证据 |
|---|---|
| 默认本地 CLI | 8180/8281 未监听时，登录、创建、执行、查询、读取结果成功 |
| HTTP CLI | 经 API → Agent Server → Worker 三节点执行，结果与本地模式一致 |
| Web | Supabase 实际登录，Web 创建并执行任务，三个节点完成、报告显示 |
| ACL / RLS | 未登录 401，viewer 写入 403，越工作空间读取 API 404、直接 REST 空结果，私有文件读取被拒绝 |
| 成员权限 | 管理员调整角色生效，普通成员不能改角色；viewer 不能创建任务 |
| 幂等与取消 | 重复请求键返回同一任务；不同内容冲突；待执行任务取消成功；Worker 无令牌 401 |
| DBX | URL 登录、六个连接可见，四个 PG 库、Redis、ClickHouse 全部连接测试通过 |
| DBX 保存凭据 | 按 Web 的默认字段规范初始化连接；读取保存后的凭据引用执行 test 与 connect，六个连接全部通过 |
| 单实例基础设施 | 运行容器清单中 postgres / redis / clickhouse / rustfs 各一个 |
| RustFS | S3 中真实文件内容与业务返回报告一致 |
| Langfuse | 实际读取到 normalize/analyze/save 三个节点观测 |
| LiteLLM | demo-mock 完成模型网关 API 调用，不消耗外部模型费用 |
| 新业务复制 | app new zcopy 后，本地和 HTTP 执行均 succeeded；测试模块已归档到 .local/acceptance，不加入默认菜单 |
| F5 前后端 | Qoder 中实际启动 API、Worker、Vite、Edge；API/Worker 源码行断点、TypeScript 源码断点实际命中 |
| 容器图调试 | Docker 内 Agent 图节点实际暂停，IDE 能查看 app_id/name/state，继续后完成节点执行；使用纯 Python 调试兼容模式 |
| 热更新与重载调试 | API pid 10376 → 38116、Worker pid 25852 → 9744，revision v1 → v2；重载后 Worker 断点仍命中；前端标题改动立即可见 |
| 依赖与构建 | uv sync --frozen、pnpm install --frozen-lockfile、TypeScript + Vite 生产构建通过 |
| 数据持久化 | 整体 restart，再强制重建 Agent/DBX 容器后，用户 ID、任务状态、Agent thread、报告内容 SHA256、DBX 配置 SHA256 均一致 |
| 云绑定生成 | 覆盖资源环境变量、私有变量展开、移除本地资源及其依赖，配置拼写错误提前报错 |

完整回归：`12 passed in 31.01s`。公共 HTTP 路由改为读取业务 services 声明后，相关 HTTP/ACL/绑定专项再次验证：`6 passed in 9.25s`。

最后再次按 F5，Qoder 显示 Agent、API、Worker、前端四个活动调试会话。最终任务 `2c6e9940-4ccc-41e6-b2c6-f6eded79d637` 经 HTTP CLI 创建并运行，状态 succeeded、三个节点齐全、报告保存成功；六个 DBX 连接再次全部通过。本地 CLI 与绑定专项最后复验为 `4 passed in 11.71s`。

补齐 DBX Web 驱动默认字段后，平台/存储/追踪/网关专项复验：`4 passed in 6.47s`。DBX 网页已实际连接 PostgreSQL，Schema 下拉能浏览 `platform`、`app_demo`、`auth`、`storage` 等 schema，验收后停留在 platform。

原始日志位于 `.local/integration-results-final.log`、`.local/http-final-results.log`、`.local/frontend-build.log`、`.local/persistence-results.json`、`.local/copy-results.json`。这些为本机运行证据，不提交私有日志。

## 截图

- [Web 任务已完成](evidence/web-task.png)
- [DBX 六个预配置连接](evidence/dbx-connections.png)
- [DBX 已连接并浏览 Schema](evidence/dbx-schemas.png)
- [API 行断点](evidence/api-breakpoint.png)
- [Worker 重载后断点](evidence/worker-breakpoint.png)
- [前端 TypeScript 断点](evidence/frontend-breakpoint.png)
- [Docker 内 Agent 图断点](evidence/agent-breakpoint.png)
- [最终 F5 四个调试会话](evidence/f5-complete.png)
- [最终任务展示](evidence/final-workbench.png)
- [Studio 实际连接并显示 demo 图](evidence/studio-connected.png)

Web 验收任务：`a4feb91a-1cb1-4fc1-ad02-60d48731e3d9`。
重建持久化验收任务：`3538cc26-f3e8-48af-bd97-a315e3757ced`。

## 实际边界

- 这是本地 MVP，Agent 使用官方开发运行时和独立状态卷，尚未建设生产 HA / 分布式任务恢复。
- 前端和 API/Worker 热更新；图结构修改在容器调试模式下需重启 Agent 调试。业务节点代码在 Worker 中仍自动重载。
- Studio 是官方外部网页开发工具，需要用户自己的 LangSmith 登录；本次不代用户创建云账号。
- Studio 连接补验：在用户已登录的 Chrome 中，经用户授权配置浏览器本地 Authorization 请求头，已实际显示 demo 的 normalize → analyze → save 图；引擎 Token 认证保持启用。连接说明见 studio.md。
- 云绑定提供配置机制与模板，本次没有云资源凭据，未做真实云迁移验收。
- 第三方管理后台使用自身登录，业务 Web/CLI 使用 Supabase 统一身份；没有冒充跨产品 SSO。
- 前端生产构建有单包约 700 kB 的体积提示，不影响运行和 sourcemap 调试。

## 复验

先停止 IDE 的 ANP 联调，确认 8180/8281 未监听，然后运行：

```powershell
.\scripts\check.ps1
```

该脚本要求先验证“没有业务 Web 服务也能运行 CLI”，随后测试夹具只启动并清理自己创建的 API/Worker 进程。已有 IDE 服务不会被测试自动终止。

## 2026-10-17 日志与评分专项

本地/HTTP 身份关联、节点评分、负例校验和 LiteLLM 正文持久化专项：4 passed in 34.84s。详见 [日志与评分](observability.md)。

## Studio 文本直跑修复

已通过浏览器提交文本，normalize、analyze、save 完成且 saved=true。Studio 专项 3 项通过（真实创建和保存、同线程换文本、内部接口鉴权及空输入提示）；本地/HTTP 一致性与可观测性回归通过。用户选择只用本机 Langfuse，保留 LangSmith 云追踪缺少 Key 的提示。截图：docs/evidence/studio-text-run-fixed.png。

## LiteLLM Lens 追踪启用

真实 OTLP 往返、鉴权和跨库隔离：2 passed in 0.17s；网关 mock 调用和两项云绑定回归通过。浏览器可展开 Agent、LLM、工具三个 span。Investigations 仍等待真实分析模型及 Worker 配置，不计入已完成验收。

## FreeLLMAPI 与 Lens Investigations

2026-10-17：auto、auto:fast 均通过真实模型调用。Lens Worker 已连接，使用 auto 对测试追踪完成诊断（completed），保存 1 条 finding 和 1 条 assessment。默认手动执行，关闭周期扫描。Lens 鉴权/隔离及 mock 网关回归：3 passed in 0.25s。截图 docs/evidence/lens-auto-completed.png。
