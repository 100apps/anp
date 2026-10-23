# ANP 原生目录发行包

目标为 macOS ARM64 和 Linux x64。Intel Mac 暂不发布。普通用户解压运行，运行时不依赖 Docker、Homebrew、系统 Python 或在线安装。Mac 首次构建需要 Xcode Command Line Tools、Python 3.12+、Git 和网络；Linux 使用 `native/linux/Dockerfile.builder` 的固定 Debian 构建环境。Rust、pnpm 等构建工具下载到本项目 var/native，不安装到系统。

```sh
make build                 # 默认平台 PoC，含 Supabase、Langfuse
make native-check          # 独立副本真实功能验收
make dist
python3 native/check.py --archive var/exports/anp-0.3.0-poc-macos-arm64.tar.gz
# 保留原来的精简构建模式
make build NATIVE_PROFILE=poc
```

构建目录是 `var/native/build/anp-macos-arm64/`。默认平台包为 `var/exports/anp-0.3.0-poc-macos-arm64.tar.gz`，附带 SHA256。旧的 `0.1.0-poc` 压缩包不会被覆盖。Docker 镜像仍使用 `make docker-build`，`make build-multi` 保持原功能。Linux 使用 `make build NATIVE_TARGET=linux-amd64`、`make dist NATIVE_TARGET=linux-amd64`，输出 `anp-linux-amd64/`。跨系统构建不受支持，需在对应原生系统执行。`make native-check NATIVE_TARGET=linux-amd64` 通过 Docker 在独立的 Debian 和 Alpine 容器验收目录；`python3 native/linux_check.py --archive <压缩包路径>` 验收解压包。容器禁用外网，不挂载业务或平台源码，也不提供宿主开发工具。

```text
anp-macos-arm64/
├── anp
├── tools/{uv,python,node}/
├── storage/{postgres,redis,clickhouse,rustfs}/
├── platform/
│   ├── postgrest/、litellm/、dbx/、business/
│   ├── auth/、storage-api/、meta/、studio/
│   ├── gateway/             # 官方 Caddy 原生包
│   └── langfuse/            # web/、worker/、prisma/
├── business/                 # 可独立复制的 uv + shadcn/ui 业务源码和界面
├── runtime/
├── defaults/anp.json
├── config/                  # 操作员配置
├── manifest/                # 来源、摘要、许可证、构建记录
└── var/                     # 首次运行生成，不进入压缩包
    ├── data/
    ├── logs/
    ├── run/
    ├── cache/
    └── secrets/runtime.json
```

Supabase 官方原生服务包保留自带 Node 和动态库目录；Langfuse 使用 tools/node。组件之间不依赖宿主 Node。Realtime、Edge Runtime、Logflare、Vector、imgproxy、Supavisor、PgBouncer 均未包含。DBX 和业务工作台已经纳入构建。

```sh
./anp verify
./anp start
./anp start --json          # 自动化调用：仅输出 JSON，不显示启动进度
./anp status
./anp config-path            # 只打印配置路径，不打印秘密
./anp stop
./anp stop -y               # 无需确认，停止占用本配置端口的进程
./anp start --services auth  # 自动补 PostgreSQL 依赖
./anp start --services langfuse-worker
ANP_OFFLINE=1 ./anp start    # 应用进程只可访问本机，禁止读取 Homebrew/Nix
```

`stop` 先停止本目录服务，再检查当前配置的主端口和附属端口。若仍有外来监听，会列出 PID、程序名和端口，并询问是否停止；回答 y/yes 后停止，拒绝或输入结束则保留占用者并以非零状态退出。`stop -y`（或 `stop --yes`）跳过确认，直接停止清单中的监听进程。先发送 SIGTERM，10 秒后仍占用原端口且 PID 身份不变才发送 SIGKILL；不会向外来进程的整个进程组发信号，也不会追杀自动重启后的新 PID。无法停止或端口仍占用会报错。仅检查当前配置端口；已覆盖的默认端口不属于检查范围。`--services` 可限定停止的服务及其依赖和附属端口；启动时仍拒绝外来端口占用，`-y` 仅用于 stop。

`start` 默认实时输出运行/配置/数据/日志目录、依赖启动顺序、端口预检查、每项服务的 PID 和就绪耗时；等待健康检查时约每 5 秒更新进度。已有健康服务显示“复用”，失败显示日志路径和本次启动服务的回滚进度。完成后显示服务表格、访问地址和管理命令。进度输出到 stderr 并立即刷新，结果表格输出到 stdout；`start --json` 改为仅输出 JSON，适合自动化调用。`status` 保留 JSON 输出格式。输出不包含运行环境、完整启动命令或密钥。

