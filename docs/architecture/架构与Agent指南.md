# 架构与Agent指南

ANP 的主交付是 Mac ARM64、Linux x64 可搬移原生目录包。包内提供工具、存储、平台与可单独复制的 Business；服务由包内 Python 管理器按依赖顺序启动，业务 HTTP 与代理统一由 LangGraph Agent Server 承载。

`make build` / `make dist` 对应 `native/`，包含 Supabase、Langfuse、LiteLLM、DBX 和可选 OTel/LGTM；Realtime 不随原生包发布。`NATIVE_PROFILE=poc` 保留精简构建。Docker 交付继续使用 `make docker-build`，独立于原生运行，不作为其隐式依赖。两种交付分别验收，详见 [原生说明](../../native/README.md) 与 [Docker 开发环境](../development/Docker开发环境.md)。

## 入口地图

| 工作 | 入口 |
| --- | --- |
| 原生构建、打包与依赖来源 | native/build.py、native/locks/、native/defaults/ |
| 原生生命周期与进程身份检查 | native/runtime/control.py、native/runtime/linux_process.py |
| 原生目录、压缩包与干净环境验收 | native/checks/、native/check.py、native/linux_check.py |
| 普通LangChain Workflow | business/src/anp_business/workflow_demo/ |
| Deep Agents与工具 | business/src/anp_business/deepagent_demo/ |
| LangGraph路由与静态工作台 | business/langgraph.json、business/src/anp_business/server/http.py |
| 平台HTTP/WebSocket代理 | business/src/anp_business/server/platforms.py |
| shadcn后台 | business/ui/src/ |
| 镜像与官方产物 | docker/toolbox/Dockerfile、versions.lock.json、fetch.py |
| 生成构建阶段 | scripts/generate_dockerfile.py、docker/build/recipes/ |
| 启动配置与依赖图 | docker/toolbox/runtime/settings.py、supervisor.py |
| 快捷命令与进程身份检查 | Makefile、scripts/manage.py、scripts/dev.py |
| 本地与CI验收 | scripts/check.py、acceptance.py、check_debug.py |

## 运行与配置边界

原生包的 `defaults/` 保存只读默认值，`config/anp.json` 保存用户配置，`var/` 保存本实例数据、日志、生成配置与私有凭据。源码构建缓存和隔离验收实例只写入仓库 `var/`。不要用构建覆盖运行目录，不要因源码目录改名搬动数据根。Business 依靠自己的配置文件与锁文件连接平台，可脱离整套构建独立开发。

Docker 的根 .env 控制镜像、资源、服务组合和端口；business/.env 控制宿主业务。/data/config/services.json 按服务覆盖平台环境，不将其他服务的秘密自动传给无关进程。effective 目录由启动器生成，不能手改作为持久配置。

/data保存配置、数据和日志；/data/run单独挂载tmpfs，保存可重新生成的运行状态与Unix socket，不能放在macOS共享文件系统中。镜像升级不隐式迁移数据目录或主版本。PostgreSQL保持Supabase17兼容扩展组合。需要外部数据库时，明确配置各平台连接，并决定是否关闭自动本地依赖。

业务对象统一通过 Supabase Data API/RPC 管理，文件通过 Storage；PostgreSQL、RustFS 仅为内部基础设施。数据库初始化、迁移与独立验收可以使用底层连接。原生 Supabase 保留 Auth、PostgREST、Storage、Studio、Meta，由 Agent Server 代理；Realtime、Envoy 属于另行保留的 Docker 交付范围，不能据此声明原生包已支持。

业务默认演示模型不调用付费API。普通Workflow验证确定性节点与摘要；Deep Agents验证图、资料工具与报告。业务范围为 Workflow 与 Deep Agent 两个演示。

## 工程选择与参考

采用多阶段构建、固定源码与下载校验、发布不可变SHA标签、原生双架构runner、验收通过后才更新latest。首次使用已发布镜像不检出平台submodule；需要定制源码才执行make sources。

参考：[Docker多架构CI](https://docs.docker.com/build/ci/github-actions/multi-platform/)、[Supabase二进制缓存](https://github.com/supabase/postgres/blob/develop/nix/docs/binary-cache.md)、[LangGraph自定义HTTP应用](https://reference.langchain.com/python/langgraph-cli/schemas/HttpConfig/app)、[GHCR发布与权限](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。这些是设计依据，不能代替本项目的验收记录。

## AI协作

make context输出不含凭据的JSON，AGENTS.md提供操作边界；锁文件和验收JSON作为事实源。新增业务优先添加图、工具与对应业务测试，沿用HTTP扩展；不要复制第二套服务器、配置加载器或进程管理器。报告必须区分当前结果和历史证据，失败时先读取本项目var/logs及服务私有初始化日志，避免打印完整环境变量。


## 原生单入口

当前原生源码将网关并入 Agent Server 的 HTTP 扩展，入口为 `business/src/anp_business/server/ingress.py`，与 `platforms.py` 共用 HTTP 流式代理。`security.py` 先校验平台管理员身份和来源，再进入代理。Auth/Data/Storage API 委托上游身份与 RLS 校验，管理页面不能通过 API 白名单绕过登录。默认配置使用 `ports.business` 单入口，旧 gateway 端口自动兼容迁移；原生构建锁已移除 Caddy。Docker 全功能交付的 Envoy 与当前原生单入口变更分开。
