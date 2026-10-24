# ANP Linux 目标主机两种存储模式验收

日期：2026-10-23 至 2026-10-24。目标：用户指定的 Alibaba Cloud Linux x64 主机。

## 当前结论

| 模式 | 当前结果 |
| --- | --- |
| 正式发布的原始 0.3.6 完整包 | 文件完整性通过，但在该主机 PostgreSQL 初始化失败；不能记为原包通过 |
| 完整存储修复候选 0.3.6-linux-hostfix3 | 独立副本实机 23/23 项通过，包含搬移、重启、持久化和运行后完整性；实例数据保留，切换云模式后已停止 |
| 不启动存储、连接 DBX 配置中的云资源 | 最终候选 0.3.6-cloud-hostfix4：32/32 项云功能、8/8 项重启/持久化/运行库审计通过，145,340 文件完整性通过 |

本次没有发布新的 GitHub Release，修复候选不能冒充正式 0.3.6 资产。云端仅为本次独立 schema、ClickHouse database 和唯一命名的验收 fixture 执行初始化；没有新建 PostgreSQL 数据库或角色，没有升级已有 Langfuse/LiteLLM 的 public 表。

## 环境与原始包

目标环境为 Alibaba Cloud Linux 3 x86_64，内核 `5.10.112-005.ali5000.al8.x86_64`；普通用户 UID/GID 2686；cgroup 内存上限 8 GiB、无 swap。开始时磁盘约 32 GiB 可用。已有 Java、业务服务和 Tengine 保留，只管理 `<验证目录>/` 下本次创建的实例。

目标机不能直接下载 GitHub Release，因此从本机经 SSH 转传。实际正式包：

