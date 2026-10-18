# DBX 内部导出目录迁移修复

状态：**当前导出目录身份故障已修复；浏览器真实下载及隔离 SQL 恢复通过。旧 Chrome 客户端拦截的具体来源仍未确认。** 不把两个不同故障合并归因，也不把探针表导出称为完整业务备份。

## 本次确定的原因

2026-10-19，登录最终本地 DBX 后导出 `public.anp_v3_storage_probe`，在服务端生成阶段失败：`/state/dbx/tmp` 的文件系统与上次记录不同。固定 DBX 上游 `c511c90a7cb5165b7847d5a46dc55f0050e7272b` 中，Linux 导出目录身份使用 `metadata.dev()`，保存于 `state_store` 的 `database_export_destination:/state/dbx/tmp`。本机实际保存值为设备号 **2080**、版本 2，当前实际挂载设备号为 **2112**。

已检查 Docker 实际挂载：`anp_v3_smoke_platform_state` 是可写命名卷，目的地 `/opt/anp/roots/dbx/state`；DBX 以组件根运行，其内部 `/state/dbx/tmp` 对应该卷中的 `dbx/tmp`。状态库、目录与挂载根的设备号一致，无符号链接替代。内核设备号与持久化状态不一致足以解释本次拒绝；没有证据精确指出旧设备号在哪次虚拟机或挂载生命周期变化，也不能把它当作之前 Chrome `ERR_BLOCKED_BY_CLIENT` 的原因。

## 受限的显式目录重认

新本机管理模块 `anp.management.dbx_export_identity` 分两步执行。它只服务于平台管理员持有 Docker 权限的本机维护，不提供业务 HTTP 旁路，不改数据库用户权限或浏览器保护。

```powershell
$env:ANP_ROOT = 'E:/anp'
uv run --no-sync python -m anp.management.dbx_export_identity inspect `
  --volume anp_v3_smoke_platform_state --plan .local/dbx-identity-plan.json

# 检查计划后，显式应用同一份计划。
uv run --no-sync python -m anp.management.dbx_export_identity apply `
  --volume anp_v3_smoke_platform_state --plan .local/dbx-identity-plan.json
```

`inspect` 不更改身份。`apply` 要求容器 ID、镜像 ID、实际命名卷及目标旧值/版本仍与计划一致；拒绝 bind、只读/错误/重复挂载、符号链接、其他文件系统、未知身份格式、未知目标表触发器和并发修改。只允许固定内部临时目录，不接受任意备份目标。先使用 SQLite 在线备份并完整性检查，再以事务及旧值/版本比较更新这一条记录；其他状态行摘要保持。用户指定的备份盘身份保护、认证和一次性下载行为均不变。

原状态库备份 SHA-256 为 `7b4c3729abe11c87faf0d61eb3285da4e2aad7c55cc6f4bd05f89e66877ac09e`。容器命名卷和宿主机 `.local/backups/dbx-export-identity-20261018T224913Z` 均保留原始备份与回执。独立复制恢复并重新打开 SQLite 完整性检查通过；逐表比较 24 张表，只有 `state_store` 改变，其他表一致，目标身份版本从 2 变为 3。

首次使用冻结 SDK 命令时未设置 `ANP_ROOT`，宿主机备份落在该制品的 SDK 目录内。原备份完好，已复制到工作区正式备份目录并核对摘要，旧回执和原文件均保留。最终命令增加真实工作区前置检查，错误根目录在进入 Docker 前即拒绝；此补丁已重新构建、Compose 重建，未以运行目录临时修改代替制品。

## 实际验收

- 最终制品 `20261018224951-3dca9667` 重新构建并由 Compose 启动；11 组件 ready。新命令与冻结 SDK 逐字节一致。
- 11 项维护命令边界测试，以及 9 项真实平台/业务检查，共 **20 项通过，23.70 秒**。错误容器的旧计划也在实际冻结命令中被拒绝；最终重新检查身份为 2112/2112，不需要再次写入。
- 在内置浏览器通过 DBX 原有菜单导出 `public.anp_v3_storage_probe`，收到真实 download 完成事件，保存至 **`C:/Users/guangfeng/Downloads/postgres.sql`**。页面完成 1 个对象、19 行、280ms；文件实际 2132 字节，SHA-256 为 `c8ec9df02b5338d9c1fb3dfe7819f96b4a0857db0a719a83271f75ff4c3534b2`。
- 将实际 SQL 恢复到本地 V3 PostgreSQL 的隔离临时数据库，19 行与下载内容及来源对应行完全一致；验证后仅清理本次随机命名的临时库，没有在原库执行导入。
- 旧 462 条线程的全部值、来源准备完整父子中断检查点以及 27 个受保护容器 ID 保留；业务回归后 464 条线程。源稿、配音和原待审运行未放行。

[下载完成界面](evidence/dbx-browser-export-20261019.jpg) 与 [公开验证摘要](evidence/dbx-export-identity-20261019.json)。原完整业务库备份、云数据库、deep_dub 正文审核和整片交付仍按主清单继续。

## 尚未证明

此次证明当前内置浏览器能保存真实文件，提供了可用浏览器下载路径；没有关闭 Chrome 扩展、下载保护或安全策略，也没有定位旧 Chrome `ERR_BLOCKED_BY_CLIENT` 的具体拦截组件。复验 Chrome 时仍须重新创建导出任务，因为 DBX 下载端点消费一次即删除临时文件，不能复用已下载 ID。
