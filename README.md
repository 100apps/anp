# ANP：AI 原生应用工作台

默认用本地 CLI 直接运行业务代码，Web、HTTP CLI 与本地 CLI 共用业务服务、Supabase 身份和权限。

**V3 仍在验收。** 四存储、单容器平台、真实任务与两种 Qoder F5 均已通过本机检查，包含前后端与平台源码热更新后再次命中断点。WSL 和返回 Docker 各 76 项回归通过，293 条跨模式线程状态完整保留。最新云端流水线已完整成功并发布（85 项检查通过），本机现已完成固定摘要真实拉取、Compose 重建、85 项回归（含真实模型）及 DBX 性能复测；deep_dub、整片 RTF≤0.5 和 B 站发布仍待完成。[完整验收账本](docs/重构实施与验收.md)保留全部目标。

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

**GHCR 镜像已发布，并通过本机固定摘要拉取、启动和回归验收。** 私有镜像为 `ghcr.io/100apps/anp-platform:latest`，本轮固定源码标签为 `sha-af282f7599b96ff1373b7d6e9a1051c3526ccd70`；两个标签的已推送摘要均为 `sha256:72d02acd0c7cd247e2266fa7f506fe795d561e7d2a4cd4bf47aa8ac18246a9b3`。本机已完成 read:packages 本人授权、标准输入 Docker 登录、固定摘要真实拉取与 Compose 重建；11 个组件 ready，85 项检查 72.47 秒全部通过且启用了真实模型分支。原 299 条线程完整保留，旧 anp2 未切换。详见 `docs/evidence/v3-ghcr-local-20261018.json`。

完成 GitHub 本人验证并取得仓库/包读取权限后，可通过标准输入登录；不要把令牌写入公开配置或仓库：

```powershell
gh auth refresh --hostname github.com --scopes read:packages
$githubLogin = gh api user --jq .login
gh auth token | docker login ghcr.io --username $githubLogin --password-stdin
docker pull ghcr.io/100apps/anp-platform@sha256:72d02acd0c7cd247e2266fa7f506fe795d561e7d2a4cd4bf47aa8ac18246a9b3
```

也可自行源码构建：先运行 `uv run python scripts/sync_upstreams.py`，构建十项源码组件，再执行 `uv run python scripts/bundle_platform.py --tag anp/platform:v3 --checkpoint-exports`。切换前保留现有状态备份，并将 `platform.image` 设置为实际验收的标签或摘要。

```powershell
uv run anp platform up
uv run anp platform check
```

GitHub Actions 支持手动运行和 `v*` 标签触发：拉取最新上游、记录 SHA，各作业复现同一清单，组装并验收后发布。[39095879677](https://github.com/100apps/anp/actions/runs/39095879677) 已终态成功，85 项检查通过（77.93 秒），导出与服务/业务检查、两个标签推送及缓存收尾均通过。CI 不持有私人模型密钥，最后的真实上游模型调用分支提前返回；本机已使用真实 auto/auto:fast 补验通过，不能把该结果归入 CI。

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

API/Worker 使用 debugpy 跟踪 Uvicorn 热重载子进程，前端使用 Vite HMR 与 Edge 调试；平台 Python 源码改动后优雅退出、保存 Agent 状态并重启，调试器追踪子进程。第三方编译制品修改后需要重新构建。开发启动的就绪等待上限为 180 秒；启动断点暂停超过该时限后，需要重新启动调试组合。

WSL 调试前置任务在宿主机确认回环监听后，才发出独立就绪标记；检查只读监听表，不建立额外调试连接。原生调试日志仅保存在 Git 忽略的 `.local/logs/native-debugpy/`。真实 Qoder 已完成整组 F5、前后端与平台源码热更新后的断点、任务执行及结果保存，见 [WSL IDE 分项证据](docs/evidence/v3-native-ide-readiness-20261018.json)。

前端前置任务先验证 Vite 源码映射指向本工作区，再复用已有服务或启动新进程，并等待 API/Worker 就绪。不依赖 IDE 再次接收到旧后台任务的启动日志；启动日志在私有 `.local/v3-vite-f5.log`。单独启动前端仍可用 `uv run python -m anp.dev.v3 web`。

平台 URL 与 `.vscode/settings.json` 的 `anp.platformUrl` 要一致，WSL 路径映射 `/mnt/e/anp` 对应当前工作区。本机隔离环境使用 `http://127.0.0.1:8089`，业务端口 8181/8282/5174；具体由私有绑定决定。5684 是本机开发调试端口，不增加 Docker 发布端口。

首次原生运行从已构建镜像提取制品到受管理的 WSL `/opt/anp`；停止的临时导出容器随后删除，不运行嵌套 Docker。模式切换时先停止另一平台，再通过私有 Agent 归档交接，保留旧快照。

原生制品更新采用干净提取，避免旧模块残留；Agent 状态保留，DBX 本地配置先备份再恢复，备份保存在 `/opt/anp/state/dbx-backups`。提取未完成或镜像身份不符时，启动器会拒绝启动；重新执行原生准备可恢复中断的提取。

本地没有目标镜像时，原生准备会先执行拉取；已有镜像可以离线复用。私有 GHCR 首次拉取仍需要有效登录，拉取失败会停止准备，保留现有数据。

**验收边界：** 实测 IDE 为 Qoder CN IDE 1.34.0。两种组合均完成真实 F5、前后端断点与热更新、任务执行及结果保存；WSL 平台源码重载后的新控制器也命中同一断点。WSL 76 项检查通过（70.50 秒），返回 Docker 后 76 项通过（75.97 秒），均包含真实模型调用；293 条线程的 ID 和 values 哈希跨模式全部一致，27 个含停止状态的受保护容器 ID 未变。临时源码已逐字恢复。运行制品为本机既有镜像，不能替代最新云端镜像的验收。[Qoder 分项证据](docs/evidence/v3-qoder-f5-20261018.json)、[WSL 与交接证据](docs/evidence/v3-native-ide-readiness-20261018.json)。

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

最新源码可能带来数据库迁移，切换前保留现有数据。已完成五库逐库逻辑恢复、RustFS 当前对象、Redis RDB，以及 ClickHouse/Keeper 和 Agent/DBX 的同版本隔离恢复验证；私有归档位于 Git 忽略的 `.local/backups/`，最近指针为 `.local/v3-latest-*-backup.json`。各备份时间不同，不是跨存储原子快照，也不是跨版本迁移通过。切换前须重新核对忙碌线程和状态变化；见[完整备份证据与限制](docs/重构实施与验收.md)。

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
