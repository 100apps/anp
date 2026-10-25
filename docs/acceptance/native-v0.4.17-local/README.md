# 现场验收与 Mac CI 分发阻碍

本报告区分正在运行的 Linux 云模式、本机自行构建的 Mac 归档，以及 GitHub CI 产物。**新的双架构正式 Release 尚未完成。**

## Linux 测试机：0.4.15 正式附件，云存储模式

从 GitHub Linux 附件核对 SHA256 后全新解压，切换为此前配置的 PostgreSQL、Redis、ClickHouse、OSS 云服务，保留独立验收 namespace；十个平台服务运行正常，六个本地存储端口关闭。没有安装 Docker 或系统依赖。

41 项云功能、Business 24 项、8 项重启/持久化/库闭包、18 项 Mac 经 SSH 隧道 HTTP 检查，以及 Chrome 真实登录控制台、DBX、Studio 页面均通过。普通上传、8 MiB 断点续传与 SHA256 回读、JWT/RLS、DBX/Studio 实际 SQL、LiteLLM 鉴权、Langfuse 异步 trace 读回均为真实操作。运行后再次 `./anp verify`：158946 个文件通过。临时浏览器测试账户已删除，云实例继续运行，旧目录数据保留。

原始公开回执见 [0.4.15 云模式报告](../native-v0.4.15/README.md)。Linux 新一轮 [CI #16](https://github.com/100apps/anp/actions/runs/40221728566/job/121254561654) 的 Debian、Alpine 目录与归档四项均成功，但整个流程因 Mac 测试失败而未发布 0.4.16。

## 本机 Mac：自行构建 0.4.17

源码：`5c46ca652e12c5024a2f7f46ce976daeb6841e60`，工作区干净；实体 Mac 为 macOS 27.0.1、ARM64。归档：`anp-0.4.17-macos-arm64.tar.gz`，2032625977 字节，SHA256：

```text
75fb022be4161ea8309d1bcf2dda369214a08864d1cce43784bb987f2e981be7
```

[独立构建目录](macos-arm64-local-directory.json)与[真实归档解压](macos-arm64-local-archive.json)各通过 26 项主检查，两个命令退出码均为 0。两者分别在全新隔离目录、随机空闲端口执行，禁止外网、Homebrew 和源码树依赖；原有用户实例未覆盖，仍为 19/19 服务就绪；两个验收副本最终停止并清理。此前 [本机 0.4.16 归档](../native-v0.4.16-local/README.md) 也通过完整验收，不能混用版本或摘要。

检查覆盖 19 个服务、Business 24 项、单入口鉴权、跨来源与普通成员管理限制、Auth/CRUD/RPC/RLS、普通与 8 MiB 断点续传、DBX/Studio 实际查询、LiteLLM 后台登录、Langfuse 异步读回、含空格路径搬移、重启持久化及运行后完整性。

## 已修复与已提交

- Mac Redis 保留官方二进制字节及 Redis Labs 签名，通过进程内启动器使用包内 OpenSSL；不修改系统安全设置。
- Redis 延迟启动测试副本保留新 `bin/`、`lib/` 布局；完整回归 150 项及前端 TypeScript/Vite 构建通过。
- 云模式控制台显示外部对象存储，隐藏不适用的本地端口和 RustFS 后台链接。该修正已进入本机 0.4.17 与源码；正在运行的远程 0.4.15 未替换其不可变文件。
- 自动发布改为候选预发布，目标实体机复验后再转正式；README、AGENTS 增加签名与跨主机证据边界。Mac runner 更新为官方 macOS 26 基线；该升级本身没有解决 DBX 问题。

## Mac CI 产物仍未通过

新 [独立 DBX 对照构建](https://github.com/100apps/anp/actions/runs/40225090360) 在 macOS 26.6.2、SDK 26.5 上成功，但下载到本机并核对 GitHub ZIP 摘要及二进制摘要后，`--help` 仍退出 **SIGKILL -9**，没有标准输出或错误输出。严格代码签名校验通过。见 [最小对照回执](dbx26-host-help.json)。旧 SDK 的 CI 产物也失败，本机自行编译的同源 DBX 能执行。

升级 SDK、重新 ad-hoc 签名和正常符号剥离均未解决；没有修改 Gatekeeper、隔离标记或终端防护。不能据此确定唯一根因，也不能用本机自建通过替代 CI 分发通过。

[候选 #17](https://github.com/100apps/anp/actions/runs/40227323694) 已主动取消，未发布正式附件。继续分发信任验证需要团队的 Developer ID Application 签名与公证配置；本机现有 Apple Development 身份不作为分发身份使用。尚未获得该配置，没有读取、导出或提交签名私钥。

## 未验证范围

真实付费模型供应商、外部 OAuth、Intel Mac、Realtime、Pyroscope、eBPF 不在本轮范围；业务模型与 trace 为离线/合成验收数据。底层服务仅监听 loopback，测试机通过 SSH 隧道访问。凭据、云地址、桶名、namespace 和服务器地址不随公开报告提交。
