# 0.4.15 历史验收与 Mac 签名问题

**本版本为预发布，Mac 正式附件未通过实体机复验。请使用后续修复版本。** 六份 CI 回执通过不等于目标实体机通过；以下结果按产物和环境分开记录。

源码：`d7cab7a67264290f9c7d5a2c88ba0b5d3643b269`。[CI #15](https://github.com/100apps/anp/actions/runs/40211839806) 成功；[Release](https://github.com/100apps/anp/releases/tag/native-v0.4.15) 已注明已知问题，资产未覆盖。

| 产物与环境 | 结果 | 证据 |
| --- | --- | --- |
| Mac ARM64 CI 目录与归档 | 通过 | macos-arm64-directory.json、macos-arm64-archive.json |
| Linux Debian / Alpine 目录与归档 | 四项通过 | debian-*.json、alpine-*.json |
| 本机自行构建的 0.4.15 归档 | 通过，不能替代 CI 附件 | local-macos-build-archive.json |
| GitHub 正式 Mac 归档在实体 Mac | **失败：Redis SIGKILL -9** | mac-official-archive.json |
| 目标 Linux 主机全新解压、复用云存储 | 41 项通过，另含 Business 24 项 | cloud-functional.json |
| 云数据、文件、trace 重启持久化及库闭包 | 8 项通过 | cloud-restart.json |
| Mac 经 SSH 隧道访问云控制台、后台和真实 SQL | 18 项通过 | mac-tunnel.json |
| Mac Chrome 真实登录控制台、DBX 与 Studio 页面 | 通过，临时用户已清理 | cloud-browser.json |

## 问题与修复方向

原构建为了重定位 OpenSSL，用 install_name_tool 修改 Redis Mach-O，破坏了 Redis Labs Developer ID 签名。重新改为 ad-hoc 签名后，CI 和本机自建归档能够运行，但该正式附件在实体 Mac 上被系统终止。

修复提交 `a57b4106c69251ce21374b3b75a72d512f664346` 保留官方 ZIP 中二进制的原字节和供应商签名，由只作用于 Redis 的启动器指定包内 OpenSSL。审计要求 Apple 证书链、固定 Redis Labs Team ID、二进制与启动器摘要匹配；不放宽其他组件的库检查。

独立 PoC 已在禁止 Homebrew 和外网的沙箱中完成 Redis 服务与 CLI PING、实际包内库加载、含空格路径搬移后重启；见 redis-vendor-poc.json。PoC 不是后续正式附件验收。未修改系统安全设置、隔离标记或终端防护。

进一步诊断：仅替换为保留官方签名 Redis 的 CI 归档副本，已越过 Redis，但随后 CI 构建的 DBX 在实体 Mac 上出现 SIGKILL -9；同一源码的本机构建可执行。该副本明确标为诊断产物，不作为正式版本通过证据。DBX 的最终原因与修复仍待验证。

## 云模式范围

使用已有 PostgreSQL、Redis、ClickHouse、OSS 的独立 namespace。新实例只运行十个平台服务，六个本地存储端口全部关闭；Agent Server 为统一入口。普通上传、8 MiB 断点续传及 SHA256 回读、JWT/RLS 双用户隔离、DBX/Studio SELECT 42、LiteLLM 密钥和数据库登录、Langfuse 异步 worker 云端读回均为真实操作。OSS 配置显式 WHEN_REQUIRED 请求校验，保留必须的校验和签名。

业务 Demo 使用离线模型，trace 为合成验收数据，没有调用付费模型供应商。云模式未启用可选 OTel/LGTM；其完整内置包启动范围由对应 CI 回执记录。未验证外部 OAuth 供应商、Realtime、Intel Mac、Pyroscope 或 eBPF。凭据、云地址、桶名、namespace 和服务器地址不随公开报告提交。