- 文件：`anp-0.3.6-linux-amd64.tar.gz`
- 大小：1,705,604,690 字节
- SHA256：`009e87384eebd1ee70183fef6703ce9c02045d7dcd05bc488060e49d889c9d83`
- 来源：[正式 Release](https://github.com/100apps/anp/releases/tag/native-v0.3.6)
- 目标机完整摘要核对通过；`./anp verify` 的 145,028 文件通过。
- 原包解压目录：`<验证目录>/original/anp-linux-amd64`。

随后 `./anp start` 在 initdb 阶段失败：全局 `LD_LIBRARY_PATH` 使宿主旧加载器读取包内新 libc，报 `GLIBC_2.35 not found`。不能把先前 Debian/Alpine CI 的结果写成这台 Alibaba Cloud Linux 主机通过。

## 修复内容与证据

1. Linux 执行缓存先用锁定官方静态 patchelf 写入传递性 RPATH，再原位修改 INTERP；不继承全局 `LD_LIBRARY_PATH`。原始 ELF 保持不变，搬移后重建缓存。最小对照先复现旧错误，修复后 Node 自派生和宿主 shell 同时通过，PostgreSQL 初始化通过，继而全部 15 个服务就绪。
2. LiteLLM 原先模型 API 正常，但真实浏览器登录报 `Not connected to DB`。补充独立数据库、冻结 Prisma Python 生成客户端、CLI 5.17.0 和官方固定引擎；使用包内 `prisma` 入口执行官方迁移，避免依赖宿主 Python 模块或启动时下载安装。修复后 `/v2/login` 返回 200 与会话 token，浏览器进入管理员 Home。
3. Prisma 引擎使用内部动态 HTTP 端口。验收仅允许确切包内引擎、受管 LiteLLM 进程组且绑定 loopback 的一个端口，并写入回执；其他未知端口仍失败。错误路径、其他进程组的单元回归均不能被放行。
4. 增加显式 Linux 实机验收模式，回执标记 `runtime_scope=linux-host`、`external_network_isolation_enforced=false`；不假称这台已有业务主机是无外网干净环境。
5. 外部资源入口使用 0600 配置文件，显式关闭四个本地存储依赖；配置独立 namespace、Redis cluster/hash tag、受管 PostgreSQL 角色和 Langfuse/LiteLLM 独立 schema。此项只有配置与边界回归通过，云端整链路尚未验证。
6. Langfuse OTLP 接收成功但读回返回 422，worker 写入也报 ClickHouse 内存超限。实测 ClickHouse RSS 约 661 MiB，却把共享 cgroup 的 4.16 GiB 算进自身 4 GiB 限额。配置 `memory_worker_use_cgroup=0`，按自身分配器统计，保留 4 GiB 上限；同一追踪探针从 422 变为 200，并读回 worker 处理的 observation。[官方设置说明](https://clickhouse.com/docs/reference/settings/server-settings/settings/memory-worker)解释了 cgroup 与 jemalloc 的统计选择。

Prisma 引擎来自 `binaries.prisma.sh` 固定提交 `393aa359c9ad4a4bb28630fb5613f9c281cde053`，压缩字节摘要已比对官方 checksum；CLI 来自官方 npm，版本和完整性由 package-lock 固定。patchelf 来自官方 0.19.2 静态发行，SHA256 `2abdc34fbe949c995a1baa4ec310896a085588a45a99cd0d726851bdfac2a4bb`。没有从第三方服务镜像提取运行文件。

候选是由正式包派生、重新冻结清单的修复目录，并非本轮重新全量编译 Langfuse。目录：`<验证目录>/fixed/anp-linux-amd64`。原始发布包与原始解压目录保留。

## 已完成的本机访问验收

本机通过 SSH 的 loopback 隧道访问远程服务，没有开放额外公网监听：

```sh
ssh -N \
  -L 127.0.0.1:58000:127.0.0.1:58000 \
  -L 127.0.0.1:53200:127.0.0.1:53200 \
  -L 127.0.0.1:54000:127.0.0.1:54000 \
  admin@<验证主机>
```

Studio 与 LiteLLM 管理台跳转到配置的独立地址，只有网关隧道不足以打开这两个后台。

可通过以下本机地址手动访问（需保持上述隧道；目前对应云模式实例）：

| 后台 | 本机地址 |
| --- | --- |
| Business | <http://127.0.0.1:58000/workbench/> |
| DBX（经 Business 管理员权限） | <http://127.0.0.1:58000/dbx/> |
| Supabase Studio | <http://127.0.0.1:53200/project/default> |
| Langfuse | <http://127.0.0.1:58000/langfuse/> |
| LiteLLM | <http://127.0.0.1:54000/ui/> |

- Business 24 项真实 HTTP 验收：Supabase 用户、登录、管理员边界、跨用户线程隔离、Workflow、Deep Agent。
- DBX 经业务代理登录、内嵌资源加载、独立 PostgreSQL 查询返回 42。
- 浏览器实际注册、登录、管理员导航；Workflow 完成 normalize/summarize/finish；Deep Agent 工具调用后生成报告。
- 浏览器 DBX 密码登录成功；Supabase Studio 项目页面加载；Langfuse 管理员登录并进入项目仪表盘；LiteLLM 管理员进入 Home。
- 临时浏览器测试账户与其两个线程已删除；没有在截图、报告或 Git 中放入密码或 token。

截图与 JSON 证据位于本项目 `var/native/remote-validation-036/`。两个 Demo 均是离线演示模型，不代表真实供应商调用。

独立副本最终回执 `host-acceptance-hostfix3.json`：`passed=true`，23 项检查全部为 true，`runtime_scope=linux-host`，不声明外网隔离。145,463 个不可变文件在运行前后校验通过，所有可执行动态库映射位于包内；内部 Prisma 端口也纳入受管监听审计。回执的文件清单 SHA256 为 `2a95d988f3c33fd00200e8cd9a58e4a8003f2b5012a35bec8395da3008ffb0fe`。

手动实例原数据已恢复到修复目录，启动结果 `restored-hostfix3-start.json` 确认 15/15 服务就绪。本机 SSH 隧道再次访问 Business、Studio、Langfuse、LiteLLM 均为 200；DBX 未登录访问为预期 401，真实管理员代理登录与数据库查询另有通过回执。恢复后浏览器 Langfuse Tracing 页面显示 `anp-host-memory-probe` 记录，总数 1，截图 `linux-langfuse-trace-restored.png`。这是本次合成追踪探针，不代表真实供应商请求。

## 云模式的当前证据

本机 DBX 连接已通过只读接口确认：

- PostgreSQL：PolarDB 17.10.7.1；账号不是 superuser，没有建库、建角色权限；已有 `agentloop_supabase`、`agentloop_langfuse`、`agentloop_litellm` 等专用库，并具有相应受管角色成员资格。
- `agentloop_supabase` 已有 auth/storage schema，Auth 迁移末版本 `20260831180000` 与本包一致；已有 pgcrypto、uuid-ossp。
- ClickHouse：26.3.28.1；已有 `agentloop_langfuse` 库。
- Redis：`cluster_enabled=1`，外部模式须使用集群配置。
- 三个数据库端点从目标主机 TCP 均可达；这不等于平台整链路已验证。

用户已确认复用 OSS 的 elemedatasync 桶并使用独立前缀，同时要求重新发起钥匙串授权。第二次正常授权成功，程序在内存中解密三个已保存连接，只导出 0600 私有资源文件；主密钥没有写入文件或输出。PostgreSQL、Redis、ClickHouse 的 TLS 模式保持 DBX 保存的 `ssl=false` 配置；OSS 保持 HTTPS 和证书校验。

独立 namespace 为 `anp-cloud-036-20261024`，PostgreSQL schema 为 `anp_cloud_036_20261024`、`anp_cloud_036_20261024_litellm`，ClickHouse database 为 `anp_cloud_036_20261024`。Langfuse S3 前缀为该 namespace 下的 `langfuse/`，Supabase 使用对应独立 tenant；预检只在该前缀写入、读回、删除自己的探针对象。

目标实例目录 `<验证目录>/cloud/anp-linux-amd64`。从修复完整包派生，只共享不可变文件；运行器文件和修改的 SQL 文件均先断开硬链接，因此不会改变完整模式实例。云模式只启动 11 个平台应用，PostgreSQL、Redis、ClickHouse、RustFS 的本地监听端口均关闭。

本轮真实暴露并修复：

1. PostgreSQL 的 `SET ROLE` 不继承角色默认 GUC，Auth 初次在 public 寻找迁移表并报权限不足。连接参数显式指定 Auth/Storage 搜索路径后成功使用既有 schema。
2. LiteLLM 16 个官方迁移文件的元数据检查没有限定 schema，误读已有 public 表的同名约束。构建步骤将 `pg_constraint` 和 `information_schema.columns` 检查限定当前 schema，记录每个文件修改前后 SHA256；仅重建本次新建、初始化失败的 LiteLLM 测试 schema。完整迁移和真实管理台登录随后通过。
3. Supabase 自带 Node 的根证书集不能验证当前 OSS 链。对照测试原先返回 `UNABLE_TO_GET_ISSUER_CERT_LOCALLY`，加载冻结 certifi 公共根证书后 TLS 验证成功。通过 `NODE_EXTRA_CA_CERTS` 配置补充 CA，未关闭证书校验。
4. 官方 Studio 直接拼接数据库连接 URI，云密码中的保留字符导致连接解析失败。仅对 Studio 的密码环境值做 URI 转义，Meta 继续使用原始密码；Studio 实际查询返回 42。
5. 出网 DNS 触发宿主 NSS myhostname 插件及其动态库。包内增加本项目的 NSS 适配库，只为本包进程选择 glibc 内置 files/DNS；普通 ELF 用实例缓存 NEEDED，官方 Slim Node 使用构造器在 main 前清除的临时预载。实际 DNS 解析、进程 maps 和宿主 shell 子进程均已复验；不修改宿主配置，不放行包外动态库。官方 Slim Node 新增 NEEDED 曾触发崩溃，改用上述有限预载后解决。ClickHouse 与 Redis 的版本入口也通过。
6. OSS 批量删除要求 `Content-MD5`，原 Storage 删除返回 400。四处 S3/TUS 删除调用显式设置 MD5，记录官方文件修改前后摘要；Langfuse 使用其官方 MD5 配置。增加删除后 API 不可访问、OSS 前缀为空检查，避免把上传成功当成完整文件验收。

最终云回执 `cloud-acceptance.json`：32 项全部通过，绑定版本 `0.3.6-cloud-hostfix4` 和验收脚本摘要；`cloud-business.json`：24 项全部通过。覆盖本地存储端口关闭、Business 用户/角色/线程隔离和两个演示、DBX 真实云数据库查询、Data API CRUD/RPC/RLS、Storage 到 OSS 的上传/下载/跨用户隔离/签名下载/删除、OSS 对象存在与删除后前缀为空、Studio 查询、LiteLLM 登录，以及 Langfuse OTLP 经云 Redis、OSS、ClickHouse 的 worker 读回。追踪 ID 为 `f6fb47f6a66044c396364afcd7ca8192`，是合成验收探针；真实供应商调用未验证。临时测试用户、线程、唯一命名的 SQL fixture 和 Storage bucket/object 已清理，保留本实例管理员及独立 Langfuse 项目。

`cloud-restart-acceptance.json` 的 8 项检查通过：11 服务重启、云数据、OSS 文件、追踪持久化、包内执行库、loopback 监听审计，以及验收文件和桶删除。`cloud-runtime-audit.json` 再次确认 11 服务、12 个配置监听与一个受管 Prisma 内部端口；PostgreSQL、Redis、ClickHouse、ClickHouse native、RustFS 的本机端口全部关闭。最终文件清单摘要为 `6b6966c4e258c0227d4821735be9513110a82f996ebd881651fc6573bced473f`，`cloud-final-verify.json` 确认 145,340 文件全部通过。派生副本不包含生成的 pyc 缓存，最终清单也排除了这些缓存项。

本机经 SSH 隧道的 `local-cloud-business.json` 24 项及 `local-cloud-dbx.json` 4 项通过；`local-cloud-platforms.json` 另有 6 项通过，包含 Studio/LiteLLM 页面及脚本、Studio 真实云查询、LiteLLM 登录、Langfuse 真实 Cookie/CSRF 登录和页面脚本。此前 GUI 截图属于完整存储模式，本轮云模式未重新做浏览器点击交互，不混用截图作云模式 GUI 证据。`cloud-fixture-cleanup.json` 确认按本实例日志识别的 5 个旧验收桶均已清理，Langfuse 前缀保留。

当前本机隧道地址对应云模式。Business 与 Langfuse 的本次管理员邮箱为 `anp-cloud-036-20261024@anp.local`，对应密码分别取本实例私有 `var/secrets/runtime.json` 的 `business-user-password`、`langfuse-user-password`；LiteLLM 用户名 admin，密码字段 `litellm`。不展示密码内容。Studio MCP 的独立只读登录能力未纳入本次验收，不能由管理查询成功推定。

本机私有凭据副本路径为 `<本机私有验收目录>/cloud-runtime-private.json`，权限 0600；仅供本人通过本机编辑器查看。资源配置与密码未进入 Git、CDN 或发行包。

## 回归与限制

本轮 `make test`：100 项通过，TypeScript 检查和 Vite 生产构建通过。没有重新运行 Mac 整包验收，未改变既有 Mac SIGKILL 结论；没有新发布流水线或 Release。干净 Debian/Alpine CI 与本次实机验收是不同环境和边界。

两种模式的目标主机验收结束。原始包、原始解压目录及修复后的完整模式实例保留；当前运行云模式 11 服务，完整模式已停止。凭据保存在对应实例私有 `var/secrets/runtime.json`，不在报告中展示。代码修复和外部资源入口仍是当前工作区修改，尚未提交或发布；本轮没有从源码全量重建 Langfuse，也未重新执行修改后的 Mac 整包或干净容器 CI。

脱敏回执归档见 [linux-host-2026-10-24/](linux-host-2026-10-24/)。运行主机、私有目录与凭据路径使用占位符，不随公开报告提供。
