# ANP3：统一工具镜像与本地业务

存储和平台组成一个 `anp3/toolbox:local` 镜像，运行时只有一个工具容器。业务 Python 和工作台在宿主运行，LangGraph 内置 Agent Server 的 HTTP 扩展提供平台路径代理。默认资源基线为 8 核、16GB。

## 启动

需要 Docker、Python、uv、Node 24、pnpm 11.25 和 Make。平台是真实 submodule，Windows 初始化时执行 `git -c core.longpaths=true submodule update --init --recursive`，避免上游源码长路径问题。已构建好的本机工作区可直接启动：

```sh
make init
make up
make dev
```

工作台：http://127.0.0.1:51760/workbench/ 。平台入口：`/dbx/`、`/langfuse/`、`/litellm/`、`/supabase/`。业务 API：http://127.0.0.1:20260/docs 。业务默认使用明确标注的离线演示模型，包含 Workflow、Deep Agents 和保留的译配工作流。

首次从源码构建：

```sh
make install
make build-components
python scripts/lock_versions.py --require-images
make build
make up
```

平台按上游生产 Dockerfile 多阶段编译；各组件镜像只是组装输入，运行无需启动这些独立容器。首次源码构建较慢、占用较多磁盘。工具镜像保留全部14个服务、完整 PostgreSQL 扩展和平台前端。经过运行文件清理、依赖去重和 Zstd 压缩，Docker 本地占用约8.72GB，分发内容约1.42GB；两者不是同一个指标。详见[镜像体积优化](docs/镜像体积优化.md)。

## 常用命令

| 命令 | 用途 |
| --- | --- |
| `make up SERVICES=redis` | 同一镜像只启动 Redis |
| `make up SERVICES=storage` | 启动四存储 |
| `make up SERVICES=langfuse` | 启动 Langfuse 及依赖 |
| `make up` | 启动全部14个服务进程 |
| `make restart` / `make down` | 重启 / 停止本项目工具容器，保留数据 |
| `make dev` / `make stop` | 本地前后端热重载 / 停止本项目开发进程 |
| `make status` / `make logs` | 查看服务状态 / 管理日志 |
| `make check` / `make smoke` | 四存储健康 / 真实读写与两业务图 |
| `make platform-check` | 平台页面、JS/CSS和API |
| `make test` | 业务、代理、运行时契约测试和前端编译 |
| `make acceptance` | 真实重启、单选恢复、存储读回及平台端到端验收 |
| `make image-size` / `make image-export` | 查看本地与分发体积 / 导出 Zstd 镜像包 |
| `make platform-operations` / `make debug-check` | 平台真实操作 / 断点与热重载协议验收 |
| `make dub-migrate` | 新 Supabase 对象、关系和 RLS 初始化 |

`make acceptance` 会重建或重启 **anp3** 工具容器，业务开发环境需先启动；不会操作相邻项目。真实数据库权限测试另设 `ANP_DUB_TEST_DATABASE=1`，默认跳过并明确报告。

## 数据与调试

唯一容器映射是 `${ANP_DATA_ROOT}:/data`。Windows Docker Desktop 默认将其设为 Linux 主机目录 `/var/lib/anp3/data`，避免 NTFS 不满足 ClickHouse 重命名语义。它不是 Windows 的 `E:/anp3/var/data`，迁移机器必须另外备份该目录。Linux 可显式设为 `/srv/anp3/data`。配置、密钥、各服务数据和轮转日志均在此目录下。

根 `.env` 和 `business/.env` 仅本机私有；容器首次生成 `/data/config/credentials.json`，持久化后拒绝以不同环境密码覆盖。查看管理员凭据：在自己终端执行 `docker compose -p anp3 exec toolbox cat /data/config/credentials.json`，不要分享输出。LiteLLM 的模型列表初始为空，通过 `/data/config/litellm.yaml` 配置供应商和模型，再重启工具容器。业务真实模型另外在 `business/.env` 配置。

VSCode/Qoder 打开本目录，选择 **ANP3: 一键前后端热重载** 后按 F5。准备任务安装锁定依赖、等待工具服务、迁移新库，随后启动 Vite 和带 debugpy 的 LangGraph 热重载后端；调试端口 56796。停止调试会停止本项目开发进程，保留工具容器。Windows开发后端使用独立隐藏控制台；Uvicorn重载退出最多等待8秒，再回收本开发子进程，避免后台调试信号导致无限等待。该适配不改变内置HTTP服务。

更多：[目录与启动](docs/三层目录与启动方案.md)、[版本与参数](docs/版本与运行参数.md)、[平台代理](docs/平台统一入口.md)、[本地验收](docs/本地验收.md)、[译配对象模型](docs/视频译配工作流.md)。