默认仅绑定 127.0.0.1。统一入口 `http://127.0.0.1:58000/` 为 Supabase Studio；Auth、Data API、Storage 分别是 `/auth/v1/`、`/rest/v1/`、`/storage/v1/`；业务工作台是 `/workbench/`，DBX 是 `/dbx/`，Langfuse 是 `/langfuse/`，LiteLLM 是 `/litellm/`。Studio 和 LiteLLM 管理界面会跳转到其配置的独立地址；DBX 经业务管理员鉴权再代理。业务端口默认 20260，DBX 默认 54424。所有内部端口见 defaults/anp.json，可复制到 config/anp.json 后在停止状态下修改。

首次启动生成独立数据库、服务角色、JWT/S3 密钥及 Langfuse 本地管理员。Langfuse 管理员邮箱为 admin@anp.local，随机密码和初始项目 API key 保存在私有 var/secrets/runtime.json。业务管理员邮箱同为 admin@anp.local，密码字段是 `business-user-password`；DBX 密码字段是 `dbx-password`。文件权限 0600；不要在聊天或日志中打印其内容。Auth 为本机开发启用邮箱自动确认；未配置外部邮件或第三方登录。Studio 管理入口仅供本机使用。LiteLLM 保持空模型配置，尚未接入真实供应商。

官方产物优先：Auth、PostgREST、ClickHouse、RustFS、Caddy 和 migrate 使用固定官方发行；Storage、Meta、Studio 使用 supabase/slim-services 的官方 Mac 归档。Langfuse 无相应原生发行包，从真实锁定 submodule 编译 Web、Worker 和 Darwin ARM64 Rust 扩展。Mac Redis 延续锁定源码编译；Linux Redis 使用官方 DEB，DBX 使用官方 Linux Web 压缩包。Mac DBX 官方没有对应 Web 原生包，因此从锁定源码编译。版本、URL、校验值及源码提交在 native/locks 固定；Python 使用 frozen uv.lock，JS 工具使用 package-lock.json，Langfuse 使用上游 frozen pnpm-lock.yaml/Cargo.lock。

Supabase 原生归档采用其已发布的版本，与 Docker 层的组件版本不要求相同；以 manifest/platform-sources.lock.json 为准。PostgreSQL 保留 Supabase 官方 CLI 运行文件，仅初始化所需角色和扩展，不承诺 Docker 版本的完整扩展集合。业务对象应经 Supabase Data API/RPC，文件经 Supabase Storage；不要让业务绕过平台直接连底层 PostgreSQL/RustFS。

`make dist` 按不可变清单校验后打包，不包含 var、用户配置或日志。存在运行数据时构建拒绝覆盖。升级前停止服务并备份 var/、config/；不要移动运行中的目录。macOS 签名使用本机 ad-hoc，不包含 Apple notarization；其他机器仍需实际验收，不能由本机结果推定。

## 可移植性与验收边界

Linux 包携带 glibc 加载器和动态库闭包，运行时按当前解压路径生成实例内的执行缓存。基本宿主要求为 x86_64 Linux 内核、可执行文件系统、POSIX `/bin/sh`、可用 `/proc` 和正常用户记录；无需安装 Python、Node、uv 或 Bash。Debian 与 Alpine 的干净容器是当前验收目标，不能将其描述为所有 Linux 内核和系统均已验证。

`business/` 的独立安装、配置和运行步骤见其 README。Python 依赖用 uv 冻结管理，前端用 pnpm；LangGraph 内置 Agent Server 提供 HTTP 扩展。默认 Workflow 和 Deep Agent 使用离线演示模型；真实供应商需要另行配置。业务对象通过 Supabase Data API/RPC，文件通过 Storage。

发布门禁包括独立目录、真实压缩包和干净环境的功能验收。Mac 在本机离线沙箱中验证，Linux 在 Debian 与 Alpine 的干净容器中验证；以当次回执及 GitHub Actions 实际结果为准。本机成功不代表其他 Mac 的签名策略与公证验收已通过。最新进展见 `docs/acceptance/native-0.3.0-progress.md`。

完整平台包的资源需求高于精简 PoC。本轮 Linux 实测 ClickHouse RSS 可达到约 3 GiB，初始 1 GiB 服务限制会使 Langfuse 写入/读取报错；现将服务上限设为 4 GiB，默认单查询限制仍为 512 MiB。上限不是预留内存，也不代表已在小内存机器上验收。
