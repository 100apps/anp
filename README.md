# ANP

一个可插拔的 AI 应用脚手架：Debian slim 工具镜像内置存储和平台，业务代码在本地运行，LangGraph 内置 Agent Server 扩展提供统一HTTP入口，前端使用React、shadcn/ui和Vite。

## 快速开始

需要 Docker（Linux容器）、uv、Node.js 24、pnpm 11.25 和 Make。命令行快捷操作还需要 Python 3；Windows使用python，Linux/macOS使用python3。uv会按锁文件准备业务Python 3.12环境。

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
| make acceptance / make debug-check | 镜像完整回归 / 断点与热重载协议 |
| make image-size / make image-export | 镜像体积 / 导出Zstd分发包 |

业务演示只使用离线模型，不调用付费API。原视频译配对象与源码作为可选业务保留，可通过make dub-config、make dub-migrate等入口接入；本轮业务验收范围是两个Demo。

## 自行构建与定制平台

```sh
make sources
make build
# 运行自行构建的镜像：根.env设 ANP_IMAGE=anp/toolbox:local
make up

# 双架构发布到自己有权限的仓库，需先docker login：
make build-multi IMAGE=ghcr.io/你的账号/anp:dev
```

只有 `docker/toolbox/Dockerfile` 是镜像构建入口。官方二进制使用固定URL与SHA256；必须编译的组件在同一个Dockerfile的阶段内完成。PostgreSQL使用锁定Supabase源码和官方Nix缓存，缓存缺失时构建同一版本及扩展，不依赖本机已有组件镜像。源码位于真实submodule，版本与提交在 `docker/toolbox/versions.lock.json`。

首次全量源码构建需要较多时间、内存和磁盘。推荐双架构分别使用原生机器构建；本地QEMU仿真可以使用，但编译明显更慢。CI采用各架构原生runner，候选镜像分别通过启动验收后才合并发布latest。

## 配置与持久化

根.env控制镜像、服务、端口、资源和数据根；business/.env控制业务；容器 `/data/config/` 存放平台配置、凭据与生效参数。资源基线8核16GiB，首次生成配置的CPU不超过Docker实际可用核心数。配置和数据通过一个独立目录映射到 `/data`。

Windows Docker Desktop 默认使用Linux主机目录 `/var/lib/anp-next/data`，避免ClickHouse在NTFS上的重命名兼容问题；它不是Windows本机文件夹，迁移机器需单独备份。当前开发机为兼容既有数据保留 `/var/lib/anp3/data`，与产品名称无关。Docker实例默认 `anp-next`，避免覆盖同机旧版ANP。

根.env、business/.env和credentials.json均不提交。已有数据根的凭据不会因修改环境变量而隐式轮换。详细配置见[配置参考](docs/配置参考.md)。

更多：[架构与Agent指南](docs/架构与Agent指南.md)、[版本与参数](docs/版本与运行参数.md)、[平台代理](docs/平台统一入口.md)、[本地验收](docs/本地验收.md)、[镜像体积](docs/镜像体积优化.md)、[GitHub构建](docs/GitHub构建.md)。
