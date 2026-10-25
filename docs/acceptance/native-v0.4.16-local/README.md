# Mac ARM64 本机 0.4.16 归档验收

本报告记录 **本机自行构建的压缩包**，不是 GitHub Release 附件。实体 Mac 的系统版本为 27.0.1、架构 arm64。构建源码为 `a57b4106c69251ce21374b3b75a72d512f664346`，归档 SHA256 为：

```text
160b3e8ad160efc7cab2df0fe6f6c58cc327c5eb41e51ca0858d7df7fc5b725e
```

执行 `python3 native/check.py --archive var/exports/anp-0.4.16-macos-arm64.tar.gz`，退出码 0；[完整回执](macos-arm64-local-archive.json) 为 `passed: true`，26 项主检查通过。归档解压到全新隔离目录，使用随机空闲端口，禁止外网、Homebrew 和源码目录依赖。没有覆盖已有实例的数据或配置。

## 覆盖范围

- 全部 19 个服务启动，包括可选 Collector、Grafana、Prometheus、Loki、Tempo；底层进程全部监听 loopback。
- Business 24 项检查；Supabase 真实注册、登录、管理员权限、CRUD/RPC 和双用户 RLS 隔离。
- 普通上传、8 MiB 断点续传及 SHA256 回读、签名下载和删除；DBX/Studio 真实数据库查询。
- Agent Server 单入口；匿名、普通成员与跨来源管理写入被拒绝；LiteLLM 机器密钥与后台数据库登录。
- Langfuse 管理登录、静态资源鉴权、合成 OTLP trace 经异步 worker 实际读回。
- 停止、搬移到另一含空格目录、重启，数据库、文件、trace、Redis 向量与随机凭据保持；运行后不可变文件清单再次通过。

验收副本最终停止并删除；原有用户运行实例保留。没有调用真实付费模型供应商，没有验证外部 OAuth 提供方。

## 与 CI 附件的区别

0.4.15 的 GitHub Mac 附件仍未通过该实体 Mac 复验，见 [历史问题报告](../native-v0.4.15/README.md)。Redis 官方签名保留方案已通过本机完整包验收，但 CI DBX 的跨主机问题尚未解决。

[DBX 最小对照回执](dbx-host-comparison.json) 对两个原生二进制执行 `--help`，使用独立空数据根：CI 产物退出 -9，本机构建退出 0；两者均通过 `codesign --verify --strict`。因此仅凭严格签名校验或本机自建成功，不能宣布 CI 下载产物可用。当前独立 [macOS 26 SDK 诊断构建](https://github.com/100apps/anp/actions/runs/40225090360) 不发布 ANP，结果待跨主机复验。

Linux 正式 0.4.15 附件在目标测试机的云模式已通过功能、重启持久化和 Mac 经 SSH 隧道访问验收，见 [云模式报告](../native-v0.4.15/README.md)。不能用本报告宣称 0.4.16 双架构 Release 已发布。
