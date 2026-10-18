# ANP：AI 原生应用工作台

默认用本地 CLI 直接运行业务代码，Web、HTTP CLI 与本地 CLI 共用业务服务、Supabase 身份和权限。

**V3 仍在验收。** 四存储、单容器平台、真实任务与 WSL 原生运行已经通过集成检查。Qoder 的 Docker 平台业务 F5 已实际命中前后端断点；热更新后再次命中也已通过，结束旧会话后的重复 F5 也已通过，WSL 组合尚待补齐。GHCR 流水线尚未完成发布、拉取与启动验证。deep_dub、整片 RTF≤0.5 和 B 站发布仍待完成。[完整验收账本](docs/重构实施与验收.md)保留全部目标。

仓库：[100apps/anp](https://github.com/100apps/anp)（私有）。旧 anp2 服务与数据保留，[V2 操作说明](docs/旧版V2操作说明.md)仅用于旧环境。

## 架构与目录

Docker 运行四类存储各一个实例，加一个平台容器。存储也可替换为云 URL/密钥。原生平台调试时只有存储运行在 Docker；业务代码始终留在宿主机，图定义通过只读 apps 目录提供给平台，节点在本机 Worker 执行。

```text
anp/
├─ apps/<id>/                 一个目录就是一个业务模块
│  ├─ app.toml                菜单、权限、服务和图入口
│  ├─ ui/                     shadcn/ui 页面
│  ├─ api/                    HTTP 路由
│  ├─ services/               CLI / HTTP / Web 共用的业务服务
│  ├─ nodes/                  各阶段的实际业务逻辑
│  ├─ flow/                   LangGraph 图与部署入口
│  └─ migrations/             业务关系、约束和 RLS
├─ console/                   登录、导航、连接管理与工作台外壳
├─ src/anp/                   CLI、身份、任务、执行适配和业务宿主
│  └─ dev/v3.py               两种 F5 和默认平台命令共用的编排
├─ platform/runtime/          平台进程管理、网关、Agent 和状态交接
├─ deploy/
│  ├─ storage/                PostgreSQL / Redis / ClickHouse / RustFS
│  ├─ platform/               一个常驻平台服务、一个发布端口
│  └─ build/                  上游源码构建适配
├─ third_party/               十项真实 Git submodule
├─ config/                    连接模板；本机配置与凭据不提交
├─ scripts/                   上游同步、构建、镜像组装、原生制品准备
├─ .github/workflows/         最新依赖构建、验收与 GHCR 发布
├─ .vscode/                   两种 F5、CLI 调试与后台任务
├─ migrations/                平台权限和任务模型
├─ tests/                     契约与真实服务验收
└─ docs/evidence/             可复核的运行证据
```

PG 按服务划分数据库与账号，ClickHouse 按库和授权隔离，Redis 按前缀/逻辑库使用，RustFS 按桶使用。Supabase 使用其官方 PostgreSQL 镜像中的扩展，不使用 Windows PostgreSQL。构建中间镜像不会成为额外常驻服务。

## 初始化和启动

需要 Docker Desktop Linux containers、uv/Python 3.12、Node.js 22+、pnpm；Windows 原生平台模式另需 WSL Ubuntu。

```powershell
git clone --recurse-submodules https://github.com/100apps/anp.git
cd anp
uv sync --frozen
pnpm install --frozen-lockfile
uv run anp init
```

编辑 `config/deployment.local.toml`，配置平台镜像/URL、本地存储选择、云资源、业务端口和卷前缀。宿主 URL 给 CLI/F5 使用，容器 URL 给平台使用；不能把容器 localhost 当作宿主地址。

**默认 GHCR 镜像尚未发布。** 本机已有测试镜像；新机器必须先构建。构建先运行 `uv run python scripts/sync_upstreams.py`，按 `scripts/build_components.py --help` 构建十项源码组件，再运行 `uv run python scripts/bundle_platform.py --tag anp/platform:v3`，将配置中的 `platform.image` 改成实际标签。

```powershell
uv run anp platform up
uv run anp platform check
```

GitHub Actions 支持手动运行和 `v*` 标签触发：拉取最新上游、记录 SHA，各作业复现同一清单，组装并验收后发布。流水线尚未实跑成功。CI 不持有私人模型密钥，仅跳过最后一项真实上游模型调用，本机另行验证。

## 默认本地 CLI

```powershell
uv run anp auth login --email admin@anp.local
uv run anp demo tasks create --title "我的任务" --text "你好，工作台。"
uv run anp demo tasks run <任务ID>
uv run anp demo tasks get <任务ID>
uv run anp demo tasks result <任务ID>
uv run anp demo tasks summary
```

不需要业务 API、Worker 或前端服务，但仍需访问平台存储和登录服务。JSON 输出与非零错误码便于 AI 调用，支持 `create --input input.json`。HTTP 模式加 `--transport http`，命令和功能一致，需要启动业务 API/Worker。

`uv run anp migrate` 只迁移业务模块；平台内部表由平台初始化。CLI 自动读取私有 `.local/generated/platform-v3/business.env`，显式环境变量优先。本机登录信息在 `config/local-credentials.md`。

## F5 开发

VS Code 或兼容 Qoder 打开 `E:\anp` 或 `anp.code-workspace`，选择：

- **ANP V3: F5 业务（Docker 平台）**：Docker 四存储和单平台，本机 API、Worker、Vite。
- **ANP V3: F5 业务 + 平台（WSL 源码）**：Docker 四存储，WSL 运行平台源码，本机业务。

API/Worker 使用 debugpy 跟踪 Uvicorn 热重载子进程，前端使用 Vite HMR 与 Edge 调试；平台 Python 源码改动后优雅退出、保存 Agent 状态并重启，调试器追踪子进程。第三方编译制品修改后需要重新构建。

WSL 调试前置任务在宿主机确认回环监听后，才发出独立就绪标记；检查只读监听表，不建立额外调试连接。原生调试日志仅保存在 Git 忽略的 `.local/logs/native-debugpy/`。真实 Qoder 已取得平台源码第 175 行断点堆栈，完整组合仍待继续验收，见 [WSL IDE 分项证据](docs/evidence/v3-native-ide-readiness-20261018.json)。

前端前置任务先验证 Vite 源码映射指向本工作区，再复用已有服务或启动新进程，并等待 API/Worker 就绪。不依赖 IDE 再次接收到旧后台任务的启动日志；启动日志在私有 `.local/v3-vite-f5.log`。单独启动前端仍可用 `uv run python -m anp.dev.v3 web`。

平台 URL 与 `.vscode/settings.json` 的 `anp.platformUrl` 要一致，WSL 路径映射 `/mnt/e/anp` 对应当前工作区。本机隔离环境使用 `http://127.0.0.1:8089`，业务端口 8181/8282/5174；具体由私有绑定决定。5684 是本机开发调试端口，不增加 Docker 发布端口。

首次原生运行从已构建镜像提取制品到受管理的 WSL `/opt/anp`；停止的临时导出容器随后删除，不运行嵌套 Docker。模式切换时先停止另一平台，再通过私有 Agent 归档交接，保留旧快照。

原生制品更新采用干净提取，避免旧模块残留；Agent 状态保留，DBX 本地配置先备份再恢复，备份保存在 `/opt/anp/state/dbx-backups`。提取未完成或镜像身份不符时，启动器会拒绝启动；重新执行原生准备可恢复中断的提取。

本地没有目标镜像时，原生准备会先执行拉取；已有镜像可以离线复用。私有 GHCR 首次拉取仍需要有效登录，拉取失败会停止准备，保留现有数据。

**验收边界：** 最新原生更新流程通过 54 项检查，返回 Docker 后综合 56 项通过；十条既有线程在热重载后不变，含原生新建任务的十二条线程在切回 Docker 后状态一致，托管进程退出清理通过。底层调试协议通过五会话重新附加检查。[最新更新与交接证据](docs/evidence/v3-native-artifacts-update-20261018.json)。随后已在 Qoder 实际命中 `service.py:23` 与 `app.tsx:22`，创建的任务三个节点完成、对象报告与数据库一致；随后实际修改前后端源码触发热更新，均再次命中同一断点，新 HTTP 任务三阶段完成且保存结果一致，临时源码逐字恢复；结束旧会话后完整重复 F5 已再次启动三个调试会话，新任务命中断点并执行成功；WSL 组合仍待验证。[Qoder 分项证据](docs/evidence/v3-qoder-f5-20261018.json)。VS Code 自身仍为 Restricted Mode，信任设置由用户操作。

## 单端口管理入口

以下路径相对于 `platform.url`：

| 路径 | 用途 |
| --- | --- |
| `/workbench/`、`/demo/` | 登录、管理工作台和示例模块 |
| `/api/` | 业务 API |
| `/agent/` | 图执行与 Studio 连接 |
| `/supabase/` | Studio、Auth、REST、Storage |
| `/dbx/` | 已预配置 PG 各库、Redis、ClickHouse 连接 |
| `/langfuse/` | 观测、user/session 与阶段评分 |
| `/litellm/ui/`、`/litellm/v1` | 网关后台与模型 API |
| `/lens/ui/` | Agent 追踪与 auto 模型分析 |
| `/_platform/health` | 全部必要组件就绪后返回 200 |

RustFS 控制台属于存储层，地址由宿主端口配置。LangSmith Studio 是外部开发工具；按用户选择仅使用本机 Langfuse，保留 LangSmith 云追踪未配置提示。

新版 Lens 使用独立追踪写入 Key，`auto` 已完成合成错误样本的真实诊断；接入地址、限额与验收边界见 [Lens 新版接入](docs/Lens新版接入.md)。

本机连接与图执行并发的实测、复现命令及参数选择见 [平台性能测量](docs/平台性能.md)。提高执行器并发未改善所测 demo 吞吐，默认保留为 1。

复制业务：`uv run anp app new my_app --from demo`，随后检查权限、迁移与 flow，执行 `uv run anp migrate`。详见 [Agent 与业务开发](docs/Agent与业务开发.md)。

## 更新前的数据备份

最新源码可能带来数据库迁移，切换正式镜像前应保留各数据库的逻辑备份。新平台五库已逐库实际还原验证，备份位于 Git 忽略的 `.local/backups/v3-postgres-before-ci-20261017T211306Z/`，私有备份不要提交仓库。Supabase 镜像中完整还原所有权需要 `supabase_admin` 管理角色，普通 `postgres` 角色会因不能 `SET ROLE supabase_admin` 失败。

本次验证保留所有权和 ACL，但依赖当前实例已有角色；不代表跨库原子备份或四种存储的完整灾难恢复。后续新写入不在该快照内，正式升级前需要按当时数据重新备份。实际范围见 [备份与还原证据](docs/evidence/v3-postgres-backup-restore-20261018.json)。

工作台“平台连接”的管理地址来自当前部署配置。RustFS 默认使用 API 端口加一的控制台端口和 `/rustfs/console/` 路径；也可设置 `storage.s3_console_url`、`storage.s3_health_url`。改用云对象存储时，控制台和健康地址需显式提供；未提供健康接口时显示“外部工具”，不会探测旧本机实例。生成配置后重新启动 F5/API，使新的环境变量生效。

## 单组件构建验证

修复某个上游组件时，可单独执行 GitHub 的“验证单组件源码构建”，使用与正式流水线相同的 CI 配方和磁盘门槛：

```powershell
gh workflow run component.yml -f component=studio
gh workflow run component.yml -f component=langfuse-web
```

该入口先更新上游并记录 SHA，只推送 `anp-build-组件:run-编号` 中间制品。正式平台流水线的 `component_cache_run` 支持按顺序提供最多四个候选构建编号（逗号分隔）；只有仓库内批准的工作流、实际源码 SHA 和配方摘要都一致才会复用。单组件成功后，仍需正式流水线完成十组件组装、服务启动、业务和权限检查，才发布平台镜像。

## deep_dub 后续设计

[数据关系、工作台与流程草案](docs/deep_dub设计草案.md)基于旧项目任务和媒体工作台的只读取证，包含版本、RLS、质量审核、设备租约和完整计时口径。目前没有创建业务模块或执行迁移；平台完整验收通过后才实施。
