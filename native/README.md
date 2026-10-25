# ANP 原生目录发行包

目标为 macOS ARM64 和 Linux x64。Intel Mac 暂不发布。普通用户解压运行，运行时不依赖 Docker、Homebrew、系统 Python 或在线安装。Mac 首次构建需要 Xcode Command Line Tools、Python 3.12+、Git 和网络；Linux 使用 `native/linux/Dockerfile.builder` 的固定 Debian 构建环境。Rust、pnpm 等构建工具下载到本项目 var/native，不安装到系统。

```sh
make build                 # 默认平台 PoC，含 Supabase、Langfuse
make native-check          # 独立副本真实功能验收
make dist
python3 native/check.py --archive var/exports/anp-<版本>-macos-arm64.tar.gz
# 保留原来的精简构建模式
make build NATIVE_PROFILE=poc
```

构建目录是 `var/native/build/anp-macos-arm64/`。平台包为 `var/exports/anp-<版本>-macos-arm64.tar.gz`，附带 SHA256。旧的 `0.1.0-poc` 压缩包不会被覆盖。Docker 镜像仍使用 `make docker-build`，`make build-multi` 保持原功能。Linux 使用 `make build NATIVE_TARGET=linux-amd64`、`make dist NATIVE_TARGET=linux-amd64`，输出 `anp-linux-amd64/`。跨系统构建不受支持，需在对应原生系统执行。`make native-check NATIVE_TARGET=linux-amd64` 通过 Docker 在独立的 Debian 和 Alpine 容器验收目录；`python3 native/linux_check.py --archive <压缩包路径>` 验收解压包。容器禁用外网，不挂载业务或平台源码，也不提供宿主开发工具。

```text
anp-macos-arm64/
├── anp
├── tools/{uv,python,node}/
├── storage/{postgres,redis,clickhouse,rustfs}/
├── platform/
│   ├── otel/                # 官方 Collector、Grafana、Prometheus、Loki、Tempo
│   ├── postgrest/、litellm/、dbx/、business/
│   ├── auth/、storage-api/、meta/、studio/
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

Supabase 官方原生服务包保留自带 Node 和动态库目录；Langfuse 使用 tools/node。组件之间不依赖宿主 Node。Realtime、Edge Runtime、Logflare、Vector、imgproxy、Supavisor、PgBouncer 均未包含。DBX 和业务工作台已经纳入构建。完整平台包新增五组件 OTel/LGTM 可选模块，默认不启动，Grafana 使用存储层 PostgreSQL，不使用 SQLite；详见 [观测模块说明](defaults/otel/README.md)。Pyroscope、eBPF 不包含。本轮只接入源码，未构建或验收。

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

默认仅绑定 127.0.0.1。统一入口 `http://127.0.0.1:58000/` 进入工作台；Supabase Studio 是 `/supabase`（同源 `/project/default`）。Auth、Data API、Storage 分别是 `/auth/v1/`、`/rest/v1/`、`/storage/v1/`；业务工作台是 `/workbench/`，DBX 是 `/dbx/`，Langfuse 是 `/langfuse/`，LiteLLM 是 `/litellm/`。Agent Server 直接监听统一端口，不再保留独立的 20260 业务监听或 Caddy 进程。DBX 内部端口默认 54424。所有内部端口见 defaults/anp.json，可复制到 config/anp.json 后在停止状态下修改。

首次启动生成独立数据库、服务角色、JWT/S3 密钥及 Langfuse 本地管理员。Langfuse 管理员邮箱为 admin@anp.local，随机密码和初始项目 API key 保存在私有 var/secrets/runtime.json。业务管理员邮箱同为 admin@anp.local，密码字段是 `business-user-password`；当前候选 DBX 经统一管理员鉴权，无需二次密码；历史包的 DBX 密码字段是 `dbx-password`。文件权限 0600；不要在聊天或日志中打印其内容。Auth 为本机开发启用邮箱自动确认；未配置外部邮件或第三方登录。Studio 管理入口仅供本机使用。LiteLLM 保持空模型配置，尚未接入真实供应商。

