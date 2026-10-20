# ANP2：可插拔 AI 业务工作台

三层职责、组件独立容器。项目固定使用 **anp2-next**，与现有 anp/anp2 服务隔离。

已跑通四存储、两个 demo、本机与容器 HTTP/流式执行，以及 Qoder 前后端断点和热重载。真实模型视频译配分别通过 CLI、浏览器和容器短片贯通，见[视频译配工作流](docs/视频译配工作流.md)。四个平台的源码构建、统一入口和关键 API 已验收，见[平台统一入口](docs/平台统一入口.md)；正式 Agent Server 授权运行及持久化仍需单独验收。

GitHub 使用本版本的测试和独立镜像构建流程，说明见[GitHub 构建](docs/GitHub构建.md)。

## 首次使用

需要 Docker Desktop、uv/Python 3.12、Node.js 24、pnpm 11.25、GNU Make。Windows 克隆时启用长路径：

```powershell
git -c core.longpaths=true clone --recurse-submodules <本仓库地址>
cd anp2
git config core.longpaths true
make init
make install
make storage
make dev
```

源码已存在时，用 `git -c core.longpaths=true submodule update --init --depth 1` 获取锁定的依赖。普通启动不更新源码。

开发工作台：[http://127.0.0.1:51730/workbench/](http://127.0.0.1:51730/workbench/)。后端默认 20240；调试端口 56790。首次无密钥运行使用明确标注的确定性演示模型。

## 快捷命令

| 命令 | 用途 |
| --- | --- |
| make init | 创建缺失的私有配置和随机密码，保留已有值 |
| make install | 按 uv/pnpm 锁文件安装业务开发依赖 |
| make storage | 启动本项目四存储及必要的一次性权限初始化 |
| make dev | 准备依赖与所选服务，前台启动 Python reload 和 Vite HMR |
| make stop | 只停止本项目登记的开发进程，保留容器及数据 |
| make build | 构建业务演示镜像，含业务 wheel 与生产前端 dist |
| make up | 启动所选服务及业务演示容器，工作台端口 52024 |
| make down | 停止本项目容器；不删除绑定数据文件夹 |
| make ps / make logs | 查看本项目状态/日志 |
| make config | 校验 Compose，不把展开后的密钥打印到终端 |
| make check | 只读检查四存储及旧环境保护基线 |
| make test | 业务单元测试与前端生产构建 |
| make smoke | 对本机后端跑四存储读写及两个 HTTP/流式 demo |
| make smoke-docker | 对演示容器执行同类检查 |
| make test-integration | 写入探针、重启本项目四存储、读回探针并运行本机 demo |
| make bootstrap | 显式初始化平台服务数据库、角色密码和 S3 桶 |
| make build-platform COMPONENT=langfuse-web | 仅构建指定平台服务；不指定 COMPONENT 则构建所选平台 |
| make platform | 启动已构建的平台服务，不隐式编译 |
| make platform-check | 验收四个平台真实页面、静态资源及关键 API |
| make build-production | 构建官方 Agent Server 生产运行目标 |
| make up-production | 校验正式运行配置并启动；需要官方授权及数据库配置 |

`make test-integration` 只重启 anp2-next 四存储，会创建 anp2_smoke schema/库、Redis 探针及 anp2-smoke 桶。不要用于生产数据验收。CLI 默认不会删除这些探针。

普通 `make up` 是可运行的演示环境，采用开发运行器保存演示状态；不将它当作生产持久化方案。生产目标在同一个业务 Dockerfile 中，依赖官方 Agent Server 入口、PG/Redis 和相应授权。

## Qoder / VS Code 一键调试

打开 E:/anp2，在“运行和调试”选择 **ANP2: 一键前后端热重载**，按 F5。

前置任务安装锁定依赖、启动所选本地服务、启动前端 HMR 和原生 LangGraph 调试服务；Python 附加到 56790，Edge 调试工作台。已配置 /workbench/ 源码路径映射、Python 子进程调试和停止收尾。

- Python 断点示例：business/src/anp_business/workflow_demo/nodes.py。
- 前端断点示例：business/ui/src/app.tsx 的 run 函数。
- 停止整组调试会停止本项目的前后端开发进程；容器与数据保留。
- 普通 make dev 保留 LangGraph 阻塞检查。显式调试模式使用官方 --allow-blocking，因为 Windows debugpy 的源码路径探测会触发阻塞检测。
- IDE 的工作区信任由用户决定，不自动修改安全设置。
- 开发日志写入 `var/logs/dev/`，避免后台终端缓冲区写满后卡住热重载；需要持续输出到交互终端时设置 `ANP_DEV_LOG_TO_TERMINAL=1`。

## 版本与文件夹

版本以 [兼容性记录](docker/compatibility.yaml)、[镜像锁定](docker/versions.env)、uv.lock、pnpm-lock.yaml 和真实 submodule SHA 为准，不使用 latest 自动升级。

Windows 默认数据根目录为 **Docker Linux 主机文件系统上的 /var/lib/anp2-next/data**，是绑定文件夹，不是命名数据卷。配置位于本项目 var/config。首次验证发现 NTFS 不能满足 ClickHouse MergeTree 目录重命名语义，因此没有使用 Windows 文件夹保存数据库数据。

Linux 可设置 ANP_DATA_ROOT 为独立宿主文件夹。变更数据根目录意味着连接另一套数据，应显式迁移；不会自动搬运或删除旧目录。不要运行 docker system prune，也不要复用旧 anp 的卷或路径。

宿主端口：PG 55432、Redis 56379、ClickHouse 58123/59000、RustFS 59002/59003；均绑定回环地址。默认没有登录鉴权的业务 demo 也只发布到回环地址。

## 切换外部资源

从 .env 的 COMPOSE_PROFILES 移除 local-pg，然后修改 var/config/services 下消费者的连接配置。其他存储也可以分别关闭 profile 并修改对应连接。

Supabase 有两种模式：本地 Auth/REST 等服务连接外部 PG；或业务直接连接已有 Supabase API，关闭整组本地 Supabase。外部 PG 必须满足启用能力所需的版本、角色、扩展、TLS 和迁移权限。bootstrap 不会随 up 自动执行。

配置样例在 config/profiles。模型配置位于本机 business/.env 或容器 var/config/services/business.env：

```dotenv
ANP_MODEL_MODE=openai
ANP_MODEL=你的模型名称
ANP_MODEL_BASE_URL=https://你的OpenAI兼容网关/v1
ANP_MODEL_API_KEY=你的密钥
```

密钥不进入前端和镜像。两个通用 demo 使用标明的离线演示模型；真实供应商调用的验证范围见视频译配工作流记录。

平台更新流程：显式更新某个 submodule → 检查并同步该组件 Dockerfile → 构建对应服务 → 备份并执行必要迁移 → 仅重建该组件。Langfuse web/worker 使用同一源码版本成组升级，Compose 拓扑不变。

工作台侧栏集成 `/dbx/`、`/langfuse/`、`/litellm/`、`/supabase/` 同源入口，开发与容器环境使用同一组路径。路径编译、外部平台配置和验收方式见[平台统一入口](docs/平台统一入口.md)。
## 视频翻译配音

已新增对象驱动的译配业务，入口为工作台的“视频译配”。运行数据保存到本项目 Supabase PostgreSQL，媒体保存到私有 RustFS。

```powershell
make dub-config
make dub-migrate
make dev
# 或：make dub-run INPUT="E:/anp2/var/dubbing/inputs/原片.mp4"
```

详细对象关系、模型接入、容器计算端、恢复与验收边界见[视频译配工作流](docs/视频译配工作流.md)。本轮跑通真实短片技术链路；成片状态为“待视听复核”，未声称长片、多角色、音色/自然度或最终专业质量通过。
