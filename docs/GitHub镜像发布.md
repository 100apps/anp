# GitHub 平台镜像构建与发布

仓库为私有的 `100apps/anp`，工作流是 `.github/workflows/platform.yml`。当前仍在云端构建验收，不能把流水线文件存在或部分组件已上传，当作默认平台镜像已经发布。

## 构建过程

1. `dependencies` 串行拉取各 submodule 的最新远端 HEAD，并保存实际 SHA。
2. 清单通过小体积作业输出传递；账号 Artifact 存储额度已满，因此不依赖 Artifact 服务，也不删除其他项目的产物。
3. 十项组件在独立作业中构建，限制并行数为 3；每个镜像标记上游源码 SHA。
4. 最后一个作业拉取这些中间制品，校验其 SHA 与同一清单一致，组装唯一平台镜像。
5. 实启四存储与单平台，在镜像外启动示例 API/Worker，验收管理接口、ACL、真实任务、对象保存和评分。
6. 通过后才推送 `ghcr.io/100apps/anp-platform:sha-<提交>` 和 `latest`。镜像内包含 `components.json` 与 `dependencies.json`，可以在拉取后检查实际来源。

CI 没有私人模型密钥，只跳过最后一项真实上游调用；模型目录、分析 Key 权限和模型拒绝访问仍检查。本机已另行验证 `auto`、`auto:fast` 与实际 Lens 分析，不能把 CI 的跳过写成模型已通过。

## 复用已成功的组件

修复平台代码或发布编排时，可以给工作流填写前次 `component_cache_run`。`scripts/reuse_component.py` 会核实：

- 编号属于本仓库的平台工作流。
- 构建配方与前次源码一致；PostgREST 另检查其专用 Dockerfile。
- 拉取的组件镜像上游 SHA 与本次刚拉取的最新代码一致。

任一条件不符就重新构建，不以旧制品冒充最新代码。这项复用逻辑已实现，尚待云端实际运行验证。

## 已遇到的失败

- 首轮 `39059425337`：依赖更新成功，上传 Artifact 时额度不足；已改成作业输出和镜像内清单。
- 第二轮 `39059610124`：Auth、Storage、Meta 已完成。Studio 运行器收到停止信号，退出 143，当前没有足够证据认定是编译错误或内存不足；本轮仍有其他组件运行。Langfuse Web 后续也以运行器停止信号 143 退出。
- 同轮 PostgREST：Nix 自动识别 submodule `.git` 文件，试图访问容器内不存在的父仓库；已改成 `path:/src#static`，本机修复构建现已成功，二进制与整合平台一致。云端新版仍待构建。
- 同轮 LiteLLM：在最终运行层处理 Prisma 权限时明确报 `No space left on device`，属于已证实的磁盘容量问题。下一轮已有托管运行器磁盘准备步骤；尚未实跑通过，不能声称问题已解决，也不能据此反推其他组件的 143 退出原因。
- 同轮 DBX 最终失败（18:48:07–20:03:32 UTC）；官方检查注释为 `The hosted runner lost communication with the server.`。作业日志返回 404 / log not found，尚无具体 CPU、内存或网络根因证据。不能按“编译时间长”重启活构建，也不能把失联直接写成 OOM。

后续作业会输出实际 CPU、内存、磁盘和 cgroup 限制。GitHub 官方列出的私有仓库标准 Linux 运行器为 2 CPU、8 GB 内存、14 GB 存储，不能按本机配置预估云端构建；具体仍以运行数据为准。[官方运行器规格](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#standard-github-hosted-runners-for-private-repositories)

Next.js 提供 `--experimental-debug-memory-usage` 用于采集构建内存；只有获得相应证据后再决定具体调整，不把退出 143 直接当作 OOM。[官方内存诊断说明](https://nextjs.org/docs/app/guides/memory-usage)

## 本机拉取私有镜像

本机当前 Docker 拉取返回 `unauthorized`；已有 gh 登录缺少 `read:packages`。用户需先完成：

```powershell
gh auth refresh -h github.com -s read:packages
```

随后使用已授权凭据登录 GHCR，不在终端打印或手工复制 Token：

```powershell
gh auth token | docker login ghcr.io --username 100apps --password-stdin
docker pull ghcr.io/100apps/anp-platform:latest
```

只有实际拉取、检查 SHA 并启动验证后，才能把 GHCR 交付项标记为完成。不把私有镜像改成公开来绕过授权。

## 下一轮构建的资源配置（实现，未在云端验收）

`ANP_BUILD_PROFILE=ci` 仅调整 Studio 和 Langfuse Web 的构建堆上限：分别为 3072 MB 和可用内存的 50%。本机默认构建不改。此次调整用于试验性降低内存压力，不能据此宣称已经证实或解决退出 143 的根因。

构建器在 Actions 中每 30 秒记录内存可用量和磁盘剩余量，便于下一次把失败与实际资源数据对应。每个新组件带有构建配方摘要与资源配置标签；后续复用同时核对源码 SHA、实际生成的配方和构建参数。旧组件没有摘要时，继续采用保守的完整配方文件比较。

PostgREST 显式使用路径 flake，并接受其源码声明的签名构建缓存。此前活构建捕获的是增加缓存接受参数前的命令，现已成功结束；其 static 构建步骤耗时 2310.8 秒，不能把这次结果当作签名缓存加速已经验证。源码提交、版本及二进制校验和见 `docs/evidence/v3-runtime-final-20261018.json`。

源码编译和镜像组装前增加磁盘准备：仅在 GitHub 托管 Linux 运行器中清理本构建不用的固定预装 SDK，分别要求至少 28 / 35 GiB 空闲。脚本在本机或自托管运行器拒绝执行；不清理 Docker 数据、工作区或其他项目产物。新轮 `39064673639` 的 Studio、DBX、Auth 均已通过 28 GiB 准备步骤，正在编译；组装步骤尚未执行，不能据此宣称已经解决此前失败或发布成功。

CI 验收已补入动态地址的本地 CLI、HTTP 权限、观测和 Studio 检查。本机完整运行得到 42 passed、9 skipped（62.92 秒）；其中 8 项属于明确归档的 V2 检查，另 1 项需显式开启存储持久化验收。直接 Supabase 写入和私有对象下载包含授权成功对照，以及只读/跨工作空间拒绝；没有用旧平台的通过结果代替新平台。

加入执行器并发契约后，本机对应镜像完整检查为 43 passed、9 skipped（78.01 秒）；后续 HTTP SDK 优化后的 Docker 和原生模式均为 48 项通过。新轮 `39064673639` 已开始，采用该优化的干净提交 `84922e5`；旧轮 `39059610124` 最终失败，成功的 Auth、Storage、Meta、Langfuse Worker、Lens 仅为中间制品。最终发布与本机 GHCR 拉取仍未通过。