官方产物优先：Auth、PostgREST、ClickHouse、RustFS 和 migrate 使用固定官方发行；Storage、Meta、Studio 使用 supabase/slim-services 的官方 Mac 归档。Langfuse 无相应原生发行包，从真实锁定 submodule 编译 Web、Worker 和 Darwin ARM64 Rust 扩展。Mac Redis 使用 Redis 官方 ZIP，保留 Redis Labs Developer ID 签名与二进制原字节，通过仅作用于 Redis 进程的启动器加载随包 OpenSSL，不再编译或改签；Linux Redis 使用官方 DEB，DBX 使用官方 Linux Web 压缩包。Mac DBX 官方没有对应 Web 原生包，因此从锁定源码编译。OTel/LGTM 五个组件均下载官方预构建包。版本、URL、校验值及源码提交在 native/locks 固定；Python 使用 frozen uv.lock，JS 工具使用 package-lock.json，Langfuse 使用上游 frozen pnpm-lock.yaml/Cargo.lock。

Supabase 原生归档采用其已发布的版本，与 Docker 层的组件版本不要求相同；以 manifest/platform-sources.lock.json 为准。PostgreSQL 保留 Supabase 官方 CLI 运行文件，仅初始化所需角色和扩展，不承诺 Docker 版本的完整扩展集合。业务对象应经 Supabase Data API/RPC，文件经 Supabase Storage；不要让业务绕过平台直接连底层 PostgreSQL/RustFS。

`make dist` 按不可变清单校验后打包，不包含 var、用户配置或日志。存在运行数据时构建拒绝覆盖。升级前停止服务并备份 var/、config/；不要移动运行中的目录。macOS 保留有效的上游供应商签名；自编译组件及必须修改的 Mach-O 使用 ad-hoc，不包含整包 Apple notarization。Redis 不修改 Mach-O、不重新签名。其他机器仍需实际验收，不能由本机结果推定。

## 可移植性与验收边界

Linux 包携带 glibc 加载器和动态库闭包，运行时按当前解压路径生成实例内的执行缓存。基本宿主要求为 x86_64 Linux 内核、可执行文件系统、POSIX `/bin/sh`、可用 `/proc` 和正常用户记录；无需安装 Python、Node、uv 或 Bash。Debian 与 Alpine 的干净容器是当前验收目标，不能将其描述为所有 Linux 内核和系统均已验证。

`business/` 的独立安装、配置和运行步骤见其 README。Python 依赖用 uv 冻结管理，前端用 pnpm；LangGraph 内置 Agent Server 提供 HTTP 扩展。默认 Workflow 和 Deep Agent 使用离线演示模型；真实供应商需要另行配置。业务对象通过 Supabase Data API/RPC，文件通过 Storage。

发布门禁包括独立目录、真实压缩包和干净环境的功能验收。Mac 在本机离线沙箱中验证，Linux 在 Debian 与 Alpine 的干净容器中验证；以当次回执及 GitHub Actions 实际结果为准。本机成功不代表其他 Mac 的签名策略与公证验收已通过。以仓库 `docs/acceptance/` 下对应版本的报告为准；CI 回执与实体机附件验收分别记录。

完整平台包的资源需求高于精简 PoC。ClickHouse 服务上限为 4 GiB，默认单查询限制为 512 MiB；上限不是预留内存，也不代表已在小内存机器上验收。当前源码设置 `memory_worker_use_cgroup=0`：多服务共享 cgroup 时按 ClickHouse 自身分配器统计，避免将其他服务与缓存的总用量误算为 ClickHouse 的内存。在 Alibaba Cloud Linux 目标机曾出现进程 RSS 约 661 MiB、共享 cgroup 用量超过 4 GiB，导致 Langfuse 返回 422；修改后同一追踪写入与 worker 读回探针通过，完整验收以当次报告为准。


## 外部资源模式

`0.4.16` 原生包支持复用 PostgreSQL、Redis、ClickHouse 和 S3 云资源，不启动包内四个存储服务。包内仍保留存储二进制，方便使用默认内置模式；实际启动范围由配置决定。

将 `defaults/external-resources.example.json` 复制到包外的私有 JSON 文件，填写服务地址和凭据，权限设为 0600。在 `config/anp.json` 中保留完整 ports，设置 `services: ["studio", "langfuse-worker", "litellm", "dbx", "business"]`，再将 `external_resources_file` 指向该 JSON 的绝对路径。依赖会展开为十个平台服务。已有数据库、角色和 Supabase Auth/Storage schema 需预先准备；运行器不创建云数据库或角色，启动会执行本实例的官方应用迁移。

使用独立 namespace。Langfuse、LiteLLM 在配置的既有专用数据库内使用独立 PostgreSQL schema，Redis 使用带 hash tag 的独立前缀，Langfuse S3 对象与 Supabase Storage tenant 隔离。ClickHouse database 需明确指定本实例的数据库。资源文件不要进入 Git、CDN、镜像或发行包。

