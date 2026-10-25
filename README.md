# ANP

ANP 把基础工具、存储、平台和可复制的 Business 业务层打包为可搬移目录。解压后运行 `./anp start`，不依赖宿主 Python、Node、Homebrew 或 Docker。

支持 **Mac ARM64、Linux x64**；Windows 使用 WSL 运行 Linux 包，Business 可以在 Windows 原生目录开发。Intel Mac、Realtime、Pyroscope、eBPF 暂不发布。

## 下载与启动

从 [GitHub Releases](https://github.com/100apps/anp/releases) 下载对应系统的压缩包和 `.sha256`，核对摘要后解压。**0.4.15 已转为预发布：Linux 目标主机云模式通过，但 Mac 正式附件未通过实体机复验，暂不推荐该 Mac 附件。** 后续修复以对应版本报告为准。

启动命令：

```sh
cd anp-macos-arm64        # Linux 为 anp-linux-amd64
./anp verify
./anp start
./anp status
./anp stop
```

默认入口为 [http://127.0.0.1:58000/workbench/](http://127.0.0.1:58000/workbench/)。启动器会输出实际地址、PID、端口、健康状态及日志路径。默认全部监听 `127.0.0.1`；只有显式修改控制台的入口监听设置后才开放局域网。

首次启动生成独立数据和随机凭据：`var/secrets/runtime.json`（权限 0600）。业务管理员为 `admin@anp.local`，密码字段为 `business-user-password`。不要提交或传播该文件。Langfuse、LiteLLM、RustFS 仍需要自己的登录时，控制台会注明，登录管理员可查看对应凭据。

## 单入口与功能

业务和平台代理统一放在 **LangGraph 内置 Agent Server** 中，不再单独运行 Caddy。平台后台要求管理员身份；Supabase JWT/RLS、S3 签名和模型/观测 SDK 的密钥校验继续保留。

| 入口 | 用途 |
| --- | --- |
| `/workbench/` | Workflow、Deep Agent 两个业务示例 |
| `/workbench/console/` | 服务状态、启停、运行配置、代理映射、OAuth 与登录凭据 |
| `/supabase` | 同源 Supabase Studio，Auth、Data API、Storage |
| `/dbx/` | 数据库管理；统一登录 |
| `/langfuse/` | Trace 与观测平台 |
| `/litellm/` | 模型网关及后台 |
| `/rustfs/console/` | 对象存储管理后台 |
| `/grafana/` | 可选的日志、指标与调用链入口 |

OTel/LGTM 包含 Collector、Grafana、Prometheus、Loki、Tempo，默认不启动；Grafana 元数据使用 PostgreSQL。旧配置的 `gateway` 端口自动交给 Agent Server，避免改变已有入口地址。业务默认使用离线演示模型，真实供应商调用需要另行配置。

## 目录

源码按职责组织，生成文件只写入被忽略的 `var/`：

```text
anp/
├── business/          # 可独立复制的 uv + shadcn/ui + LangGraph 业务层
├── native/            # 原生构建、发行与运行
│   ├── build.py       # make build / dist 的实现
│   ├── checks/        # 目录、压缩包、权限、业务、上传与干净环境验收
│   ├── runtime/       # 随包的进程管理与配置生成
│   ├── defaults/      # 首次启动配置、OAuth、OTel 模板
│   ├── locks/         # 官方 URL、SHA256、源码和构建镜像锁
│   ├── packages/      # 冻结的 Python / JS 构建依赖
│   └── licenses/      # 随产物保留的许可证
├── platform/sources/  # 仅构建需要的锁定上游 submodule
├── docker/            # 保留的 Docker 交付，独立于原生包
├── config/            # Docker 配置示例，不放秘密
├── scripts/           # 开发、环境诊断与 Docker 命令
├── tests/             # 回归、运行器和构建契约
├── docs/              # 架构、开发、发行和验收证据
└── var/               # 构建缓存、导出包、日志、隔离验收实例；不提交
```

发行包的目录与源码仓库分开：

```text
anp-macos-arm64/        # Linux 为 anp-linux-amd64/
├── anp                # 启动、停止、状态、日志及完整性校验
├── tools/             # uv、Python、Node；Linux 另含 glibc / ELF 工具
├── storage/           # PostgreSQL、Redis、ClickHouse、RustFS
├── platform/          # Supabase、Langfuse、LiteLLM、DBX、可选 OTel/LGTM
├── business/          # 可复制的业务代码、锁文件与前端
├── runtime/           # 包内生命周期管理
├── defaults/          # 只读默认配置
├── config/            # 用户配置
├── manifest/          # 来源、文件摘要与许可证
└── var/               # 首次运行生成；不进入发行压缩包
```

目录可以在停止服务后搬移，数据与配置随目录保存。不要移动正在运行的实例，也不要用构建覆盖包含运行数据的目录。

## 使用云存储层

在包外准备权限为 0600 的私有资源 JSON，填写 PostgreSQL、Redis、ClickHouse、S3 和独立 namespace。在 `config/anp.json` 保留完整 ports，设置 `external_resources_file` 为该文件绝对路径，并将 `services` 设为 `["studio", "langfuse-worker", "litellm", "dbx", "business"]`。会展开十个平台服务，四个本地存储服务不会启动；数据库、角色及 Supabase schema 须提前准备。

OSS 设置 `s3.request_checksum_calculation: "WHEN_REQUIRED"`，支持普通上传和断点续传；默认 `WHEN_SUPPORTED` 保持官方 SDK 行为。云对象仍通过 Supabase Storage 管理。远程只需转发 Agent Server 的 58000 端口，平台后台保留鉴权。示例及约束见 [外部资源配置](native/README.md#外部资源模式)。

## 从源码构建

构建需要对应架构的原生主机；Linux 在独立 Debian 构建容器中组装：

```sh
git clone --branch main https://github.com/100apps/anp.git
cd anp
make context
make doctor
make test
make build            # 按本机架构取锁定产物和构建缺少的组件
make native-check     # 从隔离副本启动真实服务并验收
make dist             # var/exports/ 下生成压缩包及 SHA256
# 真实解压包复验
python3 native/check.py --archive var/exports/anp-<版本>-macos-arm64.tar.gz
# Linux 使用 python3 native/linux_check.py --archive <Linux压缩包>
```

优先使用固定版本的官方二进制或代码包；没有适用发行物才从锁定源码构建，不从第三方 Docker 镜像提取运行文件。Python 使用 `uv --frozen`，前端使用 `pnpm --frozen-lockfile`。

仅开发业务时不需要编译平台，直接复制 `business/` 并配置依赖地址，见 [Business 使用说明](business/README.md)。常规开发的回归命令为 `make test`。Docker 交付见 [Docker 开发环境](docs/development/Docker开发环境.md)。

## 配置与发行依据

- [原生配置与组件来源](native/README.md)
- [Linux 构建与干净环境](docs/development/linux-native-build.md)
- [平台控制台、代理与 OAuth](docs/development/平台控制台与OAuth.md)
- [OTel/LGTM](docs/development/OTel-LGTM使用说明.md)
- [Release 流水线](docs/development/原生发行流水线.md)
- [文档与验收索引](docs/README.md)

推送 `main` 触发带版本的原生 Release。发布必须通过 Mac 的目录与真实解压包验收，以及 Linux 在禁用外网的 Debian、Alpine 容器中的目录与解压包验收，再核对 GitHub 服务端资产 SHA256。源码候选、本机覆盖目录、CI 构建与正式附件的结果分别记录，不能互相替代。

[0.4.15 验收报告与回执](docs/acceptance/native-v0.4.15/README.md)分别记录六份 CI 验收、Mac 正式附件失败与本机构建通过的区别，以及 Linux 测试机云模式结果。Mac 包未做 Apple notarization；Linux 已覆盖 Debian、Alpine 与目标测试机，不能据此宣称兼容任意 Linux。真实模型供应商和外部 OAuth 提供方不在本轮验收范围。

本机新构建归档的独立结果见 [0.4.16 Mac 验收](docs/acceptance/native-v0.4.16-local/README.md)：26 项主检查已通过，但不能替代 GitHub CI 附件的跨主机验收。
