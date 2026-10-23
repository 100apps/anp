# ANP 0.3.6 原生发行验收报告

日期：2026-10-23。结论：**Mac ARM64、Linux x64 的 CI 与正式 Release 已跑通；当前本机 macOS 26.5.1 的新解压服务仍存在被系统终止的问题（正式包为 Redis，此前 DBX 也复现），本机整包验收不能宣布通过。**

## 发布与下载

- [正式 Release：native-v0.3.6](https://github.com/100apps/anp/releases/tag/native-v0.3.6)，非草稿、非预发布；2026-10-23 22:14:31（上海）公开。
- [Mac ARM64 下载](https://github.com/100apps/anp/releases/download/native-v0.3.6/anp-0.3.6-macos-arm64.tar.gz) · [SHA256 文件](https://github.com/100apps/anp/releases/download/native-v0.3.6/anp-0.3.6-macos-arm64.tar.gz.sha256)。
- [Linux x64 下载](https://github.com/100apps/anp/releases/download/native-v0.3.6/anp-0.3.6-linux-amd64.tar.gz) · [SHA256 文件](https://github.com/100apps/anp/releases/download/native-v0.3.6/anp-0.3.6-linux-amd64.tar.gz.sha256)。
- [GitHub Actions 实际成功运行](https://github.com/100apps/anp/actions/runs/39961444941)：version、macos、linux、release 四个作业均成功。
- 源码提交：`3b244dbe508865b4dd0df3b21dc37b534fc7d45d`。Release ID：`421924268`。
- [发布资产元数据快照](native-0.3.6-release-metadata.json)和 [Actions 状态快照](native-0.3.6-actions.json)。

| 产物 | 字节数 | SHA256 |
| --- | ---: | --- |
| macos-arm64 | 1,808,362,966 | `75559dd10a2853033b0d88fc78e35deaf256d1ee16fa0e04bc96c5ab711ef836` |
| linux-amd64 | 1,705,604,690 | `009e87384eebd1ee70183fef6703ce9c02045d7dcd05bc488060e49d889c9d83` |

发布脚本在公开前核对十个附件的文件名集合、大小及服务端摘要。六份正式验收 JSON 与两个校验文件已另行下载，逐一比对大小与 SHA256，八份均一致；见[下载核验记录](native-0.3.6/download-verification.json)。本地快照保留正式附件原始字节，没有改写回执。

## 远程真实验收结果

| 环境 | 目录 | 实际压缩包解压 | Business |
| --- | --- | --- | --- |
| GitHub macos-15 原生 ARM64 | 22 项通过 | 22 项通过 | 两次各 24 项通过 |
| 干净 Debian x64 容器 | 23 项通过 | 23 项通过 | 两次各 24 项通过 |
| 干净 Alpine x64 容器 | 23 项通过 | 23 项通过 | 两次各 24 项通过 |

正式附件回执：

- Mac：[目录](native-0.3.6/macos-arm64-directory.json)、[压缩包](native-0.3.6/macos-arm64-archive.json)。
- Debian：[目录](native-0.3.6/debian-directory.json)、[压缩包](native-0.3.6/debian-archive.json)。
- Alpine：[目录](native-0.3.6/alpine-directory.json)、[压缩包](native-0.3.6/alpine-archive.json)。

六份回执版本均为 `0.3.6`，`passed=true`；归档回执中的 SHA256 与对应正式包一致。验收脚本 SHA256 均为 `5ba6ea0f767ca9ca1f27aab2f83ffb6658bd12aaff251cfe2619d4eece9cd954`。完整回归为 **94 项通过**，TypeScript 检查和 Vite 生产构建通过；Business 24 项计数由[已完成作业日志摘录](native-0.3.6/business-log-excerpts.json)确认，正式平台回执另记录业务检查通过。

Mac 验收通过沙箱阻止外网、Homebrew、平台和业务源码读取，在中文与空格目录中运行、搬移并重启。Linux 在固定 Debian 容器中构建；运行验收分别使用锁定摘要的 Debian、Alpine，`network=none`、普通用户 `1001:1001`，不挂载宿主平台或业务源码，并检查实际进程加载的动态库。完整镜像摘要与检查字段见原始回执。Linux 当前只覆盖这两个运行环境，不能据此声称任意 Linux 均已验证。

真实功能覆盖：

- Supabase 注册、密码登录、用户读取及管理员列表；Data API CRUD/RPC、跨用户 RLS；Storage 上传、下载、签名地址与隔离。
- Studio 数据库实际查询和页面；LiteLLM 模型 API 鉴权。
- DBX 经业务管理员代理访问、密码登录、内嵌前端资源、连接独立 PostgreSQL 并查询返回 42。
- Business 会话、管理员边界、跨用户线程隔离、Workflow 与 Deep Agent 工具调用。
- Langfuse 管理员登录、页面资源、OTLP 接收、异步 worker 消费、持久化及查询读回。
- 端口冲突保护、loopback 和附属端口、停止、搬移重启、数据库/文件/追踪/向量/秘密持久化、测试数据删除、运行后不可变文件清单。

## 本轮修改及原因

| 问题 | 修改 | 验证证据 |
| --- | --- | --- |
| Mach-O 审计将库自身标识、fat 架构标题误当依赖 | 按实际加载命令分析，仍拒绝真实包外依赖 | 回归先复现后通过；本机完整包审计 444 个 Mach-O、无包外依赖 |
| ClickHouse 归档的目录、补全脚本与二进制同名 | 按 ELF/Mach-O 文件头选择唯一真实可执行文件，拒绝歧义 | 官方归档实测、回归及 Linux 全新构建通过 |
| DBX 工具锁含内网 npm 地址 | 使用固定官方 npm 归档并保留 SHA512 | 空缓存、无用户 npm 配置安装通过，来源回归通过 |
| Mac runner 无 Docker CLI，静态 Compose 检查失败 | 显式锁定 PyYAML，直接校验源配置 | 无 Docker PATH 下测试通过，完整 CI 94 项通过 |
| DBX 完整 LTO 链接过慢 | Cargo 两个作业、ThinLTO、8 个 codegen units，参数写入构建回执 | 本机源码构建约 17 分 42 秒；CI 冷构建 46 分 46 秒，上轮为 89 分 43 秒，非严格性能基准 |
| 重试反复编译大型组件 | 缓存绑定源码、锁文件、编译器、SDK 与路径，验收前保存 | 本轮日志实际命中并恢复缓存，平台与归档功能验收仍重新执行 |
| 0.3.5 草稿创建后按尚未公开标签查 Release 返回 404 | 从认证 Release 列表定位标签，按 Release ID 上传、校验及发布；支持同源码草稿补齐，拒绝覆盖不一致已公开资产 | 执行真实工作流脚本的六项回归先复现 404 后全绿；0.3.6 正式发布成功 |

DBX 0.6.51 官方 Web 产物覆盖 Linux、Windows，Mac 附件为桌面应用；[固定版本资产](https://github.com/t8y2/dbx/releases/tag/v0.6.51)和[锁定源码发行矩阵](https://github.com/t8y2/dbx/blob/46b773d05e3162170a5358805588ce1fa60f3306/.github/workflows/release.yml)没有 Mac Web 目标，因此 Mac HTTP 服务使用锁定源码构建。不能用桌面 DMG 替代。组件继续优先固定官方二进制与代码包，没有从第三方镜像提取运行文件。

0.3.5 的旧草稿未公开，保留失败现场；对外下载使用 0.3.6。运行数据、密钥和本机用户配置不进入发行包。

## 本机 Mac 兼容性问题

本机为 macOS 26.5.1、ARM64。重新组装的 ThinLTO 目录验收 22 项、Business 24 项通过；本机实际归档解压后启动 DBX 返回 `-9`（SIGKILL）。这是本机 `0.3.0-poc` 复验包，SHA256 `93c2ac149b41694485a68b3c1a75a4fa9b89d3e61d6807de1d93771efd79ea43`，不是正式 0.3.6。

另行下载 CI 0.3.5 候选包并核对完整 SHA256 后，新解压 DBX 同样返回 `-9`，静态签名校验通过。最小归档、系统 tar、独立 inode、APFS clone、重新 ad-hoc 签名及两个 Rust 版本的小程序对照均复现过。没有证据把根因直接归为 ThinLTO、单一 Rust 版本或 Python 解压器。AMFI 日志出现信任检查，但类似日志也出现在可运行程序上，因此尚不能据此断定最终根因。

最终 **0.3.6 正式公开包**已完整下载，字节数与 SHA256 均匹配上表。对该包执行 `python3 native/check.py --archive var/exports/release-0.3.6/anp-0.3.6-macos-arm64.tar.gz`，145.2 秒后验收失败：工具、不可变清单与外来端口检查通过，PostgreSQL 就绪，Redis 进程随后以 `-9`（SIGKILL）退出。启动器完成本次 Redis/PostgreSQL 回滚，已有实例保留。见[本机执行结果](native-0.3.6/local-final-archive.json)和[原始失败回执](native-0.3.6/local-macos-26-archive.json)。

对正式包单独提取 Redis 与 DBX，脱离验收沙箱执行 `--version` / `--help`，两个文件的静态签名检查均返回 0，实际执行均返回 `-9`；见[最小执行对照](native-0.3.6/local-execution-probes.json)。这说明失败不只发生在完整验收沙箱内。

该现象已不局限于 DBX。当前证据不足以确认系统终止的根因，也不能将正式包在本机标为可用；本机验收门禁保持未通过。

没有关闭 Gatekeeper、移除隔离属性、修改端点防护或加入信任规则。Mac 未做 Apple notarization；当前只有 Apple Development 身份，没有 Developer ID 发行身份。普通 Terminal 启动对照因自动化工具禁止控制该应用未执行，没有绕过限制。CI Mac 15 的成功不能替代本机 Mac 26 的失败结果，也不能替代其他实体 Mac 和组织安全策略验收。

## 使用与复现

Mac 下载压缩包和相邻 `.sha256` 文件后：

```sh
shasum -a 256 -c anp-0.3.6-macos-arm64.tar.gz.sha256
tar -xzf anp-0.3.6-macos-arm64.tar.gz
cd anp-macos-arm64
./anp verify
./anp start
./anp status
./anp stop
```

本机存在上述 Redis/DBX 执行限制，以上命令说明运行入口，不表示当前本机已全通过。默认工作台 `http://127.0.0.1:58000/workbench/`。凭据由首次启动生成，位于包内私有 `var/secrets/runtime.json`，不要提交或传播。

源码验收在 `master`：`make build` → `make native-check` → `make dist` → `python3 native/check.py --archive <实际归档路径>`；Linux 使用 `native/linux_check.py` 在干净容器中执行。推送源码至 master 自动生成带版本 Release，仅根 README 或 docs 修改不重复触发构建。发布版本号默认取 `native/VERSION` 的主次版本与 GitHub run number。

## 未验证范围

两个业务 Demo 使用离线演示模型，真实供应商调用未验证。Langfuse 追踪输入是合成的，但接收、队列、存储和读回操作均为真实服务。Intel Mac 与 Realtime 暂不包含；Apple notarization、其他实体 Mac、更多 Linux 发行版未验证。

历史 PoC 验收记录见 [历史进展](native-0.3.0-progress.md)，其中同名本地产物可能已被后续构建覆盖；当前正式包与结论以本报告的版本、摘要及原始回执为准。
