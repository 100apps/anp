# ANP 文档

## 架构

- [架构与 Agent 指南](architecture/架构与Agent指南.md)
- [目录与启动方案](architecture/目录与启动方案.md)
- [平台统一入口](architecture/平台统一入口.md)

- [OTel/LGTM 原生集成](architecture/OTel-LGTM原生集成调研-2026-10-25.md)

## 开发与构建

- [原生发行包](../native/README.md)
- [Linux 原生构建](development/linux-native-build.md)
- [原生发行流水线](development/原生发行流水线.md)

- [配置参考](development/配置参考.md)
- [版本与运行参数](development/版本与运行参数.md)
- [GitHub 构建](development/GitHub构建.md)
- [镜像体积优化](development/镜像体积优化.md)

- [存储与平台组件官方产物调研](development/存储与平台组件产物调研-20261021.md)

## 业务

- [Workflow 与 Deep Agent](business/业务演示.md)

- [平台控制台与 OAuth](development/平台控制台与OAuth.md)
- [OTel/LGTM 使用说明](development/OTel-LGTM使用说明.md)
- [Docker 开发环境](development/Docker开发环境.md)

## 验收

- [0.4.16 本机 Mac 压缩包验收（非 CI 附件）](acceptance/native-v0.4.16-local/README.md)

- [0.4.15 云模式验收与 Mac 附件签名问题（历史）](acceptance/native-v0.4.15/README.md)

- [统一入口 Mac 验收](acceptance/统一入口-Mac验收-2026-10-25.md)
- [平台控制台 Mac 验收](acceptance/平台控制台-Mac验收-2026-10-25.md)
- [Linux 目标主机验收](acceptance/linux-host-2026-10-24.md)
- [0.3.6 历史正式发行](acceptance/native-release-2026-10-23.md)

- [原生 0.3.0 验收与修复报告](acceptance/native-0.3.0-progress.md)

- [组件精简与双架构验收结果](acceptance/组件精简与双架构验收-20261021.md)
- [本地验收历史基线](acceptance/本地验收.md)
- [目录整理与功能移除验收](acceptance/目录整理与功能移除-20261021.md)
- [2026-10-21 启动验收历史报告](acceptance/本机启动与功能验收-20261021.md)

历史回执保存在 acceptance/history/，仅证明对应版本和环境；新运行回执写入被 Git 忽略的 var/evidence/。固定测试输入位于 tests/baselines/。
