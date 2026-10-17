# ANP 本地 AI 原生工作台

默认入口是本机 CLI，业务服务无需启动。Web、HTTP CLI 和本地 CLI 共用业务服务、输入校验、身份和权限。平台依赖全部运行在 Docker，不使用 Windows PostgreSQL。

## 开始使用

本机需要 Docker Desktop（Linux containers）、uv、Node.js 22+、pnpm 11。首次安装：

```powershell
cd E:\anp
.\scripts\bootstrap.ps1
```

本机已安装 pnpm 11.25.0；新终端可直接使用。如果新机器尚未安装：`npm install --global pnpm@11.25.0`。

管理员凭据在 [本机登录信息](config/local-credentials.md)，不要提交到版本库。默认管理员邮箱 `admin@anp.local`，密码随机生成且重启不会变化。

```powershell
uv run anp auth login --email admin@anp.local
uv run anp demo tasks create --title "我的任务" --text "Hello AI。你好，工作台。"
uv run anp demo tasks run <任务ID>
uv run anp demo tasks get <任务ID>
uv run anp demo tasks result <任务ID>
```

这些命令默认直接执行本机业务代码，只需要 Docker 平台。CLI 统一输出 JSON，错误返回非零退出码，适合 AI Agent 调用；输入也可使用 `create --input input.json`。

HTTP 模式使用完全相同的命令：

```powershell
uv run anp --transport http demo tasks create --text "通过 HTTP 创建"
uv run anp --transport http demo tasks run <任务ID>
```

HTTP 执行由 Agent Server 编排、Worker 执行节点，`run` 默认等待完成；`--no-wait` 立即返回，再用 `get` 查询。HTTP 模式和 Web 需要 F5 启动业务进程。

## F5 开发

使用 Qoder CN IDE / VS Code 打开 **[anp.code-workspace](anp.code-workspace)**，选择 `ANP: F5 完整开发（热更新 + 调试）` 后按 F5。

它准备 Compose 平台、迁移数据库，启动 Vite，然后同时调试 API、Worker、Docker 内的 Agent 图和 Edge 前端。默认不启动管理后台、DBX 和 LiteLLM，也不等待 DBX。前端 Fast Refresh；Python 修改自动重启子进程，调试器自动跟踪。源码断点可设在 `apps/demo/nodes/analyze.py`、`src/anp/server/api.py`、`apps/demo/flow/graph.py` 和 `.tsx` 页面中。停止组合调试不会删除 Docker 数据。

## 管理入口

| 服务 | 地址 | 登录 |
|---|---|---|
| 应用工作台 | http://localhost:5173 | Supabase 业务账号 |
| 业务 API | http://localhost:8180/docs | Bearer 用户会话 |
| DBX | http://localhost:4224 | 本机凭据文件中的 DBX 密码 |
| Supabase Studio | http://localhost:8000 | 本机凭据文件中的 Studio 账号 |
| RustFS | http://localhost:9001 | 本机凭据文件中的 RustFS 账号 |
| Langfuse | http://localhost:3000 | 管理员邮箱及初始密码 |
| LiteLLM | http://localhost:4000/ui | 管理密钥见私有 env 的 LITELLM_KEY |
| Agent Server | http://localhost:8124/docs | 私有 ENGINE_TOKEN |

工作台“平台连接”提供上述入口和官方 Studio 链接。Studio 是连接本地图的外部开发工具，账号独立；核心 CLI、Web、追踪不依赖 Studio 登录。

Studio 首次连接还需要在 Custom Headers 中配置本机引擎的 `Authorization`。完整值见本机登录信息的 Studio 小节，操作步骤见 [Studio 连接说明](docs/studio.md)。

## 项目结构

```text
E:\anp\
├─ apps/demo/                一个完整可复制业务
│  ├─ app.toml               注册、菜单、版本、权限和入口声明
│  ├─ ui/index.tsx           工作台页面
│  ├─ api/router.py          HTTP 路由
│  ├─ services/tasks.py      本地 CLI / API 共用业务服务
│  ├─ nodes/                 normalize / analyze / save
│  ├─ flow/                  state / graph / deployment
│  └─ migrations/            业务结果表与 RLS
├─ console/                  React + Vite + shadcn/ui 工作台外壳
├─ src/anp/
│  ├─ cli/                   本地 / HTTP CLI
│  ├─ auth/                  Supabase 身份、会话和 ACL
│  ├─ applications/          模块发现、复制与注册生成
│  ├─ tasks/                 任务管理服务和公共路由
│  ├─ execution/             本地/平台执行适配、幂等与协调
│  ├─ runtime/               图节点到 Worker 的 API 适配
│  ├─ sdk/                   对象、文件、追踪连接
│  ├─ server/                API 和 Worker 宿主
│  ├─ management/            Compose、初始化、迁移、DBX 配置
│  └─ dev/                   F5 准备和热重载入口
├─ platform/
│  ├─ compose.yaml           一个 Compose 启动入口
│  ├─ compose.generated.yaml 由配置生成，含密钥，不提交
│  ├─ compose.debug.yaml     开发挂载和 Agent 调试端口
│  ├─ images.lock.json       外部镜像 digest 锁定
│  ├─ upstream.lock.toml     上游配置来源
│  ├─ config/                Supabase / Langfuse / LiteLLM / DBX / Agent
│  ├─ docker/                公共图运行镜像构建文件
│  └─ init/                  对象存储初始化
├─ config/                   连接配置模板及本机私有配置
├─ migrations/               平台权限和任务表
├─ scripts/                  安装、启动、停止
├─ tests/                    真实服务集成验收
├─ docs/                     开发说明、架构、验收和截图
├─ .vscode/                  F5、任务和推荐扩展
├─ pyproject.toml + uv.lock
└─ package.json + pnpm-lock.yaml
```

## 日常运维

```powershell
uv run anp platform up       # 构建、启动业务与追踪依赖、迁移、初始化
uv run anp platform up --admin # 额外启动 Studio、meta、DBX，并预配置 DBX
uv run anp platform up --llm # 额外启动 LiteLLM；可与 --admin 合用
uv run anp platform check    # 启动管理工具后验证六个 DBX 连接
uv run anp platform down     # 停容器，保留全部数据卷
```

管理工具属于 Compose 的 `admin` profile，LiteLLM 属于 `llm` profile，两组均关闭自动重启。默认启动和 F5 不会主动启动它们；已经手动启动的可选服务会继续运行，直到显式停止。在 `platform` 目录可单独操作：

```powershell
docker compose --profile admin up -d studio meta dbx
docker compose --profile llm up -d litellm
docker compose --profile admin stop studio meta dbx
docker compose --profile llm stop litellm
```

停止容器不会删除数据。完整集成验收 `scripts/check.ps1` 会显式启动管理工具和 LiteLLM。

也可在 `platform` 目录运行 `docker compose up -d`；首次业务账号/表初始化使用 `anp platform up`。业务模块或依赖变化时重新构建图镜像。第三方服务使用锁定的镜像和必要配置，不依赖 submodule。

详见 [架构与数据库](docs/architecture.md)、[新增业务](docs/new-business.md)、[云资源绑定](docs/cloud-resources.md)、[验收记录](docs/acceptance.md)。
