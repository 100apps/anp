# 新增一个业务

```powershell
uv run anp app new research
uv run anp migrate
uv run anp platform up
```

`app new` 复制 demo 并替换目录、包名、schema、接口、CLI 和前端引用。`app sync` 会生成菜单注册和每业务 Agent 配置。新增/停用模块后重新按 F5，让 API 加载新的路由与引擎；开发期间已有页面和节点修改会热更新。

## 逐层修改

1. `apps/research/app.toml`：改中文名称、描述、版本、菜单，保留 ID 与目录一致。
2. `services/tasks.py`：三个入口共用的业务入口。示例复用 `anp.tasks.service.TaskService`；复杂业务可以替换为自定义服务，只需保留 create/list/get/run/cancel/result 契约。
3. `nodes/`：写实际业务逻辑并在 `nodes/__init__.py` 注册。函数签名为 `async def analyze(state, context)`，返回 JSON 可序列化对象；`context.objects` 保留身份，`context.task` 给出任务信息，`context.operation_id` 可用于外部 API 幂等。
4. `flow/state.py`：声明流状态；`flow/graph.py` 定义节点和边。节点逻辑不写入引擎宿主。
5. `flow/deployment.py`：保持 `build_remote_graph('research')` 适配，平台节点会调用 Worker；本地 CLI 使用同一图的本机适配。
6. `api/router.py`：共用 task_router，也可扩展业务接口。只做传参和 HTTP 映射，不重复写业务逻辑。
7. `ui/index.tsx`：工作台业务页面，可复用 `@console/components/ui/*` 的 shadcn/ui 组件。
8. `migrations/`：新增业务表、索引、RLS。对象大文件保存到 Supabase Storage，实际存储在 RustFS。

示例节点：

```python
async def analyze(state, context):
    text = state['normalized']['text']
    return {'character_count': len(text), 'preview': text[:160]}
```

图中通过工厂注入执行方式：

```python
builder.add_node('analyze', node_factory('analyze', 'analysis'))
builder.add_edge('normalize', 'analyze')
builder.add_edge('analyze', 'save')
```

本地：工厂直接调用 nodes；平台：工厂 HTTP 调用 Worker。`State` 中的 `analysis` 接收此节点返回值。修改入参结构时同步修改 services 的 Pydantic 模型与 UI 表单；CLI 的 `--input` 读取 JSON，公共 CLI 目前仍采用示例任务契约，独立的新命令可以在 CLI 注册器扩展。

## 部署与访问

```powershell
uv run anp research tasks create --text "新业务测试"
uv run anp research tasks run <任务ID>
uv run anp --transport http research tasks run <任务ID>
```

新增业务自动成为工作台菜单，不需要复制后台外壳。MVP 各业务共享一个本机 API 和一个 Worker 宿主，每个业务拥有单独 Agent 容器与状态卷。业务间 Python 依赖共用 uv 环境；需要冲突依赖或强隔离时再拆独立镜像/Worker。

Agent 端口按有效模块的排序从 8124 分配，调试端口从 5678 分配。新增模块后运行 `app sync` 并重启平台，防止旧容器保留旧端口。需要固定外部地址时，在 `[agent]` 配置 `<app_id>_url`。

F5 默认附加 demo 的引擎。新业务图的容器内调试可复制 launch 中“Agent 图调试”，将端口改成该模块的调试端口；API、Worker 和 UI 断点无需新配置即可调试所有业务。所有业务节点在共享 Worker 中执行，可直接在 `apps/research/nodes/` 设断点。

日志字段、规则评分器及网关日志配置见 [日志与评分](observability.md)。
