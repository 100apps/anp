# ANP 协作约定

## 交流和范围

- 使用中文交流和 Markdown 文档；产品名称统一为 ANP。
- 先确认工作区、分支、未提交修改与正在运行的实例；不得覆盖用户修改或操作相邻项目的服务、数据、Docker 卷和配置。
- 数据只写入本工作区 `var/` 或用户显式配置的独立数据根。不要因目录改名迁移已有数据根；不得覆盖包含运行数据的构建目录。
- 不读取或输出 `.env`、`credentials.json` 的实际值。必要的真实验收在服务端读取本实例私有凭据，禁止把值写入日志、快照、源码、文档、压缩包或提交。
- shell 命令遵守用户的全局 RTK 约定。不要将秘密放在命令参数或完整环境输出中。

## 入口和目录

1. 开始执行 `make context`、`make doctor`，阅读 README 与 `docs/architecture/架构与Agent指南.md`。
2. 当前主交付是 Mac ARM64、Linux x64 原生目录包：`native/build.py` 负责构建和打包，`native/runtime/` 随包运行，`native/checks/` 负责真实验收。
3. 保持 `make build / native-check / dist` 与 `native/check.py / linux_check.py` 入口稳定；组件默认配置、版本和来源分别放在 `native/defaults/`、`native/VERSION`、`native/locks/`。
4. `business/` 必须可以单独复制，只靠自己的配置和锁文件开发。开发业务不需要初始化整套平台 submodule。
5. Docker 交付继续保留在 `docker/`，配置示例放 `config/`，开发与 Docker CLI 放 `scripts/`。不得把 Docker 环境启动作为原生包的隐式依赖。
6. 构建缓存、临时下载、日志和运行实例只放被忽略的 `var/`；删除可重建缓存前确认其不含运行数据。历史验收回执保留版本与环境，不伪装成当前通过结果。

## 实现原则

- Python 用 uv、前端用 pnpm，冻结安装并提交锁文件；前端使用 shadcn/ui。
- 优先固定官方二进制或代码包，每个下载必须锁定 URL 与 SHA256。缺少原生产物才从真实 submodule 提交构建，不从第三方服务镜像抽取文件。
- 保留有效的官方代码签名；修改 Mach-O 或库路径前先验证签名影响。自行 ad-hoc 重签不能作为跨主机分发通过的证据，禁止通过修改系统安全设置解决分发问题。
- 原生发行仅支持 `macos-arm64`、`linux-amd64`。Docker 的 `linux/amd64`、`linux/arm64` 多架构锁与原生范围分开，不混淆。
- 业务 HTTP 与平台代理统一放在 LangGraph 内置 Agent Server 的扩展中，不另起业务服务器或独立原生网关。
- 平台后台与管理 API 校验 Supabase 管理员身份，管理写入和 WebSocket 检查来源；Auth/Data/Storage 与 SDK API 保留各自 JWT、RLS、签名或机器密钥，不绕过上游验证。
- 底层服务默认只监听 127.0.0.1；只有 Agent Server 可按显式入口配置开放。保留外来进程、PID 重用、端口冲突与本目录进程身份检查。
- 业务对象通过 Supabase Data API/RPC，文件通过 Storage；业务不直连 PostgreSQL 或 RustFS。迁移、初始化和验收可以使用底层连接。Realtime 当前不随原生包发布，不能声明已接入。
- 页面采用全宽、自适应布局，无居中的固定最大宽度；检查宽屏与移动端，无横向溢出。

## 测试与发布

- 常规修改执行 `make test`；运行器、代理、鉴权、上传、构建或依赖改动还需启动真实服务验证，不能只看 HTTP 200。
- 原生发行必须分别验证干净构建目录和真实压缩包，覆盖首次初始化、身份权限、数据库/RLS、上传回读、重启持久化及含空格路径搬移。
- Mac 在目标 ARM64 主机/runner 的离线沙箱验收；Linux 在固定 Debian、Alpine、无外网、无开发工具的容器中验收。不要用现有运行目录冒充全新解压验收。
- Mac CI 归档还须下载到目标实体 Mac 复验；本机自行编译归档和 CI 下载归档必须分别记录版本与 SHA256，不得互相替代。
- 修改 Docker 生成输入时，先 `make sources`、`python scripts/validate_sources.py`，再运行 `python scripts/generate_dockerfile.py` 并提交生成结果；原生修改不要求启动 Docker。
- 发布以 `.github/workflows/native-release.yml` 的实际结果、六份回执、压缩包摘要、GitHub 资产摘要和公开 Release 状态为准。草稿或作业启动不代表完成。
- 明确区分离线演示、真实数据库操作、真实模型供应商、单机验证、CI 和下载附件；未执行的检查不能写通过。
- 完成说明包含变更、测试范围、未验证边界与证据路径。不得只因耗时长或接近结束就宣称完成。
