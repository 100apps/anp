> 历史进展记录：以下 PoC 路径中的同名产物可能已被后续构建覆盖，不代表当前 Release。最新发布和本机兼容性结论见 [0.3.6 验收报告](native-release-2026-10-23.md)。

# ANP 0.3.0 原生发行验收进展

日期：2026-10-23。Mac ARM64 完整目录与真实压缩包均通过本机验收，Linux x64 完整目录与真实压缩包通过两个干净系统验收。代码已推送 master，远程发布流水线运行中；发布状态以文末实际结果为准。

## 范围

本轮包括 Mac ARM64 和 Linux x64。Intel Mac 暂不发布；Realtime 暂不加入原生包。新增 DBX Web 和可单独复制的 `business/`，保留已有 Supabase、Langfuse、LiteLLM 及其存储依赖。

## 已取得的证据

| 检查 | 当前结果 | 证据 |
| --- | --- | --- |
| 项目回归与前端构建 | 本次 88 项通过，TypeScript/Vite 构建通过 | `var/native/tests-ci-fix.log` |
| 原生发行专项回归 | 22 项通过 | `var/native/native-tests-latest.log` |
| Business 单元及代理契约 | 28 项通过 | `var/native/business-tests.log` |
| 包内 Python 3.13 + 真实 Supabase | 22 项通过 | `var/native/evidence/business-python313-live.json` |
| 复制 Business，禁止读取原业务源码和 Homebrew 后联调 | 24 项通过 | `var/native/evidence/business-copied-sandbox.json` |
| 浏览器登录、Workflow、Deep Agent、退出 | 1920 和 390 两种宽度通过，无横向溢出 | `var/native/evidence/business-browser.json`、同目录截图 |
| Mac DBX Web 源码编译、密码登录和真实 PostgreSQL 查询 | 构建输出通过，查询返回 42 | `var/native/evidence/dbx-macos-native.json` |
| Mac 完整目录组装及动态库检查 | 444 个 Mach-O，无实际包外动态库依赖；完整目录 22 项及 Business 24 项通过 | `var/native/evidence/macos/macho-audit.json`、`macos-arm64-directory.json` |
| ClickHouse 搬移后的独立查询 | 干净 Debian、Alpine 均返回 42 | `var/native/evidence/clickhouse-relocation-probe.json` |
| ClickHouse 本地查询的库映射 | 两个干净 OS 均无包外执行库，查询返回 42 | `var/native/evidence/clickhouse-transitive-rpath.json` |
| ClickHouse server 与符号解析查询 | 两个干净 OS 均返回 42；Debian 有可选的只读加载器符号映射，Alpine 无此文件也通过 | `var/native/evidence/clickhouse-server-library-mappings.json` |
| Linux 首次失败记录（历史） | 曾定位 Langfuse 内存限制，修复后的完整结果见下方；此记录保留用于解释修改原因 | `var/native/evidence/linux-platform-before-memory-fix.json` |
| Linux 完整包 Debian 目录验收 | 全部通过，含搬移、重启、追踪与持久化 | `var/native/evidence/linux/debian-directory.json` |
| Alpine 原生插件独立验证 | Meta glibc profiler、Prisma 引擎加载通过；自动检测确实误选 musl | `var/native/evidence/alpine-native-bindings.json` |
| Langfuse NAPI 加载器独立验证 | 原加载器在 Alpine 失败，优先实际 Node glibc 信息后加载成功 | `var/native/evidence/alpine-langfuse-native.json` |
| Linux 完整包 Alpine 目录验收 | 23 项通过，Business 24 项通过，搬移持久化通过 | `var/native/evidence/linux/alpine-directory.json` |
| Linux 完整平台目录构建及来源清单 | 已构建，与四份最终回执的文件清单摘要一致 | `var/native/evidence/linux-platform-manifest/` |
| Linux Debian 真实压缩包验收 | 23 项通过，Business 24 项通过 | `var/native/evidence/linux/debian-archive.json` |
| Linux Alpine 真实压缩包验收 | 23 项通过，Business 24 项通过 | `var/native/evidence/linux/alpine-archive.json` |
| Linux 最终汇总与本机归档校验 | 两个系统、目录和压缩包共四份回执通过，归档 SHA256 一致 | `var/native/evidence/linux-final-acceptance.json`、`var/native/evidence/linux-archive-local-transfer.json` |
| Linux 基础包 Debian 干净容器 | 12 项通过 | `var/native/evidence/linux-poc-baseline/debian-directory.json` |
| Linux 基础包 Alpine 干净容器 | 12 项通过 | `var/native/evidence/linux-poc-baseline/alpine-directory.json` |

