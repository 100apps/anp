# Docker 开发环境

这是保留的容器开发交付，与原生目录包的构建和验收分开。普通原生用户不需要执行本文命令。

一个可插拔的 AI 应用脚手架：Debian slim 工具镜像内置存储和平台，业务代码在本地运行，LangGraph 内置 Agent Server 扩展提供统一HTTP入口，前端使用React、shadcn/ui和Vite。

## 快速开始

需要 Docker（Linux容器）、uv、Node.js 24、pnpm 11.25 和 Make。命令行快捷操作还需要 Python 3.11 或以上；Windows使用python，Linux/macOS使用python3。uv会按锁文件准备业务Python 3.12环境。

```sh
git clone --branch main https://github.com/100apps/anp.git
cd anp
make init
make pull
make up
make dev
```

首次使用发布镜像不需要检出平台源码。默认拉取 `ghcr.io/100apps/anp:latest`；正式部署可在根.env固定 `sha-<提交SHA>` 或镜像digest。发布镜像目标为linux/amd64与linux/arm64，Docker自动选择本机架构。

工作台：[http://127.0.0.1:51760/workbench/](http://127.0.0.1:51760/workbench/)。平台路径为 `/dbx/`、`/langfuse/`、`/litellm/`、`/supabase/`。业务API：[http://127.0.0.1:20260/docs](http://127.0.0.1:20260/docs)。默认离线模型可直接运行普通Workflow与Deep Agents工具调用演示。

## 平台控制台与 OAuth

工作台侧栏提供独立的[平台与存储控制台](docs/development/平台控制台与OAuth.md)：查看运行状态、启停可管理服务、在线修改运行配置与 HTTP/WebSocket 上游。底层服务保持 `127.0.0.1`，局域网入口只能显式开放已鉴权的 Agent Server。控制台依赖服务需在终端维护；Supabase Studio 与 LiteLLM UI 当前仍使用独立本机地址。

本轮本机结果见[控制台与 OAuth 验收报告](docs/acceptance/平台控制台-Mac验收-2026-10-25.md)。

RustFS 存储卡片提供 `/rustfs/console/` 后台入口和密钥查看，S3 签名请求同样经过管理员鉴权代理。DBX 免二次登录，Grafana 使用官方代理认证。Langfuse 和 LiteLLM 保留自身会话，管理员可在控制台主动查看初始化用户名密码。OAuth/OIDC 默认留空关闭；可配置公司 OIDC 或 Google，身份通过 Supabase 建立会话，平台管理员按明确的 subject 清单授权。

## 开发与F5

VSCode/Qoder打开仓库，安装推荐的Python和debugpy扩展，选择 **ANP: 一键前后端热重载**，按F5。准备任务先停止已登记的本工作区开发进程，便于从make dev切换，再安装锁定依赖、启动工具容器、Vite和带debugpy的LangGraph后端；后端支持子进程断点和热重载。前端JavaScript断点可选择另一个需Edge的联合调试配置。

停止调试仅结束本工作区登记的开发进程，工具容器继续运行。默认业务20260、前端51760、调试56796；初次准备依赖和平台迁移可能需要数分钟。

## 快捷命令

| 命令 | 用途 |
| --- | --- |
| make doctor / make context | 工具检查 / 不含凭据的Agent入口JSON |
| make pull / make up | 拉取发布镜像 / 启动全部服务 |
| make up SERVICES=storage | 仅四存储 |
| make up SERVICES=redis,dbx | 指定多个服务 |
| make up SERVICES=langfuse | Langfuse及依赖；supabase同理 |
| make status / make logs | 状态 / 容器管理日志 |
| make restart / make down | 重启 / 停止工具容器，保留数据 |
| make dev / make stop | 前后端热重载 / 停止本项目开发进程 |
| make test | 两Demo、代理与运行时契约测试及前端生产构建 |
| make smoke / make platform-operations | 四存储和两图 / 平台真实API操作 |
| make realtime-check | Supabase 对象 CRUD/RLS、实时订阅、广播与在线状态 |
| make acceptance / make debug-check | 镜像完整回归 / 断点与热重载协议 |
| make image-size / make image-export | 镜像体积 / 导出Zstd分发包 |

业务演示只使用离线模型，不调用付费API。业务包含 Workflow 与 Deep Agent 两个演示。

## 自行构建与定制平台

```sh
make sources
make docker-build
# 运行自行构建的镜像：根.env设 ANP_IMAGE=anp/toolbox:local
make up

# 双架构发布到自己有权限的仓库，需先docker login：
make build-multi IMAGE=ghcr.io/你的账号/anp:dev
```

只有 `docker/toolbox/Dockerfile` 是镜像构建入口。优先使用官方二进制和已发布代码包，固定 URL、SHA256 或包管理器完整性校验；必须编译的组件在同一个Dockerfile的阶段内完成。PostgreSQL使用锁定Supabase源码和官方Nix缓存，缓存缺失时构建同一版本及扩展，不依赖本机已有组件镜像。源码位于真实submodule，版本与提交在 `docker/toolbox/versions.lock.json`。

首次全量源码构建需要较多时间、内存和磁盘。推荐双架构分别使用原生机器构建；本地QEMU仿真可以使用，但编译明显更慢。CI采用各架构原生runner，候选镜像分别通过启动验收后才合并发布latest。

## 配置与持久化

根.env控制镜像、服务、端口、资源和数据根；business/.env控制业务；容器 `/data/config/` 存放平台配置、凭据与生效参数。资源基线8核16GiB，首次生成配置的CPU不超过Docker实际可用核心数。配置和数据通过一个独立目录映射到 `/data`。

`/data/run` 使用容器内的 tmpfs 保存临时运行状态与 Unix socket，重建时重新生成，避免 macOS 共享目录的 socket 权限限制。数据库、配置和媒体仍写入独立持久目录。

Windows Docker Desktop 默认使用Linux主机目录 `/var/lib/anp-next/data`，避免ClickHouse在NTFS上的重命名兼容问题；它不是Windows本机文件夹，迁移机器需单独备份。当前开发机为兼容既有数据保留 `/var/lib/anp3/data`，与产品名称无关。Docker实例默认 `anp-next`，避免覆盖同机旧版ANP。

根.env、business/.env和credentials.json均不提交。已有数据根的凭据不会因修改环境变量而隐式轮换。详细配置见[配置参考](docs/development/配置参考.md)。

更多：[架构与Agent指南](docs/architecture/架构与Agent指南.md)、[版本与参数](docs/development/版本与运行参数.md)、[平台代理](docs/architecture/平台统一入口.md)、[本地验收](docs/acceptance/本地验收.md)、[镜像体积](docs/development/镜像体积优化.md)、[GitHub构建](docs/development/GitHub构建.md)。

完整文档见[文档导航](docs/README.md)，业务范围见[业务演示](docs/business/业务演示.md)。新验收回执写入 `var/evidence/`，固定验收输入位于 `tests/baselines/`。