S3 请求校验默认沿用官方 SDK 的 `WHEN_SUPPORTED`。阿里云 OSS 不支持流式 `STREAMING-UNSIGNED-PAYLOAD-TRAILER` 时，在私有配置设置 `s3.request_checksum_calculation: "WHEN_REQUIRED"`；仅作用于 Storage 和 Langfuse，保留 API 必需的校验、签名及权限检查。依据见 [AWS 数据完整性文档](https://docs.aws.amazon.com/sdkref/latest/guide/feature-dataintegrity.html)。

远程统一入口为 Agent Server 的 58000 端口，只需 SSH 转发此端口，即可访问工作台、Studio、DBX、Langfuse 和 LiteLLM。各上游默认仍监听 loopback，平台后台要求 Supabase 管理员，SDK API 保留自己的鉴权。云模式的对象管理通过 Supabase Storage，未运行的 RustFS 没有本地管理后台。

云模式管理员邮箱为 `<namespace>@anp.local`，密码位于本实例私有 `var/secrets/runtime.json`。已有同邮箱账号不会被自动提升为管理员；迁移既有实例时需确认其受信管理员身份，并保留原实例的私有凭据与管理员标记。不要只复制数据目录后假定账户自动接管。

可选 OTel/LGTM 默认关闭。云模式启用 Grafana 前，需配置其专用 PostgreSQL 数据库及角色，仍不使用 SQLite。完整内置包的五个观测组件另有独立验收。

云适配还包括 Auth/Storage 角色的显式搜索路径、LiteLLM 迁移限定当前 schema、Studio 连接 URI 转义、冻结 certifi 公共根证书，以及 Storage/Langfuse 批量删除的 MD5。固定官方产物的必要适配均记录摘要。

Linux 包自带 ELF 库闭包和小型 NSS 适配库，不修改宿主配置。普通 ELF 使用实例内 RPATH；Slim Node 的临时预载在进入 main 前清除，宿主 shell 子进程不继承。仍使用宿主 hosts/resolv.conf 与用户记录。兼容范围、云配置和实际验收边界见 [版本验收记录](https://github.com/100apps/anp/tree/main/docs/acceptance)。

## 图形控制台

管理员从工作台进入 `/workbench/console/`，查看当前包的服务状态、启停独立平台、在线编辑端口和代理地址，以及按需查看必须手动登录的平台初始化凭据。入口本身依赖的服务需在终端维护；不会接管其他目录的进程。代理覆盖写入 `config/business-overrides.json`，OAuth 配置写入 `config/oauth.json`，后者默认不填写。详细说明见[平台控制台与 OAuth](../docs/development/平台控制台与OAuth.md)。

RustFS 后台入口为 `/rustfs/console/`，可从控制台的 RustFS 存储卡片打开。内部后台端口为 `rustfs-console: 59003`，仅监听回环；对象 API 保持 59002。后台使用现有 Access Key / Secret Key 登录，可在管理员控制台按需查看。


## 原生统一入口（当前源码候选）

原生包已将本机网关并入 LangGraph Agent Server，不再下载或启动 Caddy。默认统一端口为 `ports.business: 58000`，根路径进入工作台；平台入口为 `/supabase`、`/dbx/`、`/langfuse/`、`/litellm/`、`/grafana/`、`/rustfs/console/`。Studio 使用同源 `/project/default` 与 `/_next/`，LiteLLM 使用固定 `/litellm` 根前缀，均无需跳转到内部监听端口。

旧配置同时包含 `ports.gateway` 与 `ports.business` 时，Agent Server 接管旧 gateway 端口，旧 business 端口停用；旧 `gateway` 服务选择保留为展开平台服务的兼容别名。升级正在运行的旧目录时，启动器只回收此目录登记并验证身份的旧网关与旧 Agent Server；不接管外来进程。底层服务仍仅监听 127.0.0.1，控制台入口监听地址通过原有映射配置修改。

Studio 页面、资源、管理 API 及其他平台后台仍要求 Supabase 管理员登录，并检查跨来源写入。`/auth/v1/`、`/rest/v1/`、`/storage/v1/` 保留上游的 JWT、RLS 和对象权限；LiteLLM `/litellm/v1/` 与 Langfuse `/langfuse/api/public/` 保留供应商密钥校验。TUS 仍以流式转发，恢复同源 Location 的协议、完整端口与 `/storage/v1` 前缀，并保留 Studio 必需的 CORS 请求头。

Mac 本机候选验收见 [统一入口验收报告](../docs/acceptance/统一入口-Mac验收-2026-10-25.md)。本轮没有发布新 Release，Linux 未重测。