两个业务 Demo 默认使用明确标注的离线演示模型，Deep Agent 确实执行资料工具；上述结果不代表外部模型供应商调用已验收。

## 已修改及原因

- **独立 Business 配置**：服务地址默认 localhost，配置只位于业务目录或显式指定路径。业务不读取父项目配置；原生启动器在实例 `var/` 生成运行配置。
- **认证与权限**：Supabase 验证会话，HttpOnly Cookie 保存登录；只信任管理员维护的 `app_metadata`。LangGraph 按用户隔离线程和存储命名空间，平台代理限制管理员访问。
- **HTTP 扩展**：通过内置 Agent Server 提供业务接口和界面，没有增加独立业务 HTTP 服务器。
- **平台入口**：DBX 和 Langfuse 使用子路径代理；官方 Studio 和 LiteLLM 管理台使用独立地址，避免其根路径资源在子路径下损坏。LiteLLM 的模型 API 保留鉴权代理。
- **DBX 来源**：Linux 使用固定 URL 和 SHA256 的官方 Web 发行包。Mac 官方未提供对应 Web 服务产物，使用固定真实 submodule 提交构建；不以桌面 DMG 冒充 Web 服务。
- **DBX Linux 归档结构**：官方 `dbx-web` 是 Bash 启动器，真正的嵌入前端服务位于 `bin/dbx-web-bin`。安装后者并统一命名为 `dbx-web`，避免依赖 Bash 或遗漏相对路径。
- **Linux 运行库**：收集 ELF 动态库闭包，保留来源与校验信息。搬移后的加载器路径写入实例缓存，不修改原始发行文件。Node/Python 自派生进程也纳入检查。
- **ClickHouse 自重启**：官方程序会清空 `LD_LIBRARY_PATH` 并重新执行自身，导致只依赖该变量时回退到宿主库。将既有 RUNPATH 原位改为可传递的包内相对 RPATH，保持 ELF 段布局；不修改上游的安全检查。修复已在无外网 Debian 和 Alpine 中分别查询返回 42。依据：[锁定版本官方源码](https://github.com/ClickHouse/ClickHouse/blob/v26.9.22.3-stable/src/Common/EnvironmentChecks.cpp)。
- **动态库验收与符号数据**：单独记录 ClickHouse 对旧加载器文件的 `r--p` 只读符号映射，不能把它算作执行宿主代码。只有该精确进程、加载器文件、只读权限且已看到包内加载器执行段时允许；包外执行段、可写映射和其他库仍报错。服务模式已在 Alpine（无该 glibc 文件）启动、查询及符号解析通过。依据：[官方 SymbolIndex 实现](https://github.com/ClickHouse/ClickHouse/blob/v26.9.22.3-stable/src/Common/SymbolIndex.cpp)。
- **ELF 修补**：发现同时修改加载器和 RPATH 会破坏部分程序；改为只更新既有 PT_INTERP 的文件位置，保持 PT_LOAD 和程序代码地址不变，并用包内库闭包运行。
- **npm 原生预编译件**：精简其他架构和 musl 文件，记录删除路径与摘要，保留目标 glibc x64 文件和运行元数据。
- **Supabase Node 启动器**：保留官方 Node 和 execPath 预载，统一由 ANP 的包内加载器执行，避免在 shell 包装文件上再次调用 ELF 加载器。
- **ClickHouse 内存上限**：Linux 实测 RSS 约 3 GiB，超过最初 1 GiB 服务限制，使 Langfuse 追踪 API 返回 422。将服务上限改为 4 GiB，保留默认单查询 512 MiB；上限不是预留内存；修复后两个系统的目录和真实压缩包均完成追踪写入、worker 处理及读回验收。低内存机器尚未验证。
- **宿主 libc 与包内运行时**：Alpine 的 `ldd` 属于 musl，不能用它决定包内 glibc Node 应加载哪个插件。Meta 通过官方 `SENTRY_PROFILER_BINARY_PATH` 指定与自身 Node ABI 匹配的文件；Prisma 显式使用随包 glibc 查询和迁移引擎；仅对已定位的 `@langfuse/native`、`@node-rs/xxhash` 生成加载器加入运行中 Node glibc 信息的优先判断，并记录修改前后摘要。没有全局覆盖其他 NAPI 包。
- **最小 OS 依赖**：PostgreSQL 官方 Bash 包装脚本改为 POSIX shell；Linux 运行使用可用的 C locale；进程管理读取 `/proc`，无需安装 lsof/ps。测试容器只提供生成的测试用户记录，不挂载宿主账号文件。
- **Prisma 安装脚本产物**：另行锁定官方引擎地址、官方校验地址和解压后二进制 SHA256，组装前验证所有匹配的引擎副本。Linux 7 份、Mac 7 份原始文件均与官方校验值一致；Mac 这一结果仅代表字节校验，不代表本机原生执行已通过。
- **构建链路**：`make build` 根据本机系统选择目标，`make dist` 只打包不可变清单；不包含配置、运行数据、秘密或派生 ELF 缓存。可用 `ANP_VERSION` 指定发行版本。
- **业务健康检查**：使用扩展提供的 `/api/health`；框架自带 `/health` 需要登录，不能用作匿名的进程就绪检查。
- **生命周期配置**：Business 的运行配置单独放在 `var/run/config/`，避免与 PID 记录同名；初始化失败输出脱敏日志；健康检查对暂未就绪的数据库进行重试。

## Linux 交付产物与验收边界

本机压缩包：`var/exports/anp-0.3.0-poc-linux-amd64.tar.gz`，大小 **1,704,897,626 字节**；同目录有 `.sha256` 文件。

```text
归档 SHA256：7e975985665d0eb18c02619dfc07d5da244b550990ba07552f1fb63b9763ed61
文件清单 SHA256：286f3d628e32542128664325b772bc3fef5c1335194df221e423d3248fccf9d8
```

四份最终回执均对应 `0.3.0-poc` 和上述同一文件清单。在 ssh-ecs 的本项目独立 Docker 环境完成实际构建，再分别在干净 Debian trixie-slim、Alpine 3.22 中验证完整目录及真实归档。验收使用普通用户、关闭外网、移除 capabilities；容器没有宿主 Python、Node、uv、编译器，也不提供原平台及业务源码。检查包括：

- 15 项服务、17 个端口、启动及停止、冲突端口保护、包内动态库及自派生进程。
- Supabase 登录、真实 Data API CRUD/RPC/RLS，Storage 上传下载、签名地址和用户隔离。
- Studio 数据查询及页面、LiteLLM 密钥鉴权、DBX 密码登录及真实 PostgreSQL 查询返回 42。
- Business 会话和权限、Workflow、Deep Agent 工具调用，以及管理员平台代理。
- Langfuse 登录、页面资源、OTLP 追踪写入、worker 消费和查询读回。
- 含中文和空格的目录搬移、重启后数据库/文件/追踪/向量及秘密保持、验收数据删除、不可变文件复核。

Langfuse 输入使用本地合成追踪，验证的是真实接收、队列、存储和读回链路；Demo 使用离线模型，没有验收外部模型供应商。两个干净系统通过不代表任意 Linux 发行版、内核或低内存机器均获保证。

## 环境与清理

`make context` 已执行。`make doctor` 检出本机 Docker daemon 未启动，因此没有记为通过；所需本机构建工具均存在。Linux 构建及验收使用 ssh-ecs 的 Docker，本机原生运行不依赖 Docker。

仅停止本轮四个临时 Mac 测试进程，测试端口已释放；原有本机实例的 Auth 健康检查返回 200。证据：`var/native/evidence/local-test-process-cleanup.json`。远程验收容器已退出并移除，构建镜像及可复现输入保留。

## 本次继续排查（2026-10-23）

- 新复制 DBX 连续执行及既有候选包的离线沙箱执行已恢复成功，本轮未改系统安全策略。此前 AMFI 拒绝的具体触发原因仍未证实，不能以本次成功反推此前原因。
- 重新构建发现 Mach-O 审计误报：`otool -L` 混合显示库自身 `LC_ID_DYLIB` 和 fat 架构标题，旧解析把它们当作包外依赖。改为读取实际 `LC_LOAD_DYLIB`、弱链接、重导出、上行及延迟加载命令；保留真实包外依赖拒绝。回归先复现 1 失败、4 通过，修复后 5 通过。
- 重新组装后的 Mac 完整目录与真实压缩包均通过 22 项检查，Business 各 24 项通过；15 服务就绪，搬移、重启和持久化通过。两份回执的文件清单摘要相同，实际压缩包 SHA256 已核对。本次没有关闭 Gatekeeper、AMFI 或其他系统策略。

## Mac 交付产物

本机归档：`var/exports/anp-0.3.0-poc-macos-arm64.tar.gz`，**1,798,475,753 字节**。

```text
归档 SHA256：d9f582d656c9339f246a0ab3a8168456c8986fecd78008bd7dec7582484c06cf
文件清单 SHA256：377d9f0776dd55d9bc180407306eab512446c7a03d9b2647f79acf23353f6ef8
```

本地两平台回执已保存到 [native-0.3.0-local/](native-0.3.0-local/)。Mac 为本机沙箱验收，不等同于其他实体 Mac 或 Apple 公证验收。此前的 AMFI 失败记录保留在 `var/native/evidence/dbx-development-signing-probe.json`；本次未复现，具体触发原因没有得到证实，不能把它和已修复的 Mach-O 审计误报混为同一原因。

## 远程发布状态

提交 `03b078cf5bf3f64ce00d9aab85ac5afe2499a1d6` 已推送 `master`，触发 [Actions 39885663463](https://github.com/100apps/anp/actions/runs/39885663463)，本次版本 `0.3.1`。Mac 和 Linux 作业都已进入全新构建，尚未记为通过；Release 仍待双平台目录、归档验收及资产摘要核对。

首轮 Linux 全新构建在安装 ClickHouse 时失败：官方归档的 `usr/share/clickhouse` 目录与 `usr/bin/clickhouse` 文件同名，旧代码取遍历结果的第一项，产生 `IsADirectoryError`。真实归档补验还发现同名 shell 补全文件；最终按 ELF/Mach-O 文件头选择唯一原生文件，排除目录、符号链接和补全脚本，多个真实二进制则明确拒绝。文件系统回归覆盖这三类同名项及歧义，ssh-ecs 的官方归档实测选中 `usr/bin/clickhouse`；本机使用该选择流程的两个官方 Mac 原生组件（RustFS、ClickHouse）也通过检查。Mac 首轮在 DBX 工具安装失败：`native/packages/dbx-tools/package-lock.json` 包含内网 npm 归档地址，GitHub runner 连接超时。改为 npm 官方 URL，固定 pnpm 10.34.7 与原有 SHA512；已实际下载官方归档并确认完整性相同，再在空 npm 缓存、禁用用户 npm 配置的临时目录安装成功。已逐个检查全部已提交锁文件，没有发现其他内网下载地址；新增原生 npm 锁回归，防止内网地址重新进入发行链路。

最新提交 `d2b8ed5138142253450ea7ef58d918ccf3d94a80` 已触发 [第四轮流水线 39889128231](https://github.com/100apps/anp/actions/runs/39889128231)，版本 `0.3.4`。第三轮由同分支并发控制取消，旧失败版本不发布。第四轮 Linux 已全部通过：全新构建、两个干净系统的目录及归档各 23 项检查、Business 各 24 项和 Actions 产物上传均成功；四份回执文件清单一致。Mac 和最终 Release 仍待完成。

本机 Mac 和 ssh-ecs 干净 Linux 的完整归档验收门禁已通过。工作流成功、Release 公开及服务端资产摘要核对前，不将远程发行记为完成。

远程仅使用本轮 `anp-native-*` 容器和 `/data0/anp-native/` 工作目录，没有停止或清理其他项目的容器及数据。

### 第四轮 Mac 结果与修复

第四轮 Mac 源码构建成功；随后回归的两项 Compose 配置检查因 runner 没有 Docker CLI 而失败（86 通过、2 失败），因此没有进入 Mac 目录、归档或 Release。修复为直接校验源配置，不读取私有 .env；无 Docker PATH 下四项配置检查通过，完整回归 88 项通过。独立临时空配置也经本机 Compose 解析通过。

DBX 的完整 LTO 在第四轮耗时较长，改用 ThinLTO、2 个编译作业与 8 个 codegen units。本机全量源码构建已成功，耗时约 17 分 42 秒；这只是本机构建证据，不能替代新一轮 CI 和实际功能验收。增加按 SDK、源码及锁文件隔离的组件缓存，在功能测试前保存。新一轮结果待实际运行后补充。
