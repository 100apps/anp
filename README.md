# ANP

ANP 将基础工具、存储、平台和业务示例打包成可搬移的目录。原生发行支持 **Mac ARM64、Linux x64**，解压运行不依赖 Docker、Homebrew、系统 Python 或在线安装。Intel Mac 与 Realtime 暂不包含。

## 原生包：下载运行

从 [GitHub Releases](https://github.com/100apps/anp/releases) 下载对应系统的压缩包及 SHA256 文件，核对摘要，解压后进入 `anp-macos-arm64/` 或 `anp-linux-amd64/`：

```sh
./anp verify
./anp start
./anp status
./anp stop
```

默认工作台：`http://127.0.0.1:58000/workbench/`。首次启动生成独立数据与随机凭据，保存在当前包的私有 `var/secrets/runtime.json`。业务管理员为 `admin@anp.local`，密码字段为 `business-user-password`；DBX 密码字段为 `dbx-password`。不要将该文件提交或发到聊天中。

包含 uv、Python、Node，PostgreSQL、Redis、ClickHouse、RustFS，Supabase Auth/Data API/Storage/Meta/Studio，Langfuse Web/Worker、LiteLLM、DBX，以及可独立复制的 `business/`。业务使用 uv、shadcn/ui 和 LangGraph 内置 Agent Server，提供 Supabase 登录与权限、Workflow 和 Deep Agent 两个离线模型演示；真实供应商需要另行配置。

```text
anp-macos-arm64/              # Linux 为 anp-linux-amd64/
├── anp                      # 启动、状态、日志、停止、清单验证
├── tools/                   # uv、Python、Node；Linux 另带 glibc 运行库
├── storage/                 # PostgreSQL、Redis、ClickHouse、RustFS
├── platform/                # Supabase、Langfuse、LiteLLM、DBX 等
├── business/                # 可复制的业务源码、配置示例与前端
├── runtime/                 # 启动管理与配置生成
├── defaults/                # 默认配置
├── config/                  # 用户配置
├── manifest/                # 来源、校验值、许可证
└── var/                     # 首次启动生成，不随压缩包发布
```

源码构建与验证：

```sh
git clone --branch master https://github.com/100apps/anp.git
cd anp
make build
make native-check
make dist
# 再对生成的真实压缩包执行 native/check.py；Linux 用 native/linux_check.py
```

构建与运行必须匹配本机系统和架构。Linux 使用独立 Debian 构建容器，详见 [Linux 构建](docs/development/linux-native-build.md)。`make build NATIVE_PROFILE=poc` 保留精简构建模式。更详细的配置、地址和管理命令见[原生包说明](native/README.md)；单独复制业务见 [Business](business/README.md)。

本机 Mac 完整目录与真实压缩包均通过 22 项检查，Business 各 24 项通过；Linux 在无外网的干净 Debian 与 Alpine 中分别通过目录与压缩包 23 项检查，Business 各 24 项通过。见[验收报告](docs/acceptance/native-0.3.0-progress.md)与[本地回执](docs/acceptance/native-0.3.0-local/)。这些结果不代表任意系统、其他 Mac 签名策略或真实模型供应商均已验证。Mac 尚未做 Apple notarization。

推送代码到 `master` 会触发[原生发行流水线](.github/workflows/native-release.yml)：分别构建两个架构、验收目录与真实解压包，再核对 GitHub 资产摘要后发布带版本的 Release。仅修改根 README 或 `docs/` 不重复构建；[流水线说明](docs/development/原生发行流水线.md)。

以下为原有 Docker 全功能开发环境，独立保留：

一个可插拔的 AI 应用脚手架：Debian slim 工具镜像内置存储和平台，业务代码在本地运行，LangGraph 内置 Agent Server 扩展提供统一HTTP入口，前端使用React、shadcn/ui和Vite。

## 快速开始

需要 Docker（Linux容器）、uv、Node.js 24、pnpm 11.25 和 Make。命令行快捷操作还需要 Python 3.11 或以上；Windows使用python，Linux/macOS使用python3。uv会按锁文件准备业务Python 3.12环境。

```sh
git clone https://github.com/100apps/anp.git
cd anp
make init
make pull
make up
make dev
```

首次使用发布镜像不需要检出平台源码。默认拉取 `ghcr.io/100apps/anp:latest`；正式部署可在根.env固定 `sha-<提交SHA>` 或镜像digest。发布镜像目标为linux/amd64与linux/arm64，Docker自动选择本机架构。

工作台：[http://127.0.0.1:51760/workbench/](http://127.0.0.1:51760/workbench/)。平台路径为 `/dbx/`、`/langfuse/`、`/litellm/`、`/supabase/`。业务API：[http://127.0.0.1:20260/docs](http://127.0.0.1:20260/docs)。默认离线模型可直接运行普通Workflow与Deep Agents工具调用演示。

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
