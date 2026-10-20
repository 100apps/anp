# GitHub 新版构建

仓库为 `100apps/anp`，默认分支 `main`。当前代码替换旧版工作树，Git 历史保留，可按历史提交查阅旧实现。旧版单体平台打包、自动追踪上游最新版本、旧工作台与磁盘探针流水线不再保留。

## 持续集成

`.github/workflows/ci.yaml` 在推送 `main`、PR 和手动执行时运行：

1. 检出固定 submodule、使用 uv/Python 3.12 和 pnpm 11.25.0 的锁定依赖。
2. 执行业务、代理、Compose 测试和前端生产编译，核对生成后的平台配方无漂移。
3. 分别构建业务 `demo` 和 `production` 镜像。演示镜像真实启动四存储，执行读写及两个图的 HTTP 流式冒烟。
4. 默认分支成功完成对应作业后发布到 GHCR：`ghcr.io/100apps/anp2-business-demo`、`ghcr.io/100apps/anp2-business-production`，标记为 `sha-完整提交SHA` 和 `main`。PR 只构建测试，不发布。

数据库的两个专用 RLS/幂等测试需要显式的私有测试数据库配置，普通测试作业会报告跳过。四存储与容器 demo 由独立作业真实启动验证，不将跳过的测试写成通过。CI 不复用本机密钥，也不调用付费翻译/音频模型。生产镜像只验证构建，启动仍需要正式 Agent Server 授权与数据库配置。

## 独立平台镜像

在 Actions 选择“新版平台源码镜像”，手动选择九个服务中的一个，或选择 `all`。默认发布，也可关闭发布进行构建验证。每个服务单独镜像、单独缓存，最多同时构建两个；不再组装包含所有平台的单体镜像。

发布地址为 `ghcr.io/100apps/anp2-组件名:sha-完整提交SHA`。目录前缀与本机 Compose 一致，包括 Langfuse 的构建参数。依赖来自 gitlink 固定提交，构建不会执行更新上游分支的操作。源码及配方清单上传为 Actions 制品。

平台生产编译较耗资源。磁盘准备脚本仅允许在 GitHub 官方临时托管运行器执行，清理其中固定的预装 SDK 目录，绝不会在本机或共享自托管运行器清理目录。平台流水线验证镜像构建；完整平台启动与浏览器操作验收另见本地平台回执。

## 使用构建产物

在根 `.env` 显式设置相应镜像变量，例如 `BUSINESS_IMAGE=ghcr.io/100apps/anp2-business-demo:sha-提交SHA`、`DBX_IMAGE=ghcr.io/100apps/anp2-dbx:sha-提交SHA`，然后启动对应服务。生产业务镜像使用 `BUSINESS_PRODUCTION_IMAGE`。私有 GHCR 包需要先登录具有读取权限的账号。

GitHub 中原有发布镜像、Secrets 和其他设置不自动删除；新版流水线只使用本仓库自动提供的 `GITHUB_TOKEN` 登录 GHCR，不再调用旧版部署脚本。本地 `E:/anp` 的服务、数据和配置不参与 GitHub 替换过程。
