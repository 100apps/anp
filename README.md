# ANP：AI 原生应用工作台

默认用本地 CLI 直接运行业务代码，Web、HTTP CLI 与本地 CLI 共用业务服务、Supabase 身份和权限。

**V3 仍在验收。** 四存储、单容器平台、真实任务与 WSL 原生运行已经通过集成检查。两种 F5 配置已实现，实际 IDE 验收仍受工作区信任限制；GHCR 流水线尚未完成发布、拉取与启动验证。deep_dub、整片 RTF≤0.5 和 B 站发布仍待完成。[完整验收账本](docs/重构实施与验收.md)保留全部目标。

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
```

不需要业务 API、Worker 或前端服务，但仍需访问平台存储和登录服务。JSON 输出与非零错误码便于 AI 调用，支持 `create --input input.json`。HTTP 模式加 `--transport http`，命令和功能一致，需要启动业务 API/Worker。

`uv run anp migrate` 只迁移业务模块；平台内部表由平台初始化。CLI 自动读取私有 `.local/generated/platform-v3/business.env`，显式环境变量优先。本机登录信息在 `config/local-credentials.md`。

## F5 开发

VS Code 或兼容 Qoder 打开 `E:\anp` 或 `anp.code-workspace`，选择：

- **ANP V3: F5 业务（Docker 平台）**：Docker 四存储和单平台，本机 API、Worker、Vite。
- **ANP V3: F5 业务 + 平台（WSL 源码）**：Docker 四存储，WSL 运行平台源码，本机业务。

API/Worker 使用 debugpy 跟踪 Uvicorn 热重载子进程，前端使用 Vite HMR 与 Edge 调试；平台 Python 源码改动后优雅退出、保存 Agent 状态并重启，调试器追踪子进程。第三方编译制品修改后需要重新构建。

平台 URL 与 `.vscode/settings.json` 的 `anp.platformUrl` 要一致，WSL 路径映射 `/mnt/e/anp` 对应当前工作区。本机隔离环境使用 `http://localhost:8089`，业务端口 8181/8282/5174；具体由私有绑定决定。5684 是本机开发调试端口，不增加 Docker 发布端口。

首次原生运行从已构建镜像提取制品到受管理的 WSL `/opt/anp`；停止的临时导出容器随后删除，不运行嵌套 Docker。模式切换时先停止另一平台，再通过私有 Agent 归档交接，保留旧快照。

**验收边界：** 原生实跑 27 项检查通过，源码重载后既有线程保持一致；实际 IDE F5/断点尚未验收。VS Code 当前为 Restricted Mode，需要用户自行信任工作区。

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

复制业务：`uv run anp app new my_app --from demo`，随后检查权限、迁移与 flow，执行 `uv run anp migrate`。详见 [Agent 与业务开发](docs/Agent与业务开发.md)。
