# 架构与Agent指南

ANP 是单机 AI 应用脚手架：一个 Debian slim 工具镜像承载四存储与平台，业务代码在宿主用 uv 和 pnpm 运行。服务进程由 Python 标准库管理器按依赖顺序启动，独立UID、日志和环境；不运行Docker-in-Docker。

## 入口地图

| 工作 | 入口 |
| --- | --- |
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

根.env控制容器镜像、资源、服务组合和端口；business/.env控制宿主业务。/data/config/services.json按服务覆盖平台环境，不将其他服务的秘密自动传给无关进程。effective目录由启动器生成，不能手改作为持久配置。

/data只保存配置、数据、日志与运行状态；镜像升级不隐式迁移数据目录或主版本。PostgreSQL保持Supabase17兼容扩展组合。需要外部数据库时，明确配置各平台连接，并决定是否关闭自动本地依赖。

业务默认演示模型不调用付费API。普通Workflow验证确定性节点与摘要；Deep Agents验证图、资料工具与报告。原译配代码作为可选业务保留，本轮发布只以两个Demo作为业务验收范围。

## 工程选择与参考

采用多阶段构建、固定源码与下载校验、发布不可变SHA标签、原生双架构runner、验收通过后才更新latest。首次使用已发布镜像不检出平台submodule；需要定制源码才执行make sources。

参考：[Docker多架构CI](https://docs.docker.com/build/ci/github-actions/multi-platform/)、[Supabase二进制缓存](https://github.com/supabase/postgres/blob/develop/nix/docs/binary-cache.md)、[LangGraph自定义HTTP应用](https://reference.langchain.com/python/langgraph-cli/schemas/HttpConfig/app)、[GHCR发布与权限](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。这些是设计依据，不能代替本项目的验收记录。

## AI协作

make context输出不含凭据的JSON，AGENTS.md提供操作边界；锁文件和验收JSON作为事实源。新增业务优先添加图、工具与对应业务测试，沿用HTTP扩展；不要复制第二套服务器、配置加载器或进程管理器。报告必须区分当前结果和历史证据，失败时先读取本项目var/logs及服务私有初始化日志，避免打印完整环境变量。
