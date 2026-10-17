# F5 与调试

使用 Python 3.12（项目锁定）和 Node.js 22+。先完成 bootstrap，再在 Qoder CN IDE 或 VS Code 打开 `anp.code-workspace`。推荐扩展在 `.vscode/extensions.json`。

F5 组合调试顺序：

1. `anp:prepare`：重新生成模块注册、Compose 配置，启动 Docker，等待 Auth/Storage，迁移、种子账号及 DBX 连接，准备引擎调试端口。
2. `anp:web`：直接用 Node.js 启动已安装的 Vite，避免 Shell 的别名/包管理器路径差异。
3. API、Worker：debugpy 启动 Uvicorn reload 父进程，`subProcess:true` 跟踪每次重载后的子进程。
4. Agent：附加 Docker 中的 debugpy，工作区映射 `/workspace`；使用纯 Python 调试兼容模式，开发时允许阻塞，避免断点暂停被异步阻塞检测器误判。启动检查只读取容器内部监听状态，不预先连接并占用 debugpy 的客户端连接。
5. Edge：打开 5173，前端 sourcemap 映射到 console 和 apps 下的 `.tsx`。

前端组件修改立即 Fast Refresh，Python 修改触发 API/Worker 自动重启。节点业务代码在 Worker 中热重载；图的节点/边定义修改后重新启动 Agent 调试（重新准备/F5）。为避免上游重载父进程导致远程调试握手阻塞，F5 的容器图调试关闭引擎自身 reload，等待 IDE 附加后再启动图服务。普通非调试运行仍使用官方 reload。依赖安装和新增模块也需要重新准备/F5。不要同时手工启动同端口的 API/Worker。

## 建议断点

| 位置 | 触发方式 |
|---|---|
| `src/anp/server/api.py` health | 请求 http://127.0.0.1:8180/health |
| `apps/demo/services/tasks.py` / 公共 TaskService | Web/HTTP CLI 创建、运行任务 |
| `apps/demo/nodes/analyze.py` | Web/HTTP CLI 运行任务，命中 Worker 调试会话 |
| `src/anp/runtime/remote_node.py` | HTTP 平台运行，命中容器 Agent 图调试会话 |
| `apps/demo/ui/index.tsx` | 工作台创建任务、点选任务 |
| `console/src/main.tsx` | 浏览器刷新 |

本地 CLI 调试使用 launch 中的 `ANP: 本地 CLI 调试`，按需要修改 args；无需启动 API/Worker。单项配置默认隐藏，以完整联调作为 F5 默认入口，可修改 presentation.hidden 来单独显示。

断点暂停时请求暂时不返回属于正常现象；继续后节点仍须在超时时间内完成。运行验收脚本前先停止 IDE 组合调试，脚本会自动临时启动需要的业务服务并清理自己创建的进程。

## 数据保存与备份

Docker 命名卷使用 `anp_v2_` 前缀。`platform down` 保留卷。不要执行 `docker compose down -v`，除非明确要清空本平台数据。

PG 使用逻辑备份/恢复；RustFS、ClickHouse、DBX 和 Agent 状态卷分别备份。仅备份源代码目录无法恢复 Docker 卷；仅拷贝 PG 数据目录也不能覆盖对象文件与追踪数据。保存 `config/secrets.local.env`，否则旧数据对应的 JWT、加密和服务密码无法匹配。

本次重建前的旧版快照保留在 `E:\anp-backups\20261017-214413`，旧容器停止，旧数据卷未删除。新平台不使用旧 Windows PostgreSQL 或 `E:\anp\pgdata`。
